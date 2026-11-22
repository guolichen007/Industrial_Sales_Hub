"""对 74616ed 独立审查（2026-11-22）的反例：P0-01 阶段终态元数据篡改绕过多合同完结确认；P1-02 完结后订单变更使覆盖确认失效；
P1-03 阶段改名改写历史事件展示名。修复前均失败（见 docs/evidence/RC3/R74-before-74616ed.txt）。"""
import pytest

from apps.audit.models import AuditEvent
from apps.projects.models import StageDef, TrackEvent
from tests.test_v21_permissions_and_business import confirm

pytestmark = pytest.mark.django_db


def _two_orders(client_for, base):
    a = confirm(client_for, base, ref="R74-A")
    b = confirm(client_for, base, ref="R74-B")
    return base["p_sales"].id, a, b


# ------------------------------------------------------------ P0-01
@pytest.mark.parametrize("track,code,who", [("DELIVERY", "COMPLETED", "assistant"), ("PAYMENT", "SETTLED", "finance")])
def test_P0_01_is_final_tamper_cannot_bypass_coverage(client_for, base, track, code, who):
    pid, *_ = _two_orders(client_for, base)
    sd = StageDef.objects.get(track=track, code=code)
    for cfg in ("leader", "assistant"):  # 拥有 stage.config 的角色
        r = client_for(cfg).patch(f"/api/stage-defs/{sd.id}/", {"is_final": False}, format="json")
        assert r.status_code == 400, (cfg, r.status_code, r.content[:200])
    sd.refresh_from_db()
    assert sd.is_final is True
    # 篡改失败后，不确认覆盖仍被拒绝
    r = client_for(who).post(f"/api/projects/{pid}/tracks/", {"track": track, "code": code}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "confirm_coverage_required"
    assert not TrackEvent.objects.filter(project_id=pid, code=code).exists()


def test_P0_01_new_stage_cannot_be_created_final(client_for, base):
    a = client_for("assistant")
    r = a.post("/api/stage-defs/", {"track": "DELIVERY", "code": "HANDOVER_DONE", "label": "移交完成", "sort": 99, "is_final": True}, format="json")
    assert r.status_code == 400
    r = a.post("/api/stage-defs/", {"track": "DELIVERY", "code": "HANDOVER_DONE", "label": "移交完成", "sort": 99}, format="json")
    assert r.status_code == 201 and r.json()["is_final"] is False
    # 允许的配置项仍可修改，并写审计
    sd = StageDef.objects.get(track="DELIVERY", code="COMPLETED")
    assert a.patch(f"/api/stage-defs/{sd.id}/", {"label": "已交付", "sort": 70, "is_final": True}, format="json").status_code == 200  # 提交与当前值相同的 is_final 不视为修改
    assert AuditEvent.objects.filter(resource="stage_def", action="update").exists()


# ------------------------------------------------------------ P1-02
def test_P1_02_order_change_after_final_marks_coverage_stale(client_for, base):
    pid = base["p_sales"].id
    o1 = confirm(client_for, base, ref="R74-1")
    a = client_for("assistant")
    assert a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "COMPLETED"}, format="json").status_code == 201  # 单合同：无需确认
    s = client_for("sales").get(f"/api/projects/{pid}/").json()["tracks"]
    assert s["delivery"]["coverage_stale"] is False
    confirm(client_for, base, ref="R74-2")  # 完结之后新确认第二张合同
    for who in ("sales", "assistant", "finance", "leader"):
        t = client_for(who).get(f"/api/projects/{pid}/tracks/").json()["summary"]
        assert t["delivery"]["coverage_stale"] is True and t["needs_reconfirmation"] is True, who
    # 重新确认覆盖后恢复
    assert a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "COMPLETED", "confirm_all_covered": True}, format="json").status_code == 201
    t = client_for("sales").get(f"/api/projects/{pid}/").json()["tracks"]
    assert t["delivery"]["coverage_stale"] is False and t["needs_reconfirmation"] is False and t["delivery"]["covers_all_orders"] is True


def test_P1_02_revoke_and_replace_same_count_is_stale(client_for, base):
    pid, o1, o2 = _two_orders(client_for, base)
    f = client_for("finance")
    assert f.post(f"/api/projects/{pid}/tracks/", {"track": "PAYMENT", "code": "SETTLED", "confirm_all_covered": True}, format="json").status_code == 201
    assert client_for("sales").get(f"/api/projects/{pid}/").json()["tracks"]["payment"]["coverage_stale"] is False
    assert client_for("leader").post(f"/api/order-confirmations/{o2['id']}/revoke/", {"reason": "录错"}, format="json").status_code == 200
    confirm(client_for, base, ref="R74-C")  # 订单数仍为 2，但成员已变
    t = client_for("sales").get(f"/api/projects/{pid}/").json()["tracks"]
    assert t["active_order_count"] == 2 and t["payment"]["coverage_stale"] is True and t["needs_reconfirmation"] is True


def test_P1_02_non_final_and_no_change_not_stale(client_for, base):
    pid, *_ = _two_orders(client_for, base)
    a = client_for("assistant")
    a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "IN_PRODUCTION"}, format="json")
    confirm(client_for, base, ref="R74-3")
    t = client_for("sales").get(f"/api/projects/{pid}/").json()["tracks"]
    assert t["delivery"]["coverage_stale"] is False and t["needs_reconfirmation"] is False  # 非终态不涉及覆盖确认


# ------------------------------------------------------------ P1-03
def test_P1_03_history_keeps_snapshot_label_after_rename(client_for, base):
    pid = base["p_sales"].id
    a = client_for("assistant")
    a.post(f"/api/projects/{pid}/handoff/", {}, format="json")
    a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "IN_PRODUCTION"}, format="json")
    sd = StageDef.objects.get(track="DELIVERY", code="IN_PRODUCTION")
    assert a.patch(f"/api/stage-defs/{sd.id}/", {"label": "排产与制造中"}, format="json").status_code == 200
    body = client_for("sales").get(f"/api/projects/{pid}/tracks/").json()
    ev = [e for e in body["events"] if e["code"] == "IN_PRODUCTION"][0]
    assert ev["label"] == "生产中" and ev["label_snapshot"] == "生产中"  # 历史：当时的名称
    assert ev["current_label"] == "排产与制造中"
    assert body["summary"]["delivery"]["label"] == "排产与制造中"  # 当前状态：最新展示名


def test_P1_02_migration_backfills_covered_order_ids(client_for, base):
    import importlib

    from django.apps import apps as global_apps

    pid, o1, o2 = _two_orders(client_for, base)
    client_for("finance").post(f"/api/projects/{pid}/tracks/", {"track": "PAYMENT", "code": "SETTLED", "confirm_all_covered": True}, format="json")
    TrackEvent.objects.update(covered_order_ids=[])  # 模拟 74616ed 时期的旧事件（无此字段）
    importlib.import_module("apps.projects.migrations.0006_rc3_r74_covered_orders").backfill(global_apps, None)
    assert TrackEvent.objects.get(project_id=pid, code="SETTLED").covered_order_ids == sorted([o1["id"], o2["id"]])
    assert client_for("sales").get(f"/api/projects/{pid}/").json()["tracks"]["payment"]["coverage_stale"] is False
