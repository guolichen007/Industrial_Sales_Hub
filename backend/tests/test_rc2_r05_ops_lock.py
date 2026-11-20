"""RC2-R02/R05：空 private 备份、运维全局互斥、带 owner token 的维护标记。

反例（RC1）：
- R02：系统没有任何 PDF/附件时，备份目录中不存在 private/；恢复脚本无条件复制 backup/private → 失败。
- R05：两个备份（或备份+升级）同时运行，各自写同一个 DATA_DIR/MAINTENANCE，先结束的一方在 finally 中
       删除了另一方仍在使用的维护标记，导致后者的维护窗口内写入被放行。
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.test_v21_backup_restore import isolated_data, need_pg_tools  # noqa: F401  (fixture 复用)

pytestmark = pytest.mark.django_db(transaction=True)


def test_empty_system_backup_has_empty_private_dir(users, isolated_data):
    need_pg_tools()
    assert not settings.PRIVATE_STORAGE_DIR.exists() or not any(settings.PRIVATE_STORAGE_DIR.rglob("*"))
    call_command("ish_backup", dest=str(isolated_data / "bk"))
    b = next((isolated_data / "bk").glob("ish-backup-*"))
    assert (b / "private").is_dir(), "空系统备份也必须包含（空的）private 目录"
    man = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
    assert man["files"] == [] and man["format"] >= 2
    call_command("ish_verify_backup", str(b))


def test_legacy_format1_backup_without_private_still_verifies(users, isolated_data):
    """兼容 RC1 生成的空备份（format=1，没有 private 目录）：校验通过，恢复时建立空目录。"""
    need_pg_tools()
    call_command("ish_backup", dest=str(isolated_data / "bk"))
    b = next((isolated_data / "bk").glob("ish-backup-*"))
    # 改造成 RC1 形态：删除空 private、manifest format=1、重写校验和
    (b / "private").rmdir()
    from apps.accounts.management.commands.ish_backup import sha256_file

    man = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
    man["format"] = 1
    man.pop("ops", None)
    (b / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    sums = [f"{sha256_file(f)}  {f.relative_to(b).as_posix()}" for f in sorted(b.rglob("*")) if f.is_file() and f.name != "SHA256SUMS"]
    (b / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    call_command("ish_verify_backup", str(b))
    call_command("ish_verify_backup", str(b), restore_to_db="ish_legacy_drill", restore_data_dir=str(isolated_data / "legacy restore"))


def test_ops_lock_is_exclusive_and_busy_exits_cleanly(isolated_data):
    from apps.common.opslock import OpsBusy, ops_lock

    with ops_lock("backup", wait=0) as tok:
        assert (settings.DATA_DIR / "ops.lock").exists()
        # 同一调用链内嵌套（例如恢复内部调用备份）复用同一 token
        with ops_lock("backup-nested", wait=0) as inner:
            assert inner == tok
        # 另一个线程（独立的运维任务）必须遇忙
        err = {}

        def other():
            try:
                with ops_lock("upgrade", wait=0):
                    err["got"] = True
            except OpsBusy as e:
                err["busy"] = str(e)

        t = threading.Thread(target=other)
        t.start()
        t.join()
        assert "got" not in err and "backup" in err["busy"]
        info = json.loads((settings.DATA_DIR / "ops.owner.json").read_text(encoding="utf-8"))
        assert info["token"] == tok and info["task"] == "backup" and info["pid"] == os.getpid()
    with ops_lock("upgrade", wait=0):
        pass


def test_ops_lock_is_cross_process(isolated_data):
    """另一个进程持锁时，本进程拿不到锁；对方退出（含崩溃）后锁自动释放。"""
    from apps.common.opslock import OpsBusy, ops_lock

    code = (
        "import sys,time,django,os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup();"
        "from django.conf import settings;from pathlib import Path;settings.DATA_DIR=Path(sys.argv[1]);"
        "from apps.common.opslock import ops_lock\n"
        "with ops_lock('restore', wait=0):\n print('LOCKED', flush=True); time.sleep(30)\n"
    )
    p = subprocess.Popen([sys.executable, "-c", code, str(settings.DATA_DIR)], cwd=str(settings.BASE_DIR), stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "LOCKED"
        with pytest.raises(OpsBusy, match="restore"):
            with ops_lock("backup", wait=0):
                pass
    finally:
        p.kill()  # 模拟崩溃：不走 finally
        p.wait()
    with ops_lock("backup", wait=5):
        pass


def test_maintenance_flag_owner_token(isolated_data):
    from apps.common.opslock import clear_maintenance, maintenance_owner, set_maintenance

    t1 = set_maintenance("backup")
    assert settings.MAINTENANCE_FLAG.exists() and maintenance_owner()["token"] == t1
    assert clear_maintenance("not-the-owner") is False
    assert settings.MAINTENANCE_FLAG.exists(), "不能删除别人的维护标记"
    assert clear_maintenance(t1) is True and not settings.MAINTENANCE_FLAG.exists()


def test_concurrent_backups_never_remove_each_others_window(users, base, isolated_data):
    """备份 A 运行中触发备份 B 与升级：B/升级必须遇忙退出，且不得删除 A 的维护标记；A 完成后标记才消失。"""
    need_pg_tools()
    from apps.common.opslock import ops_lock

    events = {}

    def slow_a():
        # 用 ops 锁 + 维护标记模拟一个长时间运行的备份 A
        from apps.common.opslock import clear_maintenance, set_maintenance

        with ops_lock("backup", wait=0):
            tok = set_maintenance("backup-A")
            events["a_started"] = time.time()
            time.sleep(2)
            events["flag_during_a"] = settings.MAINTENANCE_FLAG.exists()
            clear_maintenance(tok)

    ta = threading.Thread(target=slow_a)
    ta.start()
    while "a_started" not in events:
        time.sleep(0.05)
    with pytest.raises(CommandError, match="正在进行"):
        call_command("ish_backup", dest=str(isolated_data / "bkB"), lock_wait=0)
    assert settings.MAINTENANCE_FLAG.exists(), "B 失败退出时不得删除 A 的维护标记"
    with pytest.raises(CommandError, match="正在进行"):
        call_command("ish_upgrade", backup_dest=str(isolated_data / "bkU"))
    with pytest.raises(CommandError, match="正在进行"):
        call_command("ish_restore", str(isolated_data), confirm_production=True)
    assert settings.MAINTENANCE_FLAG.exists()
    ta.join()
    assert events["flag_during_a"] is True
    assert not settings.MAINTENANCE_FLAG.exists()
    assert not list((isolated_data / "bkB").glob("ish-backup-*"))
    # 忙退出后，上一次成功状态不被覆盖为失败（计划任务遇忙安全退出）
    st = settings.BACKUP_STATUS_FILE
    if st.exists():
        assert json.loads(st.read_text(encoding="utf-8")).get("ok") is not False or "busy" in st.read_text(encoding="utf-8")


def test_backup_does_not_remove_foreign_flag_on_failure(users, isolated_data):
    """维护标记已由他人（例如升级器）放置：备份应拒绝启动，且不删除该标记。"""
    need_pg_tools()
    from apps.common.opslock import set_maintenance

    set_maintenance("upgrade-in-progress")
    with pytest.raises(CommandError, match="维护"):
        call_command("ish_backup", dest=str(isolated_data / "bk"))
    assert settings.MAINTENANCE_FLAG.exists()


def test_status_files_are_per_task_and_atomic(users, isolated_data):
    need_pg_tools()
    call_command("ish_backup", dest=str(isolated_data / "bk"))
    st = json.loads(settings.BACKUP_STATUS_FILE.read_text(encoding="utf-8"))
    assert st["ok"] is True and st["task_token"]
    assert not list(settings.DATA_DIR.glob("*.tmp"))
