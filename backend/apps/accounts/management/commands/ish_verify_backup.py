"""备份校验与恢复演练（L4）。

python manage.py ish_verify_backup <备份目录>
    仅校验：SHA256SUMS、manifest 格式、文件清单与哈希。坏备份/篡改/缺文件 → 退出码非 0。

python manage.py ish_verify_backup <备份目录> --restore-to-db <临时库名> --restore-data-dir <临时目录> [--pg-bin ...]
    恢复演练：把备份恢复到**新建的临时数据库**与**新的临时私有目录**（若已存在则拒绝），
    然后对恢复结果运行完整性检查（用户/角色、表行数与清单一致、报价快照与 PDF 哈希、案例册、附件）。
    永不触碰当前正式数据库与 data/ 目录。

正式恢复（覆盖生产）不在此命令中实现：必须由运维脚本 05-恢复备份 在停止服务、二次确认、先做一次当前状态备份后执行。
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from .ish_backup import pg_env, pg_tool, sha256_file

SAFE_DB = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


def verify_backup_dir(path: Path) -> dict:
    path = Path(path)
    sums_file = path / "SHA256SUMS"
    man_file = path / "manifest.json"
    if not sums_file.exists() or not man_file.exists():
        raise CommandError("备份不完整：缺少 SHA256SUMS 或 manifest.json")
    listed = {}
    for line in sums_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            digest, rel = line.split("  ", 1)
        except ValueError:
            raise CommandError(f"SHA256SUMS 格式错误：{line[:80]}")
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise CommandError(f"SHA256SUMS 含非法路径：{rel}")
        listed[rel] = digest
    actual = {f.relative_to(path).as_posix() for f in path.rglob("*") if f.is_file() and f.name != "SHA256SUMS"}
    missing = set(listed) - actual - {"SHA256SUMS"}
    extra = actual - set(listed)
    if missing:
        raise CommandError(f"备份缺少文件：{sorted(missing)[:5]}")
    if extra:
        raise CommandError(f"备份含未登记文件（可能被篡改）：{sorted(extra)[:5]}")
    for rel, digest in listed.items():
        if sha256_file(path / rel) != digest:
            raise CommandError(f"文件校验失败（可能损坏或被篡改）：{rel}")
    try:
        man = json.loads(man_file.read_text(encoding="utf-8"))
    except ValueError:
        raise CommandError("manifest.json 无法解析")
    if man.get("format") != 1:
        raise CommandError(f"不支持的备份格式：{man.get('format')}")
    for f in man.get("files", []):
        if listed.get("private/" + f["path"]) != f["sha256"]:
            raise CommandError(f"清单与校验和不一致：{f['path']}")
    if not ((path / "db.dump").exists() or (path / "db.sqlite3").exists()):
        raise CommandError("备份缺少数据库文件")
    return man


class Command(BaseCommand):
    help = "校验备份；可选恢复到临时数据库并做完整性检查"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--restore-to-db", default=None)
        parser.add_argument("--restore-data-dir", default=None)
        parser.add_argument("--pg-bin", default=None)
        parser.add_argument("--keep", action="store_true", help="演练后保留临时库（默认删除）")

    def handle(self, *args, path, restore_to_db, restore_data_dir, pg_bin, keep, **kw):
        p = Path(path)
        man = verify_backup_dir(p)
        self.stdout.write(self.style.SUCCESS(f"校验通过：{p.name}（版本 {man['app_version']}，{len(man['files'])} 个私有文件）"))
        if not restore_to_db:
            return
        if not SAFE_DB.match(restore_to_db):
            raise CommandError("临时库名只能包含小写字母、数字、下划线")
        prod_db = settings.DATABASES["default"]["NAME"]
        if restore_to_db == prod_db:
            raise CommandError("禁止恢复到当前正式数据库")
        if not restore_data_dir:
            raise CommandError("必须同时指定 --restore-data-dir（新的临时目录）")
        rdir = Path(restore_data_dir).resolve()
        data_dir = Path(settings.DATA_DIR).resolve()
        if rdir == data_dir or data_dir in rdir.parents or rdir in data_dir.parents:
            raise CommandError("禁止恢复到当前正式数据目录")
        if rdir.exists() and any(rdir.iterdir()):
            raise CommandError(f"临时目录已存在且非空：{rdir}")
        if man["database"]["vendor"] != "postgresql":
            raise CommandError("恢复演练仅支持 PostgreSQL 备份")
        env = pg_env()
        psql = pg_tool("psql", pg_bin)
        exists = subprocess.run([psql, "-d", "postgres", "-tAc", f"SELECT 1 FROM pg_database WHERE datname='{restore_to_db}'"], env=env, capture_output=True, text=True)
        if exists.returncode != 0:
            raise CommandError(f"无法连接数据库：{exists.stderr.strip()[:300]}")
        if exists.stdout.strip() == "1":
            raise CommandError(f"临时库 {restore_to_db} 已存在，请换一个名字或先手工删除")
        r = subprocess.run([psql, "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", f'CREATE DATABASE "{restore_to_db}"'], env=env, capture_output=True, text=True)
        if r.returncode != 0:
            raise CommandError(f"创建临时库失败：{r.stderr.strip()[:300]}")
        try:
            r = subprocess.run([pg_tool("pg_restore", pg_bin), "--no-owner", "--no-privileges", "--exit-on-error", "-d", restore_to_db, str(p / "db.dump")], env=env, capture_output=True, text=True)
            if r.returncode != 0:
                raise CommandError(f"pg_restore 失败：{r.stderr.strip()[:500]}")
            rdir.mkdir(parents=True, exist_ok=True)
            if (p / "private").exists():
                shutil.copytree(p / "private", rdir / "private")
            # 在子进程中用临时库与临时目录运行完整性检查（不影响本进程的正式连接）
            db = settings.DATABASES["default"]
            sub_env = os.environ.copy()
            pw = f":{db['PASSWORD']}" if db.get("PASSWORD") else ""
            sub_env.update({"DATABASE_URL": f"postgres://{db['USER']}{pw}@{db['HOST']}:{db['PORT']}/{restore_to_db}", "DATA_DIR": str(rdir), "ISH_LOG_DIR": str(rdir / "logs")})
            r = subprocess.run([os.sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "verify_integrity", "--expect-manifest", str(p / "manifest.json")],
                               env=sub_env, capture_output=True, text=True, cwd=str(settings.BASE_DIR))
            self.stdout.write(r.stdout)
            if r.returncode != 0:
                raise CommandError("恢复后完整性检查失败：\n" + (r.stderr or r.stdout)[-1500:])
            self.stdout.write(self.style.SUCCESS(f"恢复演练通过：临时库 {restore_to_db}，临时目录 {rdir}"))
        finally:
            if not keep:
                subprocess.run([psql, "-d", "postgres", "-c", f'DROP DATABASE IF EXISTS "{restore_to_db}"'], env=env, capture_output=True, text=True)
                shutil.rmtree(rdir, ignore_errors=True)
