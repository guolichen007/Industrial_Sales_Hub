"""RC3-G1：中标交接锁定销售写权限（T01–T05 + 撤销中标边界 + 订单联动 + 历史迁移）。

反例（RC2 baa9ef6，证据 docs/evidence/RC3/G1-before-baa9ef6.txt）：成交后销售 PATCH 项目 200、无 phase 售前跟进 201、
关联参与方 201、上传附件 201、详情 can_edit=True。
"""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.projects.models import Project, ProjectParty

pytestmark = pytest.mark.django_db
PNG = b"\x89PNG" + b"0" * 64


def _handoff(client_for, pid, who="assistant", **kw):
    r = client_for(who).post(f"/api/projects/{pid}/handoff/", {"note": "已中标，转商务", **kw}, format="json")
    assert r.status_code == 200, r.content
    return r.json()


@pytest.fixture
def won(client_for, base):
    pid = base["p_sales"].id
    att = client_for("sales").post("/api/attachments/", {"owner_type": "project", "owner_id": pid, "file": SimpleUploadedFile("售前图纸.png", PNG)}, format="multipart").json()
    party = client_for("sales").post(f"/api/projects/{pid}/parties/", {"account": base["accs"][0].id, "party_role": "主机厂"}, format="json").json()
    _handoff(client_for, pid)
    return {"pid": pid, "att": att, "party": party}


def test_T01_sales_cannot_patch_project_after_won(client_for, won):
    s = client_for("sales")
    for body in ({"name": "改名"}, {"description": "改说明"}, {"location": "改地址"}, {"presale_phase": "投标中"}):
        r = s.patch(f"/api/projects/{won['pid']}/", body, format="json")
        assert r.status_code == 403, (body, r.status_code)
        assert "中标" in r.json()["message"]
    assert Project.objects.get(pk=won["pid"]).name == "销售1的项目"


def test_T02_presale_update_rejected_with_or_without_phase(client_for, won):
    s = client_for("sales")
    for body in ({"stream": "PRESALE", "title": "无阶段跟进"}, {"stream": "PRESALE", "title": "x", "phase": ""}, {"stream": "PRESALE", "title": "x", "phase": "投标中"}):
        assert s.post(f"/api/projects/{won['pid']}/updates/", body, format="json").status_code == 403, body
    assert s.post(f"/api/projects/{won['pid']}/updates/", {"stream": "EXECUTION", "title": "x"}, format="json").status_code == 403


def test_T03_parties_and_attachments_write_rejected(client_for, won, base):
    s = client_for("sales")
    pid = won["pid"]
    assert s.post(f"/api/projects/{pid}/parties/", {"account": base["accs"][2].id, "party_role": "业主"}, format="json").status_code == 403
    assert s.delete(f"/api/projects/{pid}/parties/{won['party']['id']}/").status_code == 403
    assert ProjectParty.objects.filter(project_id=pid).count() == 1
    assert s.post("/api/attachments/", {"owner_type": "project", "owner_id": pid, "file": SimpleUploadedFile("x.png", PNG)}, format="multipart").status_code == 403
    assert s.delete(f"/api/attachments/{won['att']['id']}/").status_code == 403
    assert s.patch(f"/api/attachments/{won['att']['id']}/", {"klass": "PRESALE"}, format="json").status_code == 403


def test_T04_reads_still_work_and_can_edit_false(client_for, won):
    s = client_for("sales")
    pid = won["pid"]
    d = s.get(f"/api/projects/{pid}/")
    assert d.status_code == 200 and d.json()["can_edit"] is False and d.json()["sales_locked_at"]
    lst = s.get("/api/projects/").json()
    rows = lst["results"] if isinstance(lst, dict) else lst
    assert next(x for x in rows if x["id"] == pid)["can_edit"] is False
    assert s.get(f"/api/projects/{pid}/updates/").status_code == 200
    assert s.get(f"/api/projects/{pid}/milestones/").status_code == 200
    # 自己售前上传的资料仍可读
    assert [a["id"] for a in s.get(f"/api/attachments/?owner_type=project&owner_id={pid}").json()] == [won["att"]["id"]]
    assert s.get(f"/api/attachments/{won['att']['id']}/download/").status_code == 200
    body = d.content.decode()
    assert "contract_amount" not in body and "expected_amount_note" not in body


def test_T05_won_before_any_order_locks_sales(client_for, base):
    pid = base["p_sales"].id
    assert not base["p_sales"].orders.exists()
    data = _handoff(client_for, pid, won_at="2026-11-01")
    assert data["won_at"].startswith("2026-11-01") and data["presale_phase"] == "成交"
    assert client_for("sales").post(f"/api/projects/{pid}/updates/", {"stream": "PRESALE", "title": "x"}, format="json").status_code == 403
    assert client_for("sales").patch(f"/api/projects/{pid}/", {"name": "x"}, format="json").status_code == 403
    # 商务仍可代录补充信息（保留操作人与时间）
    r = client_for("assistant").patch(f"/api/projects/{pid}/", {"description": "商务代录：客户补充技术要求"}, format="json")
    assert r.status_code == 200 and r.json()["updated_by_name"] == "assistant"


def test_handoff_permissions_and_idempotency(client_for, base):
    pid = base["p_sales"].id
    for who in ("sales", "finance", "admin"):
        assert client_for(who).post(f"/api/projects/{pid}/handoff/", {}, format="json").status_code == 403
    a = _handoff(client_for, pid)
    b = _handoff(client_for, pid, who="leader")
    assert a["sales_locked_at"] == b["sales_locked_at"] and a["won_by_name"] == "assistant"
    assert AuditEvent.objects.filter(resource="project", action="handoff").count() == 1
    assert client_for("assistant").post(f"/api/projects/{pid}/handoff/", {"won_at": "2099-01-01"}, format="json").status_code == 200  # 已锁定：幂等返回，不改记录


def test_future_won_at_rejected(client_for, base):
    r = client_for("assistant").post(f"/api/projects/{base['p_sales'].id}/handoff/", {"won_at": "2099-01-01"}, format="json")
    assert r.status_code == 400


def test_revert_handoff_boundaries(client_for, base):
    pid = base["p_sales"].id
    _handoff(client_for, pid)
    for who in ("assistant", "sales", "finance"):
        assert client_for(who).post(f"/api/projects/{pid}/handoff/revert/", {"reason": "误操作"}, format="json").status_code == 403
    assert client_for("leader").post(f"/api/projects/{pid}/handoff/revert/", {}, format="json").status_code == 400  # 必须填原因
    assert client_for("leader").post(f"/api/projects/{pid}/handoff/revert/", {"reason": "x", "presale_phase": "成交"}, format="json").status_code == 400
    r = client_for("leader").post(f"/api/projects/{pid}/handoff/revert/", {"reason": "误标中标", "presale_phase": "投标中"}, format="json")
    assert r.status_code == 200 and r.json()["sales_locked_at"] is None and r.json()["presale_phase"] == "投标中"
    assert client_for("sales").patch(f"/api/projects/{pid}/", {"description": "恢复售前后可写"}, format="json").status_code == 200
    assert AuditEvent.objects.filter(resource="project", action="handoff_revert").exists()


def test_revert_refused_when_confirmed_order_exists(client_for, base):
    from tests.test_v21_permissions_and_business import confirm

    confirm(client_for, base, ref="W-1")
    p = Project.objects.get(pk=base["p_sales"].id)
    assert p.sales_locked_at and p.won_at and p.presale_phase == "成交"  # 订单确认自动记中标并锁定
    r = client_for("leader").post(f"/api/projects/{p.id}/handoff/revert/", {"reason": "x"}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "has_order"


def test_order_revoke_does_not_unlock_sales(client_for, base):
    from tests.test_v21_permissions_and_business import confirm

    o = confirm(client_for, base, ref="W-2")
    client_for("leader").post(f"/api/order-confirmations/{o['id']}/revoke/", {"reason": "合同作废"}, format="json")
    assert client_for("sales").patch(f"/api/projects/{base['p_sales'].id}/", {"name": "x"}, format="json").status_code == 403


def test_member_sales_also_locked_and_other_projects_unaffected(client_for, base, users):
    p = base["p_other"]
    p.members.add(users["sales"])
    _handoff(client_for, p.id)
    assert client_for("sales").patch(f"/api/projects/{p.id}/", {"name": "x"}, format="json").status_code == 403
    assert client_for("sales").patch(f"/api/projects/{base['p_sales'].id}/", {"description": "未中标的项目照常可写"}, format="json").status_code == 200


def test_migration_locks_historical_won_projects(base, users):
    """历史数据迁移：直接调用迁移函数（与 0003 迁移相同逻辑）。"""
    import importlib

    from django.apps import apps as global_apps

    mig = importlib.import_module("apps.projects.migrations.0003_rc3_g1_handoff")
    p = base["p_sales"]
    p.presale_phase = "成交"
    p.sales_locked_at = None
    p.save()
    other = base["p_other"]
    mig.lock_existing_won(global_apps, None)
    p.refresh_from_db()
    other.refresh_from_db()
    assert p.sales_locked_at is not None and p.won_at is not None and "迁移" in p.won_note
    assert other.sales_locked_at is None
    assert p.won_at <= timezone.now()
