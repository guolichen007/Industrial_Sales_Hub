"""L4：一致性备份、恢复演练、坏备份拒绝、维护模式（PostgreSQL 真实执行，调用 pg_dump/pg_restore）。"""
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db(transaction=True)


def need_pg_tools():
    if connection.vendor != "postgresql" or not shutil.which("pg_dump"):
        pytest.skip("需要 PostgreSQL 与 pg_dump/pg_restore 在 PATH 中")


@pytest.fixture
def isolated_data(tmp_path, settings):
    settings.DATA_DIR = tmp_path / "data"
    settings.DATA_DIR.mkdir()
    settings.PRIVATE_STORAGE_DIR = settings.DATA_DIR / "private"
    settings.MAINTENANCE_FLAG = settings.DATA_DIR / "MAINTENANCE"
    settings.BACKUP_STATUS_FILE = settings.DATA_DIR / "backup_status.json"
    return tmp_path


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_maintenance_flag_blocks_writes_not_reads(client_for, base, isolated_data):
    settings.MAINTENANCE_FLAG.write_text("x")
    c = client_for("sales")
    assert c.get("/api/projects/").status_code == 200
    r = c.post("/api/projects/", {"name": "维护中"}, format="json")
    assert r.status_code == 503 and r.json()["code"] == "maintenance"
    settings.MAINTENANCE_FLAG.unlink()
    assert c.post("/api/projects/", {"name": "恢复后"}, format="json").status_code == 201


def test_backup_restore_roundtrip_with_concurrent_writers(users, base, make_quote, isolated_data):
    need_pg_tools()
    qid, rid = make_quote()
    from django.core.files.uploadedfile import SimpleUploadedFile

    _client(users["sales"]).post("/api/attachments/", {"owner_type": "project", "owner_id": base["p_sales"].id, "file": SimpleUploadedFile("现场 照片.png", b"\x89PNG" + b"0" * 2000)}, format="multipart")
    dest = isolated_data / "备份 目录"  # 中文含空格路径
    stop = threading.Event()
    stats = {"ok": 0, "503": 0, "other": []}

    def writer():
        c = _client(users["sales"])
        while not stop.is_set():
            r = c.post(f"/api/projects/{base['p_sales'].id}/updates/", {"stream": "PRESALE", "title": "并发写入"}, format="json")
            if r.status_code == 201:
                stats["ok"] += 1
            elif r.status_code == 503:
                stats["503"] += 1
            else:
                stats["other"].append(r.status_code)
            time.sleep(0.01)
        connection.close()

    ts = [threading.Thread(target=writer) for _ in range(3)]
    [t.start() for t in ts]
    time.sleep(0.3)
    try:
        call_command("ish_backup", dest=str(dest), keep_days=30)
    finally:
        stop.set()
        [t.join() for t in ts]
    assert not stats["other"], stats
    assert stats["ok"] > 0
    assert not settings.MAINTENANCE_FLAG.exists(), "备份结束后必须解除维护"
    status = json.loads(settings.BACKUP_STATUS_FILE.read_text(encoding="utf-8"))
    assert status["ok"] is True
    backups = sorted(dest.glob("ish-backup-*"))
    assert len(backups) == 1 and not list(dest.glob(".tmp-*"))
    b = backups[0]
    man = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
    assert len(man["files"]) == 2  # 报价 PDF + 附件
    # 恢复演练：到新临时库 + 新临时目录，完整性与行数一致
    call_command("ish_verify_backup", str(b), restore_to_db="ish_restore_drill", restore_data_dir=str(isolated_data / "restore tmp"))
    assert not (isolated_data / "restore tmp").exists()  # 演练后清理


def _make_backup(users, make_quote, isolated_data):
    make_quote()
    dest = isolated_data / "bk"
    call_command("ish_backup", dest=str(dest))
    return next(dest.glob("ish-backup-*"))


@pytest.mark.parametrize("damage", ["tamper_pdf", "delete_pdf", "extra_file", "bad_manifest", "truncate_dump", "missing_sums"])
def test_bad_backups_are_rejected(users, base, make_quote, isolated_data, damage):
    need_pg_tools()
    b = _make_backup(users, make_quote, isolated_data)
    pdf = next((b / "private").rglob("*.pdf"))
    if damage == "tamper_pdf":
        pdf.write_bytes(pdf.read_bytes() + b"x")
    elif damage == "delete_pdf":
        pdf.unlink()
    elif damage == "extra_file":
        (b / "private" / "evil.txt").write_text("x")
    elif damage == "bad_manifest":
        (b / "manifest.json").write_text("{not json")
    elif damage == "truncate_dump":
        d = b / "db.dump"
        d.write_bytes(d.read_bytes()[:100])
    elif damage == "missing_sums":
        (b / "SHA256SUMS").unlink()
    with pytest.raises(CommandError):
        call_command("ish_verify_backup", str(b))


def test_restore_refuses_production_targets(users, base, make_quote, isolated_data):
    need_pg_tools()
    b = _make_backup(users, make_quote, isolated_data)
    prod = settings.DATABASES["default"]["NAME"]
    with pytest.raises(CommandError, match="禁止恢复到当前正式数据库"):
        call_command("ish_verify_backup", str(b), restore_to_db=prod, restore_data_dir=str(isolated_data / "r1"))
    with pytest.raises(CommandError, match="禁止恢复到当前正式数据目录"):
        call_command("ish_verify_backup", str(b), restore_to_db="ish_r2", restore_data_dir=str(settings.DATA_DIR))
    with pytest.raises(CommandError):
        call_command("ish_verify_backup", str(b), restore_to_db="bad-name;drop", restore_data_dir=str(isolated_data / "r3"))


def test_restore_detects_wrong_contents(users, base, make_quote, isolated_data):
    """篡改 manifest 中的表行数并同步 SHA256SUMS：恢复后完整性检查必须发现不一致。"""
    need_pg_tools()
    from apps.accounts.management.commands.ish_backup import sha256_file

    b = _make_backup(users, make_quote, isolated_data)
    man = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
    man["table_counts"]["projects.Project"] += 5
    (b / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    lines = [l for l in (b / "SHA256SUMS").read_text().splitlines() if not l.endswith("  manifest.json")]
    lines.append(f"{sha256_file(b / 'manifest.json')}  manifest.json")
    (b / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    with pytest.raises(CommandError, match="完整性检查失败"):
        call_command("ish_verify_backup", str(b), restore_to_db="ish_r4", restore_data_dir=str(isolated_data / "r4"))


def test_backup_failure_keeps_previous_and_lifts_maintenance(users, base, make_quote, isolated_data):
    need_pg_tools()
    b1 = _make_backup(users, make_quote, isolated_data)
    with pytest.raises(CommandError):
        call_command("ish_backup", dest=str(isolated_data / "bk"), pg_bin=str(isolated_data / "no-such-bin"))
    assert b1.exists() and not settings.MAINTENANCE_FLAG.exists()
    assert not list((isolated_data / "bk").glob(".tmp-*"))


def test_backup_waits_for_inflight_write(users, base, isolated_data):
    """排他锁必须等待已进入的写事务：在一个持有共享锁的事务内写入，备份应等待其提交后才开始。"""
    need_pg_tools()
    from django.db import connections, transaction

    from apps.projects.models import Project

    started = threading.Event()
    done = {}

    def long_writer():
        with transaction.atomic():
            with connection.cursor() as c:
                c.execute("SELECT pg_advisory_xact_lock_shared(%s)", [settings.ADVISORY_LOCK_KEY])
            Project.objects.create(code="PRJ-INFLIGHT", name="进行中的写入")
            started.set()
            time.sleep(1.5)
        done["commit"] = time.time()
        connection.close()

    t = threading.Thread(target=long_writer)
    t.start()
    started.wait(5)
    t0 = time.time()
    call_command("ish_backup", dest=str(isolated_data / "bk2"))
    t.join()
    assert time.time() - t0 >= 1.0
    b = next((isolated_data / "bk2").glob("ish-backup-*"))
    man = json.loads((b / "manifest.json").read_text(encoding="utf-8"))
    assert man["waited_for_writers_seconds"] >= 1.0
    assert man["table_counts"]["projects.Project"] == Project.objects.count()  # 进行中的写入已包含
