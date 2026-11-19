"""一致性备份（L4）：维护窗口 → advisory 排他锁 → pg_dump + 私有文件快照 + 清单/SHA256 → 原子发布。

用法：python manage.py ish_backup --dest <备份根目录> [--keep-days 30] [--pg-bin <PostgreSQL bin 目录>] [--copy-to <异盘/异机目录>]

流程：
1. 创建 DATA_DIR/MAINTENANCE 标志：此后新的写请求被中间件以 503 拒绝（读取照常）；
2. 用独立数据库连接获取 pg_advisory_lock(ADVISORY_LOCK_KEY)（排他）：会等待所有已经进入的写事务提交（写事务持有共享锁）；
3. 在锁内执行 pg_dump（自定义格式）与 private/ 文件复制，二者处于同一个“无写入”时刻，保证数据库与 PDF/附件一致；
4. 生成 manifest.json（版本、迁移状态、表行数、文件清单与 SHA256）与 SHA256SUMS，写入临时目录 `.tmp-*`，校验后 rename 为正式目录；
5. 释放锁、删除维护标志（finally 保证异常时也恢复服务）；更新 DATA_DIR/backup_status.json；清理过期备份（永远保留最近一个成功备份）。

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

FORMAT_VERSION = 1


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
    data = {"at": datetime.now().isoformat(timespec="seconds"), **kw}
    tmp = settings.BACKUP_STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, settings.BACKUP_STATUS_FILE)


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

    def handle(self, *args, dest, keep_days, pg_bin, copy_to, lock_timeout, label, **kw):
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(dest).free
        need = sum(f.stat().st_size for f in settings.PRIVATE_STORAGE_DIR.rglob("*") if f.is_file()) if settings.PRIVATE_STORAGE_DIR.exists() else 0
        if free < need * 2 + 200 * 1024 * 1024:
            write_status(ok=False, error=f"备份目标磁盘空间不足（剩余 {free // 2**20} MB）")
            raise CommandError(f"备份目标磁盘空间不足：剩余 {free // 2**20} MB")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{label}" if label else "")
        tmp = dest / f".tmp-{stamp}"
        final = dest / f"ish-backup-{stamp}"
        if final.exists():
            raise CommandError(f"目标已存在：{final}")
        tmp.mkdir()
        flag = settings.MAINTENANCE_FLAG
        t0 = time.time()
        try:
            self._dump(tmp, stamp, flag, pg_bin, lock_timeout, t0)
        except BaseException as e:
            shutil.rmtree(tmp, ignore_errors=True)
            write_status(ok=False, error=str(e)[:300])
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
            write_status(ok=False, error=str(e)[:300])
            raise
        elapsed = round(time.time() - t0, 1)
        copied = None
        if copy_to:
            try:
                target = Path(copy_to) / final.name
                shutil.copytree(final, target)
                from apps.accounts.management.commands.ish_verify_backup import verify_backup_dir

                verify_backup_dir(target)
                copied = str(target)
            except Exception as e:  # noqa: BLE001
                self.stderr.write(f"警告：异盘/异机副本失败：{e}")
                copied = f"失败：{e}"
        self._prune(dest, keep_days, final)
        total = sum(f.stat().st_size for f in final.rglob("*") if f.is_file())
        write_status(ok=True, path=str(final), size_mb=round(total / 2**20, 2), seconds=elapsed, copy=copied, files=len(manifest["files"]))
        self.stdout.write(self.style.SUCCESS(f"备份完成：{final}（{round(total / 2**20, 2)} MB，{elapsed} 秒，维护窗口内等待写入 {manifest['waited_for_writers_seconds']} 秒）"))

    def _dump(self, tmp, stamp, flag, pg_bin, lock_timeout, t0):
        """维护窗口内：排他 advisory 锁 → pg_dump + 私有文件复制。finally 中保证释放锁与解除维护。"""
        flag_created = False
        lock_conn = None
        try:
            flag.write_text(f"backup {stamp} pid={os.getpid()}", encoding="utf-8")
            flag_created = True
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
            if flag_created and flag.exists():
                flag.unlink()

    def _prune(self, dest: Path, keep_days: int, keep: Path):
        cutoff = datetime.now() - timedelta(days=keep_days)
        for d in sorted(dest.glob("ish-backup-*")):
            if d == keep or not d.is_dir():
                continue
            if datetime.fromtimestamp(d.stat().st_mtime) < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        for d in dest.glob(".tmp-*"):
            if datetime.fromtimestamp(d.stat().st_mtime) < datetime.now() - timedelta(hours=6):
                shutil.rmtree(d, ignore_errors=True)
