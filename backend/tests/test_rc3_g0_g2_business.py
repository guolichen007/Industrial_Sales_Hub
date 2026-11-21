"""RC3-G0/G2：业务角色能力矩阵（CI 请求矩阵）、询价登记、财务业务口径、两条状态线、阶段配置、公开/内部说明（T06–T10、T24）。

矩阵的唯一事实来源：docs/RC3-业务角色与字段矩阵.md 第 2 节；本文件 MATRIX 与之逐行对应（test_matrix_doc_in_sync 检查）。
"""
import re
from pathlib import Path

import pytest
from django.utils import timezone

from apps.projects.models import Project, StageDef, TrackEvent

pytestmark = pytest.mark.django_db
ROLES = ["admin", "leader", "assistant", "sales", "finance"]


def _won(base, client_for):
    pid = base["p_sales"].id
    assert client_for("assistant").post(f"/api/projects/{pid}/handoff/", {}, format="json").status_code == 200
    return pid


# ------------------------------------------------------------ 请求矩阵（BUSINESS 口径，默认）
# (编号, 方法, 路径模板, 请求体, {角色: 期望状态码})
def MATRIX(base):
    p, other, acc, ent = base["p_sales"].id, base["p_other"].id, base["accs"][0].id, base["ents"][0].id
    A = {"admin": 403}
    return [
        ("M01 项目列表", "get", "/api/projects/", None, {**A, "leader": 200, "assistant": 200, "sales": 200, "finance": 200}),
        ("M02 新建项目", "post", "/api/projects/", {"name": "矩阵项目"}, {**A, "leader": 201, "assistant": 201, "sales": 201, "finance": 201}),
        ("M03 改他人项目资料", "patch", f"/api/projects/{other}/", {"location": "x"}, {**A, "leader": 200, "assistant": 200, "sales": 403, "finance": 200}),
        ("M04 询价列表", "get", "/api/inquiries/", None, {**A, "leader": 200, "assistant": 200, "sales": 403, "finance": 200}),
        ("M05 登记询价", "post", "/api/inquiries/", {"project": p, "inquirer": acc, "received_on": "2026-11-20", "requirement": "x"}, {**A, "leader": 201, "assistant": 201, "sales": 403, "finance": 201}),
        ("M06 报价列表（含价格）", "get", "/api/quotations/", None, {**A, "leader": 200, "assistant": 200, "sales": 403, "finance": 403}),
        ("M07 新建报价", "post", "/api/quotations/", {"project": p, "recipient": acc, "legal_entity": ent}, {**A, "leader": 201, "assistant": 201, "sales": 403, "finance": 403}),
        ("M08 历史价格", "get", "/api/quote-lines/history/", None, {**A, "leader": 200, "assistant": 200, "sales": 403, "finance": 403}),
        ("M09 补录业绩（无金额）", "post", "/api/performances/", {"title": "矩阵业绩", "year": 2025}, {**A, "leader": 201, "assistant": 201, "sales": 403, "finance": 201}),
        ("M10 补录业绩带金额", "post", "/api/performances/", {"title": "x", "year": 2025, "internal_amount": "1"}, {**A, "leader": 201, "assistant": 201, "sales": 403, "finance": 403}),
        ("M11 业绩设为可公开", "post", "/api/performances/", {"title": "x", "year": 2025, "publishable": True}, {**A, "leader": 201, "assistant": 201, "sales": 403, "finance": 403}),
        ("M12 阶段配置读取", "get", "/api/stage-defs/", None, {**A, "leader": 200, "assistant": 200, "sales": 200, "finance": 200}),
        ("M13 改阶段展示名", "post", "/api/stage-defs/", {"track": "DELIVERY", "code": "QC_CHECK", "label": "出厂检验", "sort": 25}, {**A, "leader": 201, "assistant": 201, "sales": 403, "finance": 403}),
        ("M14 中标确认", "post", f"/api/projects/{other}/handoff/", {}, {**A, "leader": 200, "assistant": 200, "sales": 403, "finance": 403}),
        ("M15 账号管理", "get", "/api/users/", None, {"admin": 200, "leader": 403, "assistant": 403, "sales": 403, "finance": 403}),
    ]


@pytest.mark.parametrize("role", ROLES)
def test_business_role_request_matrix(client_for, base, role, settings):
    settings.FINANCE_SCOPE, settings.FINANCE_QUOTE = "BUSINESS", False
    fails = []
    for name, method, path, body, expect in MATRIX(base):
        c = client_for(role)
        r = getattr(c, method)(path, body, format="json") if body is not None else getattr(c, method)(path)
        if r.status_code != expect[role]:
            fails.append(f"{name} {role}: 期望 {expect[role]} 实际 {r.status_code} {r.content[:120]!r}")
    assert not fails, "\n".join(fails)


def test_rc2_scope_switch_restores_d5(client_for, base, settings):
    settings.FINANCE_SCOPE = "RC2"
    f = client_for("finance")
    assert f.post("/api/projects/", {"name": "x"}, format="json").status_code == 403
    assert f.get("/api/inquiries/").status_code == 403
    assert f.post("/api/performances/", {"title": "x", "year": 2025}, format="json").status_code == 403
    assert "project.create" not in f.get("/api/auth/me/").json()["caps"]


def test_finance_quote_switch_is_explicit(client_for, base, settings):
    settings.FINANCE_QUOTE = True
    assert client_for("finance").get("/api/quotations/").status_code == 200
    assert "quote.access" in client_for("finance").get("/api/auth/me/").json()["caps"]
    assert client_for("sales").get("/api/quotations/").status_code == 403  # 任何口径下销售都无报价


def test_matrix_doc_in_sync(base):
    doc = (Path(__file__).resolve().parents[2] / "docs" / "RC3-业务角色与字段矩阵.md").read_text(encoding="utf-8")
    for name, *_ in MATRIX(base):
        assert name.split()[0] in doc, f"矩阵文档缺少 {name}"


# ------------------------------------------------------------ T06/T07 财务业务操作
def test_T06_finance_inquiry_flow_and_no_price(client_for, base):
    f = client_for("finance")
    r = f.post("/api/inquiries/", {"project": base["p_sales"].id, "inquirer": base["accs"][0].id, "received_on": "2026-11-18", "due_on": "2026-11-25",
                                   "requirement": "斗轮机润滑", "items": [{"model": "LUB-320", "name": "润滑系统", "qty": "2", "unit": "套"}]}, format="json")
    assert r.status_code == 201 and r.json()["code"].startswith("XJ-") and r.json()["created_by_name"] == "finance"
    iid = r.json()["id"]
    bad = f.patch(f"/api/inquiries/{iid}/", {"items": [{"model": "x", "unit_price": "100"}]}, format="json")
    assert bad.status_code == 400  # 询价不记录价格
    # 商务以该询价生成报价，询价状态变为已报价；财务只能看到报价编号，看不到价格
    q = client_for("assistant").post("/api/quotations/", {"project": base["p_sales"].id, "recipient": base["accs"][0].id, "legal_entity": base["ents"][0].id, "inquiry": iid}, format="json")
    assert q.status_code == 201
    d = f.get(f"/api/inquiries/{iid}/").json()
    assert d["status"] == "QUOTED" and d["quote_codes"] == [q.json()["code"]]
    assert "total" not in str(d) and "price" not in str(d)
    wrong = client_for("assistant").post("/api/quotations/", {"project": base["p_other"].id, "recipient": base["accs"][0].id, "legal_entity": base["ents"][0].id, "inquiry": iid}, format="json")
    assert wrong.status_code == 400


def test_T07_finance_performance_fields(client_for, base, users):
    from apps.performances.models import Performance

    f = client_for("finance")
    r = f.post("/api/performances/", {"title": "财务补录", "year": 2024, "region": "华东"}, format="json")
    assert r.status_code == 201 and r.json()["status"] == "PENDING" and "internal_amount" not in r.json()
    pid = r.json()["id"]
    assert f.patch(f"/api/performances/{pid}/", {"region": "华北"}, format="json").status_code == 200
    assert f.patch(f"/api/performances/{pid}/", {"internal_amount": "99"}, format="json").status_code == 403
    assert f.patch(f"/api/performances/{pid}/", {"publishable": True}, format="json").status_code == 403
    assert f.patch(f"/api/performances/{pid}/", {"remark": "内部"}, format="json").status_code == 403
    assert f.post(f"/api/performances/{pid}/approve/").status_code == 403
    p = Performance.objects.get(pk=pid)
    assert p.internal_amount is None and not p.publishable and p.region == "华北"


def test_finance_project_edit_but_not_owner_or_scale(client_for, base, users):
    f = client_for("finance")
    r = f.post("/api/projects/", {"name": "财务建项", "expected_amount_note": "约 100 万"}, format="json")
    assert r.status_code == 201 and "expected_amount_note" not in r.json()
    assert Project.objects.get(pk=r.json()["id"]).expected_amount_note == ""
    assert f.post(f"/api/projects/{base['p_sales'].id}/handoff/revert/", {"reason": "x"}, format="json").status_code == 403


# ------------------------------------------------------------ T08/T09 两条状态线
def test_T08_T09_two_tracks_coexist_and_sales_summary(client_for, base):
    pid = _won(base, client_for)
    a, f = client_for("assistant"), client_for("finance")
    r = a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "PRODUCED", "happened_on": "2026-11-10", "public_note": "已出厂检验", "private_note": "内部：成本略超"}, format="json")
    assert r.status_code == 201, r.content
    assert a.post(f"/api/projects/{pid}/tracks/", {"track": "PAYMENT", "code": "PREPAID"}, format="json").status_code == 403  # 资金线商务不可写
    r = f.post(f"/api/projects/{pid}/tracks/", {"track": "PAYMENT", "code": "BALANCE_DUE", "public_note": "尾款待收", "private_note": "尾款 30 万，客户承诺月底"}, format="json")
    assert r.status_code == 201
    summ = r.json()["summary"]
    assert summ["delivery"]["code"] == "PRODUCED" and summ["payment"]["code"] == "BALANCE_DUE"  # T09 不互相覆盖
    s = client_for("sales").get(f"/api/projects/{pid}/").json()["tracks"]
    assert s["delivery"]["label"] == "生产完成" and s["payment"]["label"] == "尾款待收/存在欠款"
    assert "private_note" not in s["delivery"] and "private_note" not in s["payment"]
    sales_body = client_for("sales").get(f"/api/projects/{pid}/tracks/").content.decode()
    assert "30 万" not in sales_body and "成本略超" not in sales_body and "已出厂检验" in sales_body
    assert client_for("sales").post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "SHIPPED"}, format="json").status_code == 403


def test_tracks_require_won_and_dates_validated(client_for, base):
    pid = base["p_sales"].id
    assert client_for("assistant").post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "IN_PRODUCTION"}, format="json").status_code == 409
    _won(base, client_for)
    a = client_for("assistant")
    assert a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "NOPE"}, format="json").status_code == 400
    assert a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "SHIPPED", "happened_on": "2099-01-01"}, format="json").status_code == 400
    # 客户端伪造 created_at 被忽略
    r = a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "SHIPPED", "created_at": "2000-01-01T00:00:00Z"}, format="json")
    assert r.status_code == 201 and not r.json()["event"]["created_at"].startswith("2000")


def test_revert_event_and_overdue(client_for, base):
    pid = _won(base, client_for)
    a = client_for("assistant")
    a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "IN_PRODUCTION"}, format="json")
    e2 = a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "SHIPPED"}, format="json").json()["event"]
    assert a.post(f"/api/projects/{pid}/tracks/{e2['id']}/revert/", {}, format="json").status_code == 400  # 必须填原因
    r = a.post(f"/api/projects/{pid}/tracks/{e2['id']}/revert/", {"reason": "录错，尚未发货"}, format="json")
    assert r.status_code == 200 and r.json()["summary"]["delivery"]["code"] == "IN_PRODUCTION"
    assert TrackEvent.objects.filter(pk=e2["id"], reverted_at__isnull=False).exists()  # 不删除
    assert a.patch(f"/api/projects/{pid}/", {"delivery_due_on": (timezone.localdate() - timezone.timedelta(days=3)).isoformat()}, format="json").status_code == 200
    assert client_for("sales").get(f"/api/projects/{pid}/").json()["tracks"]["delivery_overdue"] is True
    a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "COMPLETED"}, format="json")
    assert client_for("sales").get(f"/api/projects/{pid}/").json()["tracks"]["delivery_overdue"] is False


# ------------------------------------------------------------ T10 阶段配置
def test_T10_stage_rename_reorder_disable_keeps_history(client_for, base):
    pid = _won(base, client_for)
    a = client_for("assistant")
    a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "READY_PICKUP", "happened_on": "2026-11-15"}, format="json")
    d = StageDef.objects.get(track="DELIVERY", code="READY_PICKUP")
    assert a.patch(f"/api/stage-defs/{d.id}/", {"label": "待客户提货", "sort": 5, "code": "HACK", "track": "PAYMENT"}, format="json").status_code == 200
    d.refresh_from_db()
    assert (d.code, d.track, d.label, d.sort) == ("READY_PICKUP", "DELIVERY", "待客户提货", 5)  # code/track 不可改
    assert client_for("sales").get(f"/api/projects/{pid}/").json()["tracks"]["delivery"]["label"] == "待客户提货"
    assert a.patch(f"/api/stage-defs/{d.id}/", {"active": False}, format="json").status_code == 200
    # 停用后：历史仍显示、不能再新记录该阶段
    assert client_for("sales").get(f"/api/projects/{pid}/").json()["tracks"]["delivery"]["code"] == "READY_PICKUP"
    assert a.post(f"/api/projects/{pid}/tracks/", {"track": "DELIVERY", "code": "READY_PICKUP"}, format="json").status_code == 400
    assert a.delete(f"/api/stage-defs/{d.id}/").status_code == 405
    ev = TrackEvent.objects.get(project_id=pid, code="READY_PICKUP")
    assert ev.label_snapshot == "待提货"  # 事件保留当时的名称


def test_legacy_milestones_migrate_to_delivery_track(client_for, base):
    import importlib

    from django.apps import apps as global_apps

    from tests.test_v21_permissions_and_business import confirm

    confirm(client_for, base, ref="LEG-1")
    p = Project.objects.get(pk=base["p_sales"].id)
    m = p.milestones.get(stage="生产/备货")
    m.state, m.actual_date = "DONE", timezone.localdate()
    m.save()
    TrackEvent.objects.all().delete()
    mig = importlib.import_module("apps.projects.migrations.0004_rc3_g2_tracks")
    mig.seed_and_migrate(global_apps, None)
    ev = TrackEvent.objects.get(project=p)
    assert ev.code == "PRODUCED" and "迁移" in ev.private_note


# ------------------------------------------------------------ T24 内部说明不外泄
def test_T24_internal_amount_text_never_reaches_sales(client_for, base):
    from tests.test_v21_permissions_and_business import confirm

    confirm(client_for, base, ref="T24-1")
    pid = base["p_sales"].id
    a = client_for("assistant")
    a.post(f"/api/projects/{pid}/updates/", {"stream": "EXECUTION", "title": "收款进展", "content": "预付款 30 万已到账", "public_content": "预付款已到账"}, format="json")
    mid = Project.objects.get(pk=pid).milestones.get(stage="生产/备货").id
    a.patch(f"/api/projects/{pid}/milestones/{mid}/", {"state": "DELAYED", "delay_reason": "客户欠款 12 万暂停排产", "note": "合同额 88 万", "public_note": "排产推迟"}, format="json")
    client_for("finance").post(f"/api/projects/{pid}/tracks/", {"track": "PAYMENT", "code": "PREPAID", "private_note": "到账 300000 元"}, format="json")
    s = client_for("sales")
    bodies = [s.get(u).content.decode() for u in (f"/api/projects/{pid}/", f"/api/projects/{pid}/updates/", f"/api/projects/{pid}/milestones/", f"/api/projects/{pid}/tracks/", "/api/projects/", "/api/dashboard/")]
    for b in bodies:
        for secret in ("30 万", "12 万", "88 万", "300000"):
            assert secret not in b, (secret, b[:200])
    assert "预付款已到账" in bodies[1] and "排产推迟" in bodies[0]
    # 商务/财务仍能看到内部说明
    assert "30 万" in a.get(f"/api/projects/{pid}/updates/").content.decode()
    assert "88 万" in a.get(f"/api/projects/{pid}/").content.decode()


def test_sales_own_presale_text_still_visible(client_for, base):
    pid = base["p_sales"].id
    client_for("sales").post(f"/api/projects/{pid}/updates/", {"stream": "PRESALE", "title": "拜访", "content": "客户关注交期"}, format="json")
    assert "客户关注交期" in client_for("sales").get(f"/api/projects/{pid}/updates/").content.decode()
    assert "客户关注交期" not in client_for("sales2").get(f"/api/projects/{pid}/updates/").content.decode()  # 他人只看公开说明
