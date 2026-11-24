"""RC3 定向整改 F-4：恢复失败后不得遗留 RUNNING；正式数据未改动/已回退时可安全恢复服务，回退失败保持保护。

反例（75073de 基线）：`ish_restore._restore` 在 VERIFY 阶段先写 `phase=VERIFY`（state 仍 RUNNING），
随后的纯只读预检查（备份校验/版本兼容/磁盘空间/正式库其他连接）抛错时未进入任何回滚/终态记账区，
日志永远停在 RUNNING；上层 restore.ps1 因此无法判定“数据未改动、可安全恢复应用服务”，只能无条件保持停止(42)。

修复后：预检查任何失败都记为终态 FAILED_PRECHECK（含错误摘要与完成时间，不伪称 DONE、不启动回退），
正式库/私有文件/维护标记均不变；回退失败则记 ROLLBACK_FAILED。对应 restore.ps1 由 Get-RestoreRecoveryDecision 判定
（见 deploy/windows/tests/unit-f4-recovery.ps1）：FAILED_PRECHECK/ROLLED_BACK/无日志 → 重新启动应用；RUNNING/ROLLBACK_FAILED → 保持停止。
"""
import json
import shutil

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from apps.common import pgops
from tests.test_rc2_r04_restore import _backup, _restore, _state, _journal  # noqa: F401
from tests.test_v21_backup_restore import isolated_data, need_pg_tools  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def _no_residue():
    assert not settings.MAINTENANCE_FLAG.exists(), "预检查失败不得遗留维护标记"
    prod = pgops.prod_db()
    leftover = pgops.psql(f"SELECT datname FROM pg_database WHERE datname LIKE '{prod}_rst_%'")
    assert leftover == "", f"预检查失败不得遗留 staging 库：{leftover}"
    assert not list(settings.DATA_DIR.glob("private.restore-*"))


@pytest.fixture(autouse=True)
def _drop_side_dbs():
    yield
    if connection.vendor == "postgresql":
        connection.close()
        prod = pgops.prod_db()
        for n in pgops.psql(f"SELECT datname FROM pg_database WHERE datname LIKE '{prod}_old_%' OR datname LIKE '{prod}_rst_%'").splitlines():
            pgops.drop_db(n.strip())


# ------------------------------------------------------------------ 前置拒绝（全部 → FAILED_PRECHECK，数据未改动）
def test_precheck_disk_full_failed_precheck(users, base, make_quote, isolated_data, monkeypatch):
    need_pg_tools()
    make_quote()
    b = _backup(isolated_data / "bk")
    before = _state()
    import apps.accounts.management.commands.ish_restore as R
    monkeypatch.setattr(R.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**12, 10**12 - 10**6, 10**6))
    with pytest.raises(CommandError, match="磁盘空间不足"):
        _restore(b, isolated_data)
    j = _journal()
    assert j["state"] == "FAILED_PRECHECK" and j.get("finished_at") and "磁盘空间不足" in j.get("error", "")
    assert _state() == before and connection.vendor == "postgresql"
    _no_residue()


def test_precheck_incompatible_version_failed_precheck(users, base, make_quote, isolated_data):
    need_pg_tools()
    make_quote()
    b = _backup(isolated_data / "bk")
    from apps.accounts.management.commands.ish_backup import sha256_file

    man = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
    man["migrations"].append("quotations.9999_future")
    (b / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    lines = [x for x in (b / "SHA256SUMS").read_text().splitlines() if not x.endswith("  manifest.json")]
    (b / "SHA256SUMS").write_text("\n".join(lines + [f"{sha256_file(b / 'manifest.json')}  manifest.json"]) + "\n")
    before = _state()
    with pytest.raises(CommandError, match="更新的程序版本"):
        _restore(b, isolated_data)
    j = _journal()
    assert j["state"] == "FAILED_PRECHECK" and "更新的程序版本" in j.get("error", "")
    assert _state() == before
    _no_residue()


def test_precheck_missing_attachment_failed_precheck(users, base, make_quote, isolated_data):
    """备份校验失败（缺失应有附件）——也等同“校验后备份发生变化”在二次校验被拒。"""
    need_pg_tools()
    make_quote()
    from tests.test_rc2_r04_restore import _attach

    _attach(users, base)
    b = _backup(isolated_data / "bk")
    next((b / "private" / "attachments").rglob("*.png")).unlink()
    before = _state()
    with pytest.raises(CommandError):
        _restore(b, isolated_data)
    j = _journal()
    assert j["state"] == "FAILED_PRECHECK"
    assert _state() == before
    _no_residue()


def test_precheck_other_connections_failed_precheck(users, base, make_quote, isolated_data, monkeypatch):
    need_pg_tools()
    make_quote()
    b = _backup(isolated_data / "bk")
    before = _state()
    import apps.accounts.management.commands.ish_restore as R
    monkeypatch.setattr(R.pgops, "other_sessions", lambda *a, **k: 1)
    with pytest.raises(CommandError, match="其他连接"):
        _restore(b, isolated_data)
    j = _journal()
    assert j["state"] == "FAILED_PRECHECK" and "其他连接" in j.get("error", "")
    assert _state() == before
    _no_residue()


# ------------------------------------------------------------------ 切换前 / 切换后失败 → 已安全回退（ROLLED_BACK）
@pytest.mark.parametrize("stage", ["restore.pg_restore", "restore.copy_files", "restore.after_stage_verify", "restore.switch_db", "restore.switch_files", "restore.post_verify"])
def test_switch_before_or_after_failure_rolled_back(users, base, make_quote, isolated_data, monkeypatch, stage):
    need_pg_tools()
    (settings.DATA_DIR / "ALLOW_FAULT_INJECTION").write_text("t")
    make_quote()
    b = _backup(isolated_data / "bk")
    make_quote()
    before = _state()
    monkeypatch.setenv("ISH_FAULT_INJECT", stage)
    with pytest.raises(CommandError, match="已自动回退"):
        _restore(b, isolated_data)
    connection.close()
    j = _journal()
    assert j["state"] == "ROLLED_BACK", f"{stage}: {j['state']}"
    assert _state() == before
    assert not settings.MAINTENANCE_FLAG.exists()


# ------------------------------------------------------------------ 回退失败 → ROLLBACK_FAILED（保持保护）
def test_rollback_failure_reports_rollback_failed(users, base, make_quote, isolated_data, monkeypatch):
    """切换后失败触发回退，但回退时“旧库改回正式库”这一步失败 → ROLLBACK_FAILED（状态不确定，须保持停止、人工介入）。"""
    need_pg_tools()
    (settings.DATA_DIR / "ALLOW_FAULT_INJECTION").write_text("t")
    make_quote()
    b = _backup(isolated_data / "bk")
    import apps.accounts.management.commands.ish_restore as R

    real_rename = R.pgops.rename_db
    prod = R.pgops.prod_db()

    def flaky(src, dst, *a, **k):
        if str(src).startswith(prod + "_old_") and dst == prod:
            raise RuntimeError("模拟回退改名失败")
        return real_rename(src, dst, *a, **k)

    monkeypatch.setattr(R.pgops, "rename_db", flaky)
    monkeypatch.setenv("ISH_FAULT_INJECT", "restore.post_verify")  # 切换后失败 → 触发回退
    try:
        with pytest.raises(CommandError):
            _restore(b, isolated_data)
    finally:
        monkeypatch.undo()
        connection.close()
        # 修复数据库到可继续后续测试的状态：把旧库改回正式库，清理残留快照
        jj = _journal()
        old = jj.get("old_db")
        if old and R.pgops.db_exists(old) and not R.pgops.db_exists(prod):
            real_rename(old, prod)
        for n in R.pgops.psql(f"SELECT datname FROM pg_database WHERE datname LIKE '{prod}_rst_%' OR datname LIKE '{prod}_old_%'").splitlines():
            R.pgops.drop_db(n.strip())
    j = _journal()
    assert j["state"] == "ROLLBACK_FAILED"
    assert j.get("rollback_errors")
