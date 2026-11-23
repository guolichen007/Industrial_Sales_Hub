"""对 5753b5f 第三轮审查（2026-11-23）R76-P1-01：中标已转交、已有交付/资金进度但尚无合同的项目，
在销售“执行中/进度”入口（旧 execution_phase=执行中 过滤）中消失；列表也不显示 RC3 状态线摘要。
修复：列表新增 tracking=1（已中标或已有有效合同的项目，默认不含已完结，tracking=all 含已完结）与安全摘要 delivery/payment。"""
import pytest

from tests.test_v21_permissions_and_business import MONEY_KEYS, confirm, keys

pytestmark = pytest.mark.django_db


def _won_unsigned(client_for, base):
    pid = base["p_sales"].id
    assert client_for("assistant").post(f"/api/projects/{pid}/handoff/", {}, format="json").status_code == 200
    assert client_for("assistant").post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "IN_PRODUCTION", "public_note": "已排产", "private_note": "内部：成本 8 万"}, format="json").status_code == 201
    assert client_for("finance").post(f"/api/projects/{pid}/tracks/", {"track": "PAYMENT", "code": "PREPAID", "private_note": "到账 36 万"}, format="json").status_code == 201
    return pid


def test_R76_01_won_unsigned_project_found_in_sales_progress_entry(client_for, base):
    pid = _won_unsigned(client_for, base)
    for who in ("sales", "sales2"):  # 本人与其他销售（全团队公共进度）
        c = client_for(who)
        d = c.get(f"/api/projects/{pid}/").json()
        assert d["tracks"]["delivery"]["code"] == "IN_PRODUCTION" and d["execution_phase"] == "未启动"
        old = [x["id"] for x in c.get("/api/projects/?execution_phase=执行中").json()]
        assert pid not in old  # 旧过滤（保留为高级条件）仍按旧执行阶段，不伪造
        rows = c.get("/api/projects/?tracking=1").json()
        row = [x for x in rows if x["id"] == pid]
        assert row, (who, [x["id"] for x in rows])
        st = row[0]["status"]
        assert st["delivery"]["label"] == "生产中" and st["payment"]["label"] == "预付款已到账"
        assert st["delivery"]["created_by_name"] == "assistant" and st["delivery"]["happened_on"] and st["delivery"]["created_at"]
        body = c.get("/api/projects/?tracking=1").content.decode()
        for secret in ("8 万", "36 万", "private_note", "已排产x"):
            assert secret not in body, secret
        assert not (MONEY_KEYS & set(keys(rows)))


def test_R76_01_tracking_scope_rules(client_for, base):
    pid = _won_unsigned(client_for, base)
    other = base["p_other"].id  # 未中标、无合同：不在跟踪范围
    s = client_for("sales")
    ids = {x["id"] for x in s.get("/api/projects/?tracking=1").json()}
    assert pid in ids and other not in ids
    # 已签约（旧逻辑执行中）也在
    confirm(client_for, base, project=base["p_other"], ref="R76-O")
    assert other in {x["id"] for x in s.get("/api/projects/?tracking=1").json()}
    # 两条线都完结 → 默认不在“跟踪中”，tracking=all 仍可见（不悄悄隐藏）
    client_for("assistant").post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "COMPLETED"}, format="json")
    client_for("finance").post(f"/api/projects/{pid}/tracks/", {"track": "PAYMENT", "code": "SETTLED"}, format="json")
    assert pid not in {x["id"] for x in s.get("/api/projects/?tracking=1").json()}
    rows = s.get("/api/projects/?tracking=all").json()
    row = [x for x in rows if x["id"] == pid][0]
    assert row["status"]["all_final"] is True
    # 失效的完结仍算“跟踪中”（需要重新确认）
    confirm(client_for, base, ref="R76-NEW")
    assert pid in {x["id"] for x in s.get("/api/projects/?tracking=1").json()}


def test_R76_01_admin_and_query_count(client_for, base, django_assert_max_num_queries):
    assert client_for("admin").get("/api/projects/?tracking=1").status_code == 403
    from apps.projects.models import Project

    for i in range(30):
        p = Project.objects.create(code=f"PRJ-Q{i:03d}", name=f"批量{i}", sales_owner=base["p_sales"].sales_owner, created_by=base["p_sales"].sales_owner)
        client_for("assistant").post(f"/api/projects/{p.id}/handoff/", {}, format="json")
        client_for("assistant").post(f"/api/projects/{p.id}/tracks/", {"track": "DELIVERY", "code": "IN_PRODUCTION"}, format="json")
    c = client_for("sales")
    with django_assert_max_num_queries(25):  # 批量取状态，不随项目数线性增长
        assert len(c.get("/api/projects/?tracking=1").json()) >= 30
