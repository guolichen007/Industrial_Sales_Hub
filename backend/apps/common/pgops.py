"""PostgreSQL 运维辅助（恢复/升级用）：只通过 psql/pg_restore 子进程与维护库 postgres 交互，不依赖业务模型。

应用账号 ish_app 为数据库 ish 的 owner 且具有 CREATEDB，因此无需超级用户即可：
- CREATE DATABASE x TEMPLATE ish（要求 ish 上没有其他连接）——升级前的整库快照；
- ALTER DATABASE a RENAME TO b（要求 a 上没有任何连接）——恢复/回滚时的原子切换；
- DROP DATABASE（仅限本工具创建的 staging/快照库）。
"""
import re
import subprocess

from django.conf import settings
from django.core.management.base import CommandError
from django.db import connections

SAFE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _q(name):
    if not SAFE.match(name):
        raise CommandError(f"非法数据库名：{name!r}")
    return f'"{name}"'


def pg_env_for(dbname=None):
    from apps.accounts.management.commands.ish_backup import pg_env

    env = pg_env()
    if dbname:
        env["PGDATABASE"] = dbname
    return env


def psql(sql, pg_bin=None, db="postgres", check=True):
    from apps.accounts.management.commands.ish_backup import pg_tool

    r = subprocess.run([pg_tool("psql", pg_bin), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", db, "-tAc", sql], env=pg_env_for(db), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise CommandError(f"psql 失败：{r.stderr.strip()[:400]}")
    return r.stdout.strip()


def prod_db():
    return settings.DATABASES["default"]["NAME"]


def db_exists(name, pg_bin=None):
    return psql(f"SELECT 1 FROM pg_database WHERE datname='{name}'", pg_bin) == "1" if SAFE.match(name) else False


def other_sessions(name, pg_bin=None):
    """name 库上除本次 psql 外的会话数（用于确认应用已停止）。"""
    # 只统计客户端连接；autovacuum 工作进程由 PostgreSQL 在改名/删除时自行终止
    return int(psql(f"SELECT count(*) FROM pg_stat_activity WHERE datname='{name}' AND pid <> pg_backend_pid() AND backend_type = 'client backend'", pg_bin) or 0)


def close_django():
    connections.close_all()


def create_db(name, pg_bin=None, template=None):
    close_django()
    psql(f"CREATE DATABASE {_q(name)}" + (f" TEMPLATE {_q(template)}" if template else ""), pg_bin)


def rename_db(old, new, pg_bin=None):
    close_django()
    psql(f"ALTER DATABASE {_q(old)} RENAME TO {_q(new)}", pg_bin)


def drop_db(name, pg_bin=None):
    close_django()
    psql(f"DROP DATABASE IF EXISTS {_q(name)}", pg_bin)


def server_major(pg_bin=None):
    return int(psql("SHOW server_version_num", pg_bin)) // 10000


def migrations_in(dbname, pg_bin=None):
    out = psql("SELECT app || '.' || name FROM django_migrations ORDER BY 1", pg_bin, db=dbname, check=False)
    return [x for x in out.splitlines() if x.strip()]


def known_migrations():
    """当前程序认识的全部迁移（app.name）。"""
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=True)
    return {f"{a}.{n}" for a, n in loader.disk_migrations}


def check_schema_compatible(backup_migrations):
    """备份的 schema 必须不新于当前程序：备份中出现程序不认识的迁移 → 拒绝（不能用旧程序恢复新版本数据）。"""
    unknown = sorted(set(backup_migrations) - known_migrations())
    if unknown:
        raise CommandError(f"备份来自更新的程序版本（含本程序不认识的迁移 {unknown[:5]}）：请使用与备份相同或更新的程序版本恢复")
    return True
