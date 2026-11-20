"""正式恢复（RC2-R04）：staging 库 + staging 文件验证 → 原子切换 → 切换后校验 → 失败自动回退旧库与旧文件。

用法（由 05-恢复备份 -Production 调用，调用前必须已停止应用服务）：
    python manage.py ish_restore <备份目录> --confirm-production [--pg-bin <bin>] [--lock-wait 0]
    python manage.py ish_restore --rollback <恢复ID>      # 依据日志把切换撤销（例如脚本在切换后设置权限失败）
    python manage.py ish_restore --cleanup <恢复ID>       # 确认无误后删除保留的旧库与旧文件
    python manage.py ish_restore --status                 # 列出恢复日志

状态机（每一步写入 DATA_DIR/ops/restore-<ID>.json 与同名 .log，崩溃后可诊断/回退）：
  VERIFY        校验备份（SHA256、清单、format）、schema 兼容（备份不得来自更新的程序）、磁盘空间
  LOCKED        获取全局运维锁 + 维护标记（token）
  SAFETY_BACKUP 当前状态的一致性备份（应用已停写），记录备份目录
  STAGE_DB      新建 staging 库并 pg_restore（单事务）；旧 schema 备份在 staging 上执行迁移到当前程序版本
  STAGE_FILES   复制附件/PDF 到 DATA_DIR/private.restore-<ID> 并逐个核对 SHA256（空备份 → 空目录）
  STAGE_VERIFY  对 staging 库 + staging 文件运行完整性检查（报价快照/PDF 哈希、案例、附件、表行数）
  SWITCH        正式库改名为 <db>_old_<ID>，staging 改名为正式库；private 改名为 private.old-<ID>，staging 文件改名为 private
  POST_VERIFY   对切换后的正式库与文件再次完整性检查
  DONE          解除维护（仅自己的 token）；旧库/旧文件保留，待 --cleanup
任何一步失败：按日志逆序撤销已完成的切换，删除 staging，解除自己的维护标记，状态 ROLLED_BACK，退出码非 0。
正式数据在 SWITCH 之前从未被修改；SWITCH 只使用数据库改名与目录改名（同一磁盘卷），不存在“半复制”的正式目录。
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.common import pgops
from apps.common.faults import fault

PHASES = ["VERIFY", "LOCKED", "SAFETY_BACKUP", "STAGE_DB", "STAGE_FILES", "STAGE_VERIFY", "SWITCH", "POST_VERIFY", "DONE"]


def ops_dir():
    d = settings.DATA_DIR / "ops"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Journal:
    def __init__(self, rid, data=None):
        self.id = rid
        self.path = ops_dir() / f"restore-{rid}.json"
        self.log = ops_dir() / f"restore-{rid}.log"
        self.data = data or {"id": rid, "phase": "NEW", "state": "RUNNING", "steps": [], "created_at": datetime.now().isoformat(timespec="seconds")}

    @classmethod
    def load(cls, rid):
        p = ops_dir() / f"restore-{rid}.json"
        if not p.exists():
            raise CommandError(f"找不到恢复日志：{p}")
        return cls(rid, json.loads(p.read_text(encoding="utf-8")))

    def save(self):
        from apps.common.opslock import write_status_file

        write_status_file(self.path, self.data)

    def say(self, msg):
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{self.data.get('phase')}] {msg}"
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return line

    def phase(self, name, **kw):
        self.data["phase"] = name
        self.data.update(kw)
        self.save()
        self.say(f"进入阶段 {name}")

    def step(self, name, **kw):
        """记录一个已完成、需要时可逆的切换动作。"""
        self.data["steps"].append({"step": name, "at": datetime.now().isoformat(timespec="seconds"), **kw})
        self.save()
        self.say(f"完成 {name} {kw}")


def dir_size(p: Path):
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def run_manage(args, extra_env, cwd=None):
    """在子进程中运行 manage.py：数据库与数据目录总是显式传入（不依赖父进程环境变量的默认值）。"""
    from apps.common.opslock import child_env

    env = child_env({"DATA_DIR": str(settings.DATA_DIR), **extra_env})
    r = subprocess.run([sys.executable, "-X", "utf8", str(Path(settings.BASE_DIR) / "manage.py"), *args], env=env, capture_output=True, text=True, cwd=str(cwd or settings.BASE_DIR))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def db_url_for(name):
    from urllib.parse import quote

    db = settings.DATABASES["default"]
    pw = f":{quote(db['PASSWORD'], safe='')}" if db.get("PASSWORD") else ""
    return f"postgres://{quote(db['USER'], safe='')}{pw}@{db['HOST']}:{db['PORT']}/{name}"


class Command(BaseCommand):
    help = "正式恢复（staging 验证 + 原子切换 + 失败回退）"

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?")
        parser.add_argument("--confirm-production", action="store_true")
        parser.add_argument("--pg-bin", default=None)
        parser.add_argument("--lock-wait", type=float, default=0)
        parser.add_argument("--rollback", default=None, metavar="ID")
        parser.add_argument("--cleanup", default=None, metavar="ID")
        parser.add_argument("--status", action="store_true")
        parser.add_argument("--skip-safety-backup", action="store_true", help="仅测试/空系统首装恢复使用：不做当前状态备份")
        parser.add_argument("--backup-dest", default=None, help="恢复前当前状态备份的目录（默认 ISH_BACKUP_DIR 或 DATA_DIR/../backups）")

    # ------------------------------------------------------------------ 入口
    def handle(self, *args, path, confirm_production, pg_bin, lock_wait, rollback, cleanup, status, skip_safety_backup, backup_dest, **kw):
        from apps.common.opslock import OpsBusy, ops_lock

        self.pg_bin = pg_bin
        self.backup_dest = Path(backup_dest or os.environ.get("ISH_BACKUP_DIR") or (settings.DATA_DIR.parent / "backups"))
        if status:
            for p in sorted(ops_dir().glob("restore-*.json")):
                d = json.loads(p.read_text(encoding="utf-8"))
                self.stdout.write(f"{d['id']}  {d.get('state')}  阶段 {d.get('phase')}  备份 {d.get('backup', '')}")
            return
        try:
            with ops_lock("restore", wait=lock_wait):
                if rollback:
                    j = Journal.load(rollback)
                    self._rollback(j, "管理员/脚本请求回退")
                    return
                if cleanup:
                    return self._cleanup(Journal.load(cleanup))
                if not path:
                    raise CommandError("请指定备份目录")
                if not confirm_production:
                    raise CommandError("正式恢复必须加 --confirm-production（演练请使用 ish_verify_backup --restore-to-db）")
                return self._restore(Path(path), skip_safety_backup)
        except OpsBusy as e:
            raise CommandError(str(e), returncode=75)

    # ------------------------------------------------------------------ 主流程
    def _restore(self, bpath: Path, skip_safety_backup):
        from apps.accounts.management.commands.ish_verify_backup import verify_backup_dir
        from apps.common.opslock import MaintenanceHeld, acquire_maintenance, clear_maintenance, current_token

        rid = datetime.now().strftime("%Y%m%d%H%M%S") + f"{os.getpid() % 1000:03d}"
        j = Journal(rid)
        j.data.update(backup=str(bpath), prod_db=pgops.prod_db())
        j.phase("VERIFY")
        self._say(j, f"恢复 ID {rid}，备份 {bpath}")
        man = verify_backup_dir(bpath)
        if man["database"]["vendor"] != "postgresql":
            raise CommandError("正式恢复仅支持 PostgreSQL 备份")
        pgops.check_schema_compatible(man.get("migrations", []))
        need = (bpath / "db.dump").stat().st_size * 4 + dir_size(bpath / "private") * 2 + 200 * 2**20
        free = shutil.disk_usage(settings.DATA_DIR).free
        if free < need:
            raise CommandError(f"磁盘空间不足：需要约 {need // 2**20} MB（staging 库 + staging 文件 + 保留旧数据），剩余 {free // 2**20} MB")
        prod = pgops.prod_db()
        pgops.close_django()
        if pgops.other_sessions(prod, self.pg_bin) > 0:
            raise CommandError("正式数据库仍有其他连接：请先停止应用服务（05-恢复备份 会自动停止）")
        token = current_token()
        try:
            _t, created = acquire_maintenance(f"restore {rid}", token)
        except MaintenanceHeld as e:
            raise CommandError(str(e))
        j.phase("LOCKED", token_owner=created)
        try:
            self._run(j, bpath, man, prod, skip_safety_backup)
        except BaseException as e:  # noqa: BLE001 —— 包括 KeyboardInterrupt：尽力回退
            j.data["error"] = f"{type(e).__name__}: {e}"[:800]
            j.save()
            self._say(j, f"失败：{j.data['error']}")
            self._rollback(j, "自动回退")
            if created:
                clear_maintenance(token)
            raise CommandError(f"恢复失败，已自动回退到恢复前状态（恢复 ID {rid}，日志 {j.log}）：{e}")
        if created:
            clear_maintenance(token)
        j.phase("DONE", state="DONE", finished_at=datetime.now().isoformat(timespec="seconds"))
        self.stdout.write(self.style.SUCCESS(
            f"恢复完成（恢复 ID {rid}）。旧数据库保留为 {j.data['old_db']}，旧文件保留在 {j.data['old_private']}；"
            f"确认业务无误后运行：manage.py ish_restore --cleanup {rid}"))
        self.restore_id = rid

    def _run(self, j, bpath, man, prod, skip_safety_backup):
        rid = j.id
        if not skip_safety_backup:
            j.phase("SAFETY_BACKUP")
            cmd = _Capture()
            call_command("ish_backup", dest=str(self.backup_dest), pg_bin=self.pg_bin, label=f"before-restore-{rid}", stdout=cmd)
            st = json.loads(settings.BACKUP_STATUS_FILE.read_text(encoding="utf-8"))
            j.data["safety_backup"] = st.get("path")
            j.save()
            self._say(j, f"当前状态已备份：{st.get('path')}")
        fault("restore.after_safety_backup")

        # ---- staging 库
        j.phase("STAGE_DB")
        staging = f"{prod}_rst_{rid}"[:63]
        pgops.create_db(staging, self.pg_bin)
        j.data["staging_db"] = staging
        j.save()
        fault("restore.pg_restore")
        from apps.accounts.management.commands.ish_backup import pg_tool

        r = subprocess.run([pg_tool("pg_restore", self.pg_bin), "--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction", "-d", staging, str(bpath / "db.dump")],
                           env=pgops.pg_env_for(staging), capture_output=True, text=True)
        if r.returncode != 0:
            raise CommandError(f"pg_restore 到 staging 库失败：{r.stderr.strip()[:500]}")
        self._say(j, f"已恢复到 staging 库 {staging}")
        backup_migs = set(man.get("migrations", []))
        pending = sorted(pgops.known_migrations() - backup_migs)
        if pending:
            if True:
                self._say(j, f"备份来自较旧的 schema，在 staging 库上执行 {len(pending)} 个迁移")
                rc, out = run_manage(["migrate", "--noinput"], {"DATABASE_URL": db_url_for(staging)})
                if rc != 0:
                    raise CommandError("staging 库迁移失败：" + out[-800:])

        # ---- staging 文件（与正式 private 在同一目录，保证切换为同卷改名）
        j.phase("STAGE_FILES")
        live = settings.PRIVATE_STORAGE_DIR
        stage_dir = live.parent / f"private.restore-{rid}"
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        j.data["staging_private"] = str(stage_dir)
        j.save()
        stage_dir.mkdir(parents=True)
        src = bpath / "private"
        files = man.get("files", [])
        for i, f in enumerate(files):
            s, t = src / f["path"], stage_dir / f["path"]
            t.parent.mkdir(parents=True, exist_ok=True)
            if i == len(files) // 2:
                fault("restore.copy_files")
            shutil.copyfile(s, t)
        from apps.accounts.management.commands.ish_backup import sha256_file

        for f in files:
            if sha256_file(stage_dir / f["path"]) != f["sha256"]:
                raise CommandError(f"staging 文件哈希不一致：{f['path']}")
        self._say(j, f"staging 文件就绪：{len(files)} 个，{dir_size(stage_dir) // 1024} KB")

        # ---- staging 校验（独立子进程：staging 库 + staging 文件）
        j.phase("STAGE_VERIFY")
        rc, out = run_manage(["verify_integrity", "--expect-manifest", str(bpath / "manifest.json"), "--private-dir", str(stage_dir)],
                             {"DATABASE_URL": db_url_for(staging)})
        self._say(j, out.strip()[-600:])
        if rc != 0:
            raise CommandError("staging 完整性检查失败：" + out[-800:])
        fault("restore.after_stage_verify")

        # ---- 切换（只用改名）
        j.phase("SWITCH")
        old_db = f"{prod}_old_{rid}"[:63]
        pgops.close_django()
        if pgops.other_sessions(prod, self.pg_bin) > 0:
            raise CommandError("切换前发现正式库有其他连接，已中止（未修改正式数据）")
        pgops.rename_db(prod, old_db, self.pg_bin)
        j.step("db_prod_to_old", src=prod, dst=old_db)
        j.data["old_db"] = old_db
        fault("restore.switch_db")
        pgops.rename_db(staging, prod, self.pg_bin)
        j.step("db_staging_to_prod", src=staging, dst=prod)
        old_private = live.parent / f"private.old-{rid}"
        j.data["old_private"] = str(old_private)
        if live.exists():
            os.replace(live, old_private)
            j.step("files_live_to_old", src=str(live), dst=str(old_private))
        else:
            j.step("files_live_absent")
        fault("restore.switch_files")
        os.replace(stage_dir, live)
        j.step("files_staging_to_live", src=str(stage_dir), dst=str(live))

        # ---- 切换后校验（新连接）
        j.phase("POST_VERIFY")
        fault("restore.post_verify")
        rc, out = run_manage(["verify_integrity", "--expect-manifest", str(bpath / "manifest.json"), "--private-dir", str(live)], {"DATABASE_URL": db_url_for(prod)})
        self._say(j, out.strip()[-400:])
        if rc != 0:
            raise CommandError("切换后完整性检查失败：" + out[-800:])

    # ------------------------------------------------------------------ 回退
    def _rollback(self, j: Journal, why):
        self._say(j, f"开始回退（{why}）")
        prod = j.data.get("prod_db") or pgops.prod_db()
        steps = j.data.get("steps", [])
        errors = []
        for st in reversed(steps):
            if st.get("undone"):
                continue
            try:
                name = st["step"]
                if name == "files_staging_to_live":
                    live = Path(st["dst"])
                    os.replace(live, Path(st["src"]))
                elif name == "files_live_to_old":
                    os.replace(Path(st["dst"]), Path(st["src"]))
                elif name == "db_staging_to_prod":
                    pgops.rename_db(prod, st["src"], self.pg_bin)
                elif name == "db_prod_to_old":
                    pgops.rename_db(st["dst"], st["src"], self.pg_bin)
                st["undone"] = datetime.now().isoformat(timespec="seconds")
                j.save()
                self._say(j, f"已撤销 {name}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{st['step']}: {e}")
                self._say(j, f"撤销 {st['step']} 失败：{e}")
                break  # 逆序撤销：前一步失败则不能继续，保留日志供人工处理
        if not errors:
            sd = j.data.get("staging_db")
            if sd and pgops.db_exists(sd, self.pg_bin):
                pgops.drop_db(sd, self.pg_bin)
            sp = j.data.get("staging_private")
            if sp and Path(sp).exists():
                shutil.rmtree(sp, ignore_errors=True)
        j.data["state"] = "ROLLBACK_FAILED" if errors else "ROLLED_BACK"
        j.data["rollback_errors"] = errors
        j.data["rolled_back_at"] = datetime.now().isoformat(timespec="seconds")
        j.save()
        if errors:
            raise CommandError(f"自动回退未完成：{errors}。请勿手工改动数据目录，联系实施人员按日志 {j.log} 处理。")
        self._say(j, "回退完成：正式库与私有文件为恢复前状态")

    def _cleanup(self, j: Journal):
        if j.data.get("state") != "DONE":
            raise CommandError("只能清理已完成（DONE）的恢复")
        od, op = j.data.get("old_db"), j.data.get("old_private")
        if od and pgops.db_exists(od, self.pg_bin):
            pgops.drop_db(od, self.pg_bin)
        if op and Path(op).exists():
            shutil.rmtree(op)
        j.data["cleaned_at"] = datetime.now().isoformat(timespec="seconds")
        j.save()
        self.stdout.write(f"已删除旧库 {od} 与旧文件 {op}")

    def _say(self, j, msg):
        self.stdout.write(j.say(msg))


class _Capture:
    def __init__(self):
        self.buf = []

    def write(self, s):
        self.buf.append(s)

    def flush(self):
        pass
