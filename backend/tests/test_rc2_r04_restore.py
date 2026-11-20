"""RC2-R04：正式恢复状态机（真实 PostgreSQL + pg_dump/pg_restore + 文件系统）。

反例（RC1 restore.ps1）：先对正式库 pg_restore --clean，再改名/复制 private；文件复制或校验失败时数据库已被覆盖，
留下“新库 + 旧文件”的半状态，且 --clean 不清除备份中不存在的新表。

本文件覆盖：空库恢复、有业务恢复、实际大附件、故意缺失附件、pg_restore 中断、复制中断、哈希不匹配、
磁盘不足、权限拒绝、切换中/切换后失败、旧 schema（缺迁移）恢复、拒绝来自更新版本的备份、遇忙退出、回退与清理。
"""
import json
import os
import shutil
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from apps.common import pgops
from tests.test_v21_backup_restore import isolated_data, need_pg_tools  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def faults(isolated_data, monkeypatch):
    (settings.DATA_DIR / "ALLOW_FAULT_INJECTION").write_text("test")

    def set_(name):
        monkeypatch.setenv("ISH_FAULT_INJECT", name)

    return set_


def _state():
    """正式库 + 私有目录的可比较指纹。"""
    from apps.accounts.management.commands.ish_backup import table_counts

    files = {}
    root = settings.PRIVATE_STORAGE_DIR
    if root.exists():
        import hashlib

        files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    return table_counts(), files


def _backup(dest):
    call_command("ish_backup", dest=str(dest))
    return sorted(Path(dest).glob("ish-backup-*"))[-1]


def _restore(b, isolated_data, **kw):
    kw.setdefault("backup_dest", str(isolated_data / "safety"))
    call_command("ish_restore", str(b), confirm_production=True, **kw)


def _journal():
    js = sorted((settings.DATA_DIR / "ops").glob("restore-*.json"))
    return json.loads(js[-1].read_text(encoding="utf-8"))


def _no_leftovers():
    prod = pgops.prod_db()
    leftovers = pgops.psql(f"SELECT datname FROM pg_database WHERE datname LIKE '{prod}_rst_%'")
    assert leftovers == "", f"staging 库未清理：{leftovers}"
    assert not list(settings.DATA_DIR.glob("private.restore-*"))
    assert not settings.MAINTENANCE_FLAG.exists()


def _cleanup_old_dbs():
    prod = pgops.prod_db()
    for n in pgops.psql(f"SELECT datname FROM pg_database WHERE datname LIKE '{prod}_old_%' OR datname LIKE '{prod}_rst_%'").splitlines():
        pgops.drop_db(n.strip())


@pytest.fixture(autouse=True)
def _drop_side_dbs():
    yield
    if connection.vendor == "postgresql":
        connection.close()
        _cleanup_old_dbs()


def _attach(users, base, size=2000, name="现场照片.png"):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from rest_framework.test import APIClient

    c = APIClient()
    c.force_authenticate(users["sales"])
    r = c.post("/api/attachments/", {"owner_type": "project", "owner_id": base["p_sales"].id, "file": SimpleUploadedFile(name, b"\x89PNG" + os.urandom(size))}, format="multipart")
    assert r.status_code == 201, r.content
    return r.json()


def test_restore_business_backup_roundtrip(users, base, make_quote, isolated_data):
    """有业务数据：备份 → 继续写入 → 正式恢复 → 回到备份时刻（含删除备份后新增的文件与行）。"""
    need_pg_tools()
    make_quote()
    _attach(users, base, size=3 * 2**20, name="大附件.png")  # 实际大附件 3 MB
    b = _backup(isolated_data / "bk")
    snap = _state()
    make_quote()  # 备份之后的新数据
    _attach(users, base)
    assert _state() != snap
    _restore(b, isolated_data)
    assert _state() == snap
    j = _journal()
    assert j["state"] == "DONE" and j["safety_backup"] and Path(j["safety_backup"]).exists()
    assert pgops.db_exists(j["old_db"]) and Path(j["old_private"]).exists()
    _no_leftovers()
    # 清理旧库/旧文件
    call_command("ish_restore", cleanup=j["id"])
    assert not pgops.db_exists(j["old_db"]) and not Path(j["old_private"]).exists()


def test_restore_empty_backup_over_business_data(users, base, make_quote, isolated_data):
    """空系统备份（空 private）恢复到有业务的系统：附件与报价全部回到空。"""
    need_pg_tools()
    b = _backup(isolated_data / "bk")
    snap = _state()
    assert snap[1] == {}
    make_quote()
    _attach(users, base)
    _restore(b, isolated_data)
    assert _state() == snap
    assert settings.PRIVATE_STORAGE_DIR.is_dir()
    _no_leftovers()


def test_restore_rejects_backup_with_missing_attachment(users, base, make_quote, isolated_data):
    """备份中故意缺失一个应有附件：在任何正式数据改变之前拒绝。"""
    need_pg_tools()
    make_quote()
    _attach(users, base)
    b = _backup(isolated_data / "bk")
    victim = next((b / "private" / "attachments").rglob("*.png"))
    victim.unlink()
    before = _state()
    with pytest.raises(CommandError):
        _restore(b, isolated_data)
    assert _state() == before
    assert not settings.MAINTENANCE_FLAG.exists()


@pytest.mark.parametrize("stage", ["restore.pg_restore", "restore.copy_files", "restore.after_stage_verify", "restore.switch_db", "restore.switch_files", "restore.post_verify"])
def test_fault_at_each_stage_rolls_back_completely(users, base, make_quote, isolated_data, faults, stage):
    need_pg_tools()
    make_quote()
    _attach(users, base)
    b = _backup(isolated_data / "bk")
    make_quote()
    _attach(users, base)
    before = _state()
    faults(stage)
    with pytest.raises(CommandError, match="已自动回退"):
        _restore(b, isolated_data)
    connection.close()
    assert _state() == before, f"{stage}：回退后数据必须与恢复前完全一致"
    j = _journal()
    assert j["state"] == "ROLLED_BACK" and stage.split(".")[1] in j["error"]
    _no_leftovers()


def test_hash_mismatch_in_staging_is_rejected_before_switch(users, base, make_quote, isolated_data, monkeypatch):
    """备份通过了 SHA256SUMS 校验但 staging 复制后内容不同（模拟介质错误）：切换前拒绝。"""
    need_pg_tools()
    make_quote()
    b = _backup(isolated_data / "bk")
    before = _state()
    import apps.accounts.management.commands.ish_restore as R

    real = shutil.copyfile

    def corrupt(src, dst, *a, **k):
        real(src, dst, *a, **k)
        Path(dst).write_bytes(Path(dst).read_bytes() + b"x")

    monkeypatch.setattr(R.shutil, "copyfile", corrupt)
    with pytest.raises(CommandError, match="哈希不一致"):
        _restore(b, isolated_data)
    assert _state() == before
    _no_leftovers()


def test_permission_denied_on_switch_rolls_back(users, base, make_quote, isolated_data, monkeypatch):
    """切换文件时遇到“拒绝访问”（例如杀毒软件/资源管理器占用 private 目录）：数据库切换被撤销。"""
    need_pg_tools()
    make_quote()
    b = _backup(isolated_data / "bk")
    make_quote()
    before = _state()
    import apps.accounts.management.commands.ish_restore as R

    real = os.replace

    def deny(src, dst):
        if str(src).endswith("private") and ".old-" in str(dst):
            raise PermissionError(13, "拒绝访问", str(src))
        return real(src, dst)

    monkeypatch.setattr(R.os, "replace", deny)
    with pytest.raises(CommandError, match="已自动回退"):
        _restore(b, isolated_data)
    connection.close()
    assert _state() == before
    _no_leftovers()


def test_disk_full_is_refused_before_any_change(users, base, make_quote, isolated_data, monkeypatch):
    need_pg_tools()
    make_quote()
    b = _backup(isolated_data / "bk")
    before = _state()
    import apps.accounts.management.commands.ish_restore as R

    monkeypatch.setattr(R.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**12, 10**12 - 10**6, 10**6))
    with pytest.raises(CommandError, match="磁盘空间不足"):
        _restore(b, isolated_data)
    assert _state() == before
    assert not settings.MAINTENANCE_FLAG.exists()


def test_restore_refuses_backup_from_newer_schema(users, base, make_quote, isolated_data):
    need_pg_tools()
    make_quote()
    b = _backup(isolated_data / "bk")
    from apps.accounts.management.commands.ish_backup import sha256_file

    man = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
    man["migrations"].append("quotations.9999_future")
    (b / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    lines = [l for l in (b / "SHA256SUMS").read_text().splitlines() if not l.endswith("  manifest.json")] + [f"{sha256_file(b / 'manifest.json')}  manifest.json"]
    (b / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    with pytest.raises(CommandError, match="更新的程序版本"):
        _restore(b, isolated_data)


def test_restore_old_schema_backup_is_migrated_in_staging(users, base, isolated_data):
    """旧 schema 备份（少一个迁移）：在 staging 库上迁移到当前版本后才切换。"""
    need_pg_tools()
    from apps.accounts.management.commands.ish_backup import pg_env, pg_tool, sha256_file

    b = _backup(isolated_data / "bk")
    # 用“回退一个迁移”的库生成旧 schema 的 dump：复制当前库 → 回滚最后一个 projects 迁移 → 重新 dump
    prod = pgops.prod_db()
    old = f"{prod}_oldschema"
    connection.close()
    pgops.drop_db(old)
    pgops.create_db(old, template=prod)
    import subprocess
    import sys

    env = os.environ.copy()
    env["DATABASE_URL"] = env["DATABASE_URL"].rsplit("/", 1)[0] + "/" + old
    r = subprocess.run([sys.executable, "manage.py", "migrate", "projects", "0001", "--noinput"], env=env, cwd=str(settings.BASE_DIR), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    e2 = pg_env()
    e2["PGDATABASE"] = old
    subprocess.run([pg_tool("pg_dump"), "--format=custom", "--no-owner", "--no-privileges", "--file", str(b / "db.dump")], env=e2, check=True)
    pgops.drop_db(old)
    man = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
    man["migrations"] = [m for m in man["migrations"] if m != "projects.0002_executionmilestone_delay_reason"]
    (b / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    sums = [f"{sha256_file(f)}  {f.relative_to(b).as_posix()}" for f in sorted(b.rglob("*")) if f.is_file() and f.name != "SHA256SUMS"]
    (b / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    _restore(b, isolated_data, skip_safety_backup=True)
    applied = set(pgops.migrations_in(prod))
    assert "projects.0002_executionmilestone_delay_reason" in applied
    assert any("在 staging 库上执行" in l for l in (settings.DATA_DIR / "ops").glob("restore-*.log").__next__().read_text(encoding="utf-8").splitlines())


def test_restore_busy_when_other_ops_running(users, isolated_data):
    need_pg_tools()
    import threading

    from apps.common.opslock import ops_lock

    b = _backup(isolated_data / "bk")
    held, release = threading.Event(), threading.Event()

    def hold():
        with ops_lock("backup", wait=0):
            held.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        with pytest.raises(CommandError, match="正在进行"):
            _restore(b, isolated_data)
    finally:
        release.set()
        t.join()


def test_restore_requires_explicit_confirmation(users, isolated_data):
    need_pg_tools()
    b = _backup(isolated_data / "bk")
    with pytest.raises(CommandError, match="confirm-production"):
        call_command("ish_restore", str(b))


def test_manual_rollback_after_done(users, base, make_quote, isolated_data):
    """恢复已完成后发现问题（例如脚本设置 ACL 失败）：--rollback 回到恢复前。"""
    need_pg_tools()
    b = _backup(isolated_data / "bk")
    make_quote()
    before = _state()
    _restore(b, isolated_data, skip_safety_backup=True)
    assert _state() != before
    call_command("ish_restore", rollback=_journal()["id"])
    connection.close()
    assert _state() == before
