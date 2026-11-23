"""对 6912049 第二轮独立审查（2026-11-23）的反例：R75-01 单合同撤销/替换后完结确认未失效，且可不确认直接重记终态；
R75-02 历史多合同未确认终态经迁移 0006 回填后被视为有效；R75-03 自定义阶段与正式完结阶段同名。修复前结果见 docs/evidence/RC3/R75-before-6912049.txt。"""
import importlib

import pytest
from django.apps import apps as global_apps

from apps.projects.models import StageDef, TrackEvent
from tests.test_v21_permissions_and_business import confirm

pytestmark = pytest.mark.django_db
FINAL = {"DELIVERY": ("COMPLETED", "assistant"), "PAYMENT": ("SETTLED", "finance")}


def _summ(client_for, pid, who="sales"):
    return client_for(who).get(f"/api/projects/{pid}/").json()["tracks"]


def _revoke(client_for, o):
    assert client_for("leader").post(f"/api/order-confirmations/{o['id']}/revoke/", {"reason": "反例"}, format="json").status_code == 200


# ------------------------------------------------------------ R75-01
@pytest.mark.parametrize("track", ["DELIVERY", "PAYMENT"])
@pytest.mark.parametrize("case", ["1to0", "1to_other1", "1to2", "2_replace"])
def test_R75_01_any_order_set_change_marks_stale(client_for, base, track, case):
    code, who = FINAL[track]
    pid = base["p_sales"].id
    a = confirm(client_for, base, ref=f"R75-{case}-A")
    b = confirm(client_for, base, ref=f"R75-{case}-B") if case == "2_replace" else None
    body = {"track": track, "code": code, **({"confirm_all_covered": True} if b else {})}
    assert client_for(who).post(f"/api/projects/{pid}/tracks/", body, format="json").status_code == 201
    assert _summ(client_for, pid)[track.lower()]["coverage_stale"] is False
    if case == "1to0":
        _revoke(client_for, a)
    elif case == "1to_other1":
        _revoke(client_for, a)
        confirm(client_for, base, ref=f"R75-{case}-C")
    elif case == "1to2":
        confirm(client_for, base, ref=f"R75-{case}-C")
    else:
        _revoke(client_for, b)
        confirm(client_for, base, ref=f"R75-{case}-C")
    t = _summ(client_for, pid)
    assert t[track.lower()]["coverage_stale"] is True and t["needs_reconfirmation"] is True, (case, t)


@pytest.mark.parametrize("track", ["DELIVERY", "PAYMENT"])
def test_R75_01_stale_requires_confirmation_to_rerecord(client_for, base, track):
    code, who = FINAL[track]
    pid = base["p_sales"].id
    a = confirm(client_for, base, ref="R75-RR-A")
    c = client_for(who)
    assert c.post(f"/api/projects/{pid}/tracks/", {"track": track, "code": code}, format="json").status_code == 201
    _revoke(client_for, a)
    confirm(client_for, base, ref="R75-RR-B")  # 现在仍只有 1 张，但已不是原来那张
    r = c.post(f"/api/projects/{pid}/tracks/", {"track": track, "code": code}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "confirm_coverage_required", (r.status_code, r.content[:200])
    assert _summ(client_for, pid)["needs_reconfirmation"] is True  # 被拒绝后提示仍在
    r = c.post(f"/api/projects/{pid}/tracks/", {"track": track, "code": code, "confirm_all_covered": True}, format="json")
    assert r.status_code == 201 and r.json()["event"]["covers_all_orders"] is True
    t = _summ(client_for, pid)
    assert t[track.lower()]["coverage_stale"] is False and t["needs_reconfirmation"] is False


def test_R75_01_legit_states_not_stale(client_for, base):
    pid = base["p_sales"].id
    a = client_for("assistant")
    a.post(f"/api/projects/{pid}/handoff/", {}, format="json")
    assert a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "COMPLETED"}, format="json").status_code == 201  # 0 张合同合法终态
    assert _summ(client_for, pid)["delivery"]["coverage_stale"] is False
    a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "IN_PRODUCTION"}, format="json")  # 非终态
    confirm(client_for, base, ref="R75-L1")
    confirm(client_for, base, ref="R75-L2")
    t = _summ(client_for, pid)
    assert t["delivery"]["coverage_stale"] is False and t["needs_reconfirmation"] is False
    # 销售字段裁剪不受影响
    assert "private_note" not in t["delivery"]


# ------------------------------------------------------------ R75-02
def _legacy_event(pid, code, track, covers):
    ev = TrackEvent.objects.create(project_id=pid, track=track, code=code, label_snapshot="旧终态", happened_on="2026-11-20", covers_all_orders=covers, active_orders_at=2)
    TrackEvent.objects.filter(pk=ev.pk).update(covered_order_ids=[])  # 74616ed 之前无此字段
    return ev


@pytest.mark.parametrize("covers,want_stale", [(False, True), (True, False)])
def test_R75_02_legacy_multi_order_final_after_migration(client_for, base, covers, want_stale):
    pid = base["p_sales"].id
    confirm(client_for, base, ref="R75-M-A")
    confirm(client_for, base, ref="R75-M-B")
    _legacy_event(pid, "COMPLETED", "DELIVERY", covers)
    importlib.import_module("apps.projects.migrations.0006_rc3_r74_covered_orders").backfill(global_apps, None)
    assert len(TrackEvent.objects.get(project_id=pid).covered_order_ids) == 2
    t = _summ(client_for, pid)
    assert t["delivery"]["coverage_stale"] is want_stale and t["needs_reconfirmation"] is want_stale, (covers, t)


def test_R75_02_legacy_single_order_final_stays_valid(client_for, base):
    pid = base["p_sales"].id
    confirm(client_for, base, ref="R75-S-A")
    ev = TrackEvent.objects.create(project_id=pid, track="PAYMENT", code="SETTLED", label_snapshot="已结清", happened_on="2026-11-20", active_orders_at=1)
    TrackEvent.objects.filter(pk=ev.pk).update(covered_order_ids=[])
    importlib.import_module("apps.projects.migrations.0006_rc3_r74_covered_orders").backfill(global_apps, None)
    assert _summ(client_for, pid)["payment"]["coverage_stale"] is False


# ------------------------------------------------------------ R75-03
def test_R75_03_custom_stage_cannot_mimic_final_label(client_for, base):
    a = client_for("assistant")
    final_label = StageDef.objects.get(track="DELIVERY", code="COMPLETED").label
    r = a.post("/api/stage-defs/", {"track": "DELIVERY", "code": "HANDOVER_DONE", "label": final_label, "sort": 99}, format="json")
    assert r.status_code == 400, (r.status_code, r.content[:200])
    r = a.post("/api/stage-defs/", {"track": "DELIVERY", "code": "HANDOVER_DONE", "label": " 已交付／完结 ", "sort": 99}, format="json")
    assert r.status_code == 400  # 规范化（空白/全角）后同名
    ok = a.post("/api/stage-defs/", {"track": "DELIVERY", "code": "HANDOVER_DONE", "label": "移交完成", "sort": 99}, format="json")
    assert ok.status_code == 201
    assert a.patch(f"/api/stage-defs/{ok.json()['id']}/", {"label": final_label}, format="json").status_code == 400  # 改名也不能撞名
    other = StageDef.objects.get(track="PAYMENT", code="SETTLED")
    assert a.patch(f"/api/stage-defs/{other.id}/", {"label": "已结清"}, format="json").status_code == 200  # 改成自己原名允许
