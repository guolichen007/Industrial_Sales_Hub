"""RC3 收口 C4（D-5）：状态线是“项目总体状态”；多张有效合同/订单时明确提示，完结类阶段须人工确认已覆盖全部合同/批次并留痕。

反例（02b5464）：状态摘要无 scope/多合同标识；有 2 张有效订单时直接记录“已交付/完结”“已结清”不需任何确认。
"""
import pytest

from apps.audit.models import AuditEvent
from apps.projects.models import TrackEvent
from tests.test_v21_permissions_and_business import confirm

pytestmark = pytest.mark.django_db


def _two_orders(client_for, base):
    confirm(client_for, base, ref="D5-A")
    confirm(client_for, base, ref="D5-B")
    return base["p_sales"].id


def test_C4_summary_marks_project_scope_and_multi_order(client_for, base):
    confirm(client_for, base, ref="D5-ONE")
    pid = base["p_sales"].id
    t = client_for("sales").get(f"/api/projects/{pid}/").json()["tracks"]
    assert t["scope"] == "PROJECT" and t["active_order_count"] == 1 and t["multi_order"] is False
    confirm(client_for, base, ref="D5-TWO")
    for who in ("sales", "assistant", "finance", "leader"):
        t = client_for(who).get(f"/api/projects/{pid}/tracks/").json()["summary"]
        assert t["scope"] == "PROJECT" and t["active_order_count"] == 2 and t["multi_order"] is True, who


@pytest.mark.parametrize("track,code,who", [("DELIVERY", "COMPLETED", "assistant"), ("PAYMENT", "SETTLED", "finance")])
def test_C4_final_stage_requires_coverage_confirmation_when_multi_order(client_for, base, track, code, who):
    pid = _two_orders(client_for, base)
    c = client_for(who)
    r = c.post(f"/api/projects/{pid}/tracks/", {"track": track, "code": code}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "confirm_coverage_required" and "2 张" in r.json()["message"]
    assert not TrackEvent.objects.filter(project_id=pid, code=code).exists()
    # 非完结阶段不需确认
    mid = "IN_PRODUCTION" if track == "DELIVERY" else "PREPAID"
    assert c.post(f"/api/projects/{pid}/tracks/", {"track": track, "code": mid}, format="json").status_code == 201
    r = c.post(f"/api/projects/{pid}/tracks/", {"track": track, "code": code, "confirm_all_covered": True}, format="json")
    assert r.status_code == 201
    ev = TrackEvent.objects.get(project_id=pid, code=code)
    assert ev.covers_all_orders is True and ev.active_orders_at == 2 and ev.created_by.username == who and ev.created_at
    a = AuditEvent.objects.filter(resource="project", action="track_event").latest("id")
    assert a.detail["covers_all_orders"] is True and a.detail["active_orders"] == 2 and "覆盖全部 2 张" in a.summary and a.actor_name == who
    summ = client_for("sales").get(f"/api/projects/{pid}/").json()["tracks"]
    assert summ[track.lower()]["covers_all_orders"] is True


def test_C4_single_order_or_won_without_order_no_confirmation_needed(client_for, base):
    pid = base["p_sales"].id
    assert client_for("assistant").post(f"/api/projects/{pid}/handoff/", {}, format="json").status_code == 200  # 中标、尚无订单
    assert client_for("assistant").post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "COMPLETED"}, format="json").status_code == 201
    confirm(client_for, base, ref="D5-S")
    assert client_for("finance").post(f"/api/projects/{pid}/tracks/", {"track": "PAYMENT", "code": "SETTLED"}, format="json").status_code == 201
    assert not TrackEvent.objects.filter(covers_all_orders=True).exists()


def test_C4_sales_cannot_confirm_coverage(client_for, base):
    pid = _two_orders(client_for, base)
    assert client_for("sales").post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "COMPLETED", "confirm_all_covered": True}, format="json").status_code == 403
