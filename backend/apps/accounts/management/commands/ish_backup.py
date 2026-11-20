"""一致性备份（L4，RC2 加固）：运维互斥锁 → 维护窗口 → advisory 排他锁 → pg_dump + 私有文件快照 + 清单/SHA256 → 原子发布。

用法：python manage.py ish_backup --dest <备份根目录> [--keep-days 30] [--pg-bin <bin>] [--copy-to <异盘/异机目录>] [--lock-wait 秒]

流程：
0. 获取全局运维锁 DATA_DIR/ops.lock（与恢复/升级/安装互斥）；拿不到 → 退出码 75（忙），不改上一次备份状态；
1. 放置带 token 的维护标志 DATA_DIR/MAINTENANCE：新的业务写请求被中间件 503 拒绝（读取照常）；
   标志已被其他任务持有（例如升级失败后保持的只读状态）→ 拒绝备份，且不删除该标志；
   若是父任务（恢复/升级）以同一 token 放置的标志 → 复用且结束时不删除（由父任务负责）；
2. 独立数据库连接获取 pg_advisory_lock(ADVISORY_LOCK_KEY)：等待所有已经进入的写事务提交；
3. 锁内 pg_dump（自定义格式）与 private/ 复制（**空系统也创建空的 private/ 目录**）；
4. manifest.json（format 2：版本、迁移、表行数、文件清单与 SHA256、运维 token）与 SHA256SUMS，校验后 rename 为正式目录；
5. 释放 advisory 锁、仅删除自己的维护标志；原子写 backup_status.json；清理过期备份（永远保留本次）。
异盘副本先写入目标目录下的 .tmp-*，校验通过后才改名，失败只告警、不影响本机备份。

一致性边界：登录/登出/CSRF 不受维护模式限制（last_login 与登录审计可能晚于快照），见 apps/common/opslock.py。
失败：保留上一个可用备份，状态文件记录 error，退出码非 0；不会留下半成品正式目录。
SQLite（仅 DEMO）：直接复制数据库文件（演示用途）。
"""
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, connections

FORMAT_VERSION = 2
BUSY_EXIT = 75  # 另一个运维任务正在进行（计划任务应视为“延期”，不是失败）


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def pg_tool(name, pg_bin=None):
    exe = name + (".exe" if os.name == "nt" else "")
    if pg_bin:
        p = Path(pg_bin) / exe
        if p.exists():
            return str(p)
        raise CommandError(f"找不到 {p}")
    found = shutil.which(name)
    if not found:
        raise CommandError(f"找不到 {name}，请用 --pg-bin 指定 PostgreSQL bin 目录")
    return found


def pg_env():
    db = settings.DATABASES["default"]
    env = os.environ.copy()
    env.update({"PGHOST": db["HOST"], "PGPORT": str(db["PORT"]), "PGUSER": db["USER"], "PGDATABASE": db["NAME"]})
    if db.get("PASSWORD"):
        env["PGPASSWORD"] = db["PASSWORD"]
    return env


def write_status(**kw):
    from apps.common.opslock import write_status_file

    write_status_file(settings.BACKUP_STATUS_FILE, {"at": datetime.now().isoformat(timespec="seconds"), **kw})


def table_counts():
    from django.apps import apps

    out = {}
    for m in apps.get_models():
        if m._meta.app_label in ("accounts", "organizations", "products", "projects", "quotations", "contracts", "performances", "files", "audit", "imports"):
            out[m._meta.label] = m.objects.count()
    return out


def applied_migrations():
    with connection.cursor() as c:
        c.execute("SELECT app, name FROM django_migrations ORDER BY app, name")
        return [f"{a}.{n}" for a, n in c.fetchall()]


class Command(BaseCommand):
    help = "一致性备份数据库与私有文件"

    def add_arguments(self, parser):
        parser.add_argument("--dest", required=True)
        parser.add_argument("--keep-days", type=int, default=30)
        parser.add_argument("--pg-bin", default=None)
        parser.add_argument("--copy-to", default=None, help="备份完成后再复制一份到该目录（异盘/异机共享），失败只告警")
        parser.add_argument("--lock-timeout", type=int, default=300, help="等待进行中写事务的最长秒数")
        parser.add_argument("--label", default="")
        parser.add_argument("--lock-wait", type=float, default=0, help="等待其他运维任务结束的最长秒数（默认不等待，忙则退出码 75）")
        parser.add_argument("--no-prune", action="store_true", help="不清理过期备份（恢复/升级前的安全备份使用）")

    def handle(self, *args, lock_wait=0, **kw):
        from apps.common.opslock import OpsBusy, ops_lock

        try:
            with ops_lock("backup", wait=lock_wait) as token:
                self._token = token
                return self._handle(**kw)
        except OpsBusy as e:
            raise CommandError(str(e), returncode=BUSY_EXIT)

    def _handle(self, dest, keep_days, pg_bin, copy_to, lock_timeout, label, no_prune=False, **kw):
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(dest).free
        need = sum(f.stat().st_size for f in settings.PRIVATE_STORAGE_DIR.rglob("*") if f.is_file()) if settings.PRIVATE_STORAGE_DIR.exists() else 0
        if free < need * 2 + 200 * 1024 * 1024:
            write_status(ok=False, task_token=self._token, error=f"备份目标磁盘空间不足（剩余 {free // 2**20} MB）")
            raise CommandError(f"备份目标磁盘空间不足：剩余 {free // 2**20} MB")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{label}" if label else "")
        tmp = dest / f".tmp-{stamp}"
        final = dest / f"ish-backup-{stamp}"
        if final.exists():
            raise CommandError(f"目标已存在：{final}")
        tmp.mkdir()
        (tmp / "private").mkdir()  # R02：空系统也有明确的空 private 目录
        flag = settings.MAINTENANCE_FLAG
        t0 = time.time()
        try:
            self._dump(tmp, stamp, flag, pg_bin, lock_timeout, t0)
        except BaseException as e:
            shutil.rmtree(tmp, ignore_errors=True)
            write_status(ok=False, task_token=self._token, error=str(e)[:300])
            raise
        manifest = self._manifest
        try:
            (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
            sums = []
            for f in sorted(tmp.rglob("*")):
                if f.is_file():
                    sums.append(f"{sha256_file(f)}  {f.relative_to(tmp).as_posix()}")
            (tmp / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
            # 发布前自检：校验和全部可复算
            from apps.accounts.management.commands.ish_verify_backup import verify_backup_dir

            verify_backup_dir(tmp)
            os.replace(tmp, final)
        except Exception as e:
            shutil.rmtree(tmp, ignore_errors=True)
            write_status(ok=False, task_token=self._token, error=str(e)[:300])
            raise
        elapsed = round(time.time() - t0, 1)
        copied = None
        if copy_to:
            ctmp = None
            try:
                target = Path(copy_to) / final.name
                ctmp = Path(copy_to) / f".tmp-{final.name}"
                shutil.rmtree(ctmp, ignore_errors=True)
                shutil.copytree(final, ctmp)
                from apps.accounts.management.commands.ish_verify_backup import verify_backup_dir

                verify_backup_dir(ctmp)
                os.replace(ctmp, target)
                copied = str(target)
            except Exception as e:  # noqa: BLE001
                if ctmp is not None:
                    shutil.rmtree(ctmp, ignore_errors=True)
                self.stderr.write(f"警告：异盘/异机副本失败：{e}")
                copied = f"失败：{e}"
        if not no_prune:
            self._prune(dest, keep_days, final)
        total = sum(f.stat().st_size for f in final.rglob("*") if f.is_file())
        write_status(ok=True, task_token=self._token, path=str(final), size_mb=round(total / 2**20, 2), seconds=elapsed, copy=copied, files=len(manifest["files"]))
        self.backup_path = final
        self.stdout.write(self.style.SUCCESS(f"备份完成：{final}（{round(total / 2**20, 2)} MB，{elapsed} 秒，维护窗口内等待写入 {manifest['waited_for_writers_seconds']} 秒）"))

    def _dump(self, tmp, stamp, flag, pg_bin, lock_timeout, t0):
        """维护窗口内：排他 advisory 锁 → pg_dump + 私有文件复制。finally 中保证释放锁与解除维护。"""
        from apps.common.opslock import MaintenanceHeld, acquire_maintenance, clear_maintenance

        flag_created = False
        lock_conn = None
        try:
            try:
                _tok, flag_created = acquire_maintenance(f"backup {stamp}", self._token)
            except MaintenanceHeld as e:
                raise CommandError(str(e))
            vendor = connection.vendor
            if vendor == "postgresql":
                lock_conn = connections.create_connection("default")
                lock_conn.ensure_connection()
                lock_conn.set_autocommit(True)
                with lock_conn.cursor() as c:
                    c.execute("SET lock_timeout = %s", [f"{lock_timeout}s"])
                    c.execute("SELECT pg_advisory_lock(%s)", [settings.ADVISORY_LOCK_KEY])
                waited = round(time.time() - t0, 2)
                r = subprocess.run([pg_tool("pg_dump", pg_bin), "--format=custom", "--no-owner", "--no-privileges", "--file", str(tmp / "db.dump")],
                                   env=pg_env(), capture_output=True, text=True)
                if r.returncode != 0:
                    raise CommandError(f"pg_dump 失败：{r.stderr.strip()[:500]}")
            else:
                waited = 0
                connection.close()
                shutil.copy2(settings.DATABASES["default"]["NAME"], tmp / "db.sqlite3")
            files = []
            if settings.PRIVATE_STORAGE_DIR.exists():
                for f in sorted(settings.PRIVATE_STORAGE_DIR.rglob("*")):
                    if f.is_file() and not f.name.endswith(".part"):
                        rel = f.relative_to(settings.PRIVATE_STORAGE_DIR).as_posix()
                        target = tmp / "private" / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, target)
                        files.append({"path": rel, "size": target.stat().st_size, "sha256": sha256_file(target)})
            self._manifest = {
                "format": FORMAT_VERSION,
                "app_version": settings.APP_VERSION,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "database": {"vendor": vendor, "name": settings.DATABASES["default"]["NAME"] if vendor == "postgresql" else "sqlite"},
                "migrations": applied_migrations(),
                "table_counts": table_counts(),
                "files": files,
                "waited_for_writers_seconds": waited,
                "ops": {"task": "backup", "label": stamp},
            }
        finally:
            if lock_conn is not None:
                try:
                    with lock_conn.cursor() as c:
                        c.execute("SELECT pg_advisory_unlock(%s)", [settings.ADVISORY_LOCK_KEY])
                except Exception:  # noqa: BLE001 —— 连接断开时锁随会话自动释放
                    pass
                finally:
                    lock_conn.close()
            if flag_created:
                clear_maintenance(self._token)

    def _prune(self, dest: Path, keep_days: int, keep: Path):
        cutoff = datetime.now() - timedelta(days=keep_days)
        for d in sorted(dest.glob("ish-backup-*")):
            if d == keep or not d.is_dir():
                continue
            # 恢复/升级生成的安全备份（before-restore / pre-upgrade）被运维日志引用，自动清理永不删除，由管理员确认后手工清理
            if "-before-restore" in d.name or "-pre-upgrade" in d.name:
                continue
            if datetime.fromtimestamp(d.stat().st_mtime) < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        for d in dest.glob(".tmp-*"):
            if datetime.fromtimestamp(d.stat().st_mtime) < datetime.now() - timedelta(hours=6):
                shutil.rmtree(d, ignore_errors=True)
