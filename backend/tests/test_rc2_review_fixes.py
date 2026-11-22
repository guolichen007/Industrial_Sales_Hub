"""RC2 独立复审发现的问题回归测试（每项先在修复前代码上失败，修复后通过）。"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from apps.common import pgops
from tests.test_rc2_r04_restore import _attach, _backup, _cleanup_old_dbs, _journal, _state  # noqa: F401
from tests.test_v21_backup_restore import isolated_data, need_pg_tools  # noqa: F401

PNG = b"\x89PNG" + b"0" * 64


@pytest.fixture(autouse=True)
def _drop_side_dbs():
    yield
    if connection.vendor == "postgresql":
        connection.close()
        _cleanup_old_dbs()
        prod = pgops.prod_db()
        for n in pgops.psql(f"SELECT datname FROM pg_database WHERE datname LIKE '{prod}_undone_%'").splitlines():
            pgops.drop_db(n.strip())


# ---------- P0-1：恢复前的安全备份不得清理旧备份（包括正要恢复的那一份）
@pytest.mark.django_db(transaction=True)
def test_safety_backup_never_prunes_old_backups(users, base, make_quote, isolated_data):
    need_pg_tools()
    make_quote()
    dest = isolated_data / "bk"
    old = _backup(dest)
    t = (datetime.now() - timedelta(days=45)).timestamp()
    os.utime(old, (t, t))  # 45 天前的备份（默认保留 30 天）
    call_command("ish_restore", str(old), confirm_production=True, backup_dest=str(dest))
    assert old.exists(), "安全备份不得清理正要恢复的旧备份"
    # 计划备份的清理也不删除 before-restore/pre-upgrade 安全备份
    safety = next(dest.glob("ish-backup-*-before-restore-*"))
    os.utime(safety, (t, t))
    call_command("ish_backup", dest=str(dest), keep_days=30)
    assert safety.exists() and not old.exists()  # 普通过期备份照常清理


# ---------- P0-2：撤销“已完成”的恢复，不得删除恢复之后录入的数据
@pytest.mark.django_db(transaction=True)
def test_rollback_of_done_restore_keeps_post_restore_data(users, base, make_quote, isolated_data):
    need_pg_tools()
    b = _backup(isolated_data / "bk")
    make_quote()
    call_command("ish_restore", str(b), confirm_production=True, skip_safety_backup=True)
    # 恢复之后又录入了新业务
    _attach(users, base, name="恢复后上传.png")
    after = _state()
    rid = _journal()["id"]
    call_command("ish_restore", rollback=rid)
    connection.close()
    j = _journal()
    kept_db, kept_dir = j.get("undone_db"), j.get("undone_private")
    assert kept_db and pgops.db_exists(kept_db), "恢复后录入的数据库必须改名保留"
    assert kept_dir and Path(kept_dir).exists() and any(Path(kept_dir).rglob("*.png"))
    assert after[1] != _state()[1]


@pytest.mark.django_db(transaction=True)
def test_rollback_after_cleanup_is_refused(users, base, isolated_data):
    need_pg_tools()
    b = _backup(isolated_data / "bk")
    call_command("ish_restore", str(b), confirm_production=True, skip_safety_backup=True)
    rid = _journal()["id"]
    call_command("ish_restore", cleanup=rid)
    live_before = settings.PRIVATE_STORAGE_DIR.exists()
    with pytest.raises(CommandError, match="cleanup"):
        call_command("ish_restore", rollback=rid)
    assert settings.PRIVATE_STORAGE_DIR.exists() == live_before and pgops.db_exists(pgops.prod_db())


# ---------- P1-4：意图先于动作；动作未发生时撤销跳过、已发生时撤销（崩溃/日志写失败）
@pytest.mark.django_db(transaction=True)
def test_rollback_is_idempotent_by_actual_state(users, base, make_quote, isolated_data, monkeypatch):
    need_pg_tools()
    make_quote()
    b = _backup(isolated_data / "bk")
    make_quote()
    before = _state()
    import apps.accounts.management.commands.ish_restore as R

    real = R.pgops.rename_db
    calls = {"n": 0}

    def flaky(old, new, pg_bin=None):
        calls["n"] += 1
        real(old, new, pg_bin)
        if calls["n"] == 1:
            raise RuntimeError("模拟：改名成功后进程/日志异常")

    monkeypatch.setattr(R.pgops, "rename_db", flaky)
    with pytest.raises(CommandError, match="已自动回退"):
        call_command("ish_restore", str(b), confirm_production=True, skip_safety_backup=True)
    connection.close()
    assert _state() == before and pgops.db_exists(pgops.prod_db())
    assert _journal()["state"] == "ROLLED_BACK"


# ---------- P0-3：销售即使能编辑项目，也看不到商务上传的内部附件
@pytest.mark.django_db
def test_sales_editor_cannot_see_internal_project_attachments(client_for, base):
    pid = base["p_sales"].id  # 销售1 负责的项目
    internal = client_for("assistant").post("/api/attachments/", {"owner_type": "project", "owner_id": pid, "file": SimpleUploadedFile("内部比价.png", PNG)}, format="multipart").json()
    mine = client_for("sales").post("/api/attachments/", {"owner_type": "project", "owner_id": pid, "file": SimpleUploadedFile("现场照片.png", PNG)}, format="multipart").json()
    s = client_for("sales")
    ids = [a["id"] for a in s.get(f"/api/attachments/?owner_type=project&owner_id={pid}").json()]
    assert ids == [mine["id"]]
    assert s.get(f"/api/attachments/{internal['id']}/download/").status_code == 403
    assert s.delete(f"/api/attachments/{internal['id']}/").status_code == 403
    assert s.get(f"/api/attachments/{mine['id']}/download/").status_code == 200
    assert client_for("assistant").get(f"/api/attachments/{internal['id']}/download/").status_code == 200


@pytest.mark.django_db
def test_classification_patch_only_by_leader_or_assistant(client_for, base, make_quote):
    pid = base["p_sales"].id
    asst = client_for("assistant")
    d = asst.post("/api/attachments/", {"owner_type": "project", "owner_id": pid, "klass": "DELIVERY", "file": SimpleUploadedFile("发货.png", PNG)}, format="multipart").json()
    asst.patch(f"/api/attachments/{d['id']}/", {"sales_visible": True}, format="json")
    # 销售（项目编辑者）不能撤回授权；非字符串 klass 不再 500
    assert client_for("sales").patch(f"/api/attachments/{d['id']}/", {"sales_visible": False}, format="json").status_code == 403
    assert asst.patch(f"/api/attachments/{d['id']}/", {"klass": ["X"]}, format="json").status_code == 400
    # 财务不能借 PATCH 探测订单附件
    from apps.contracts.models import OrderConfirmation

    o = OrderConfirmation.objects.create(project=base["p_sales"], legal_entity=base["ents"][0], basis="CONTRACT", order_ref="HT-X")
    oa = client_for("leader").post("/api/attachments/", {"owner_type": "order", "owner_id": o.id, "file": SimpleUploadedFile("合同.png", PNG)}, format="multipart").json()
    r = client_for("finance").patch(f"/api/attachments/{oa['id']}/", {}, format="json")
    assert r.status_code == 403 and "合同" not in r.content.decode()


# ---------- P2：财务不能把报价关联到订单；试用报价不会被订单确认改为已接受
@pytest.mark.django_db
def test_finance_cannot_bind_quote_to_order(client_for, base, make_quote, settings):
    # RC3 收口 D-2（2026-11-22 负责人决策）：财务默认使用报价中心；RC2 口径（ISH_FINANCE_SCOPE=RC2）下仍 403——RC3 默认下财务可关联报价，但仍不能录入合同金额（见 test_rc3_closeout_c1）
    settings.FINANCE_SCOPE, settings.FINANCE_QUOTE = "RC2", False
    qid, _ = make_quote()
    r = client_for("finance").post("/api/order-confirmations/", {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "quote": qid, "basis": "CONTRACT", "order_ref": "HT-F1"}, format="json")
    assert r.status_code == 403


@pytest.mark.django_db
def test_order_confirm_does_not_accept_quote_whose_latest_is_trial(client_for, base, make_quote, users):
    from apps.organizations.models import TemplateSignoff
    from apps.quotations.models import Quote

    qid, rid = make_quote()  # V1 正式
    c = client_for("assistant")
    TemplateSignoff.objects.filter(entity=base["ents"][0]).delete()
    nv = c.post(f"/api/quotations/{qid}/revisions/").json()
    assert c.post(f"/api/quote-revisions/{nv['id']}/render/", {"trial": True}, format="json").status_code == 200  # V2 试用
    r = client_for("leader").post("/api/order-confirmations/", {"project": base["p_sales"].id, "quote_revision": rid, "basis": "CONTRACT", "order_ref": "HT-T2", "confirm_now": True}, format="json")
    assert r.status_code in (200, 201), r.content
    assert Quote.objects.get(pk=qid).status != "ACCEPTED"


@pytest.mark.django_db
def test_trial_request_on_signed_template_is_rejected(client_for, base, make_quote):
    _, rid = make_quote(render=False)
    r = client_for("assistant").post(f"/api/quote-revisions/{rid}/render/", {"trial": True}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "already_signed"
