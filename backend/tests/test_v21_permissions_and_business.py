"""V2.1-LAN：权限（D2/D5/D7/管理员分离）、账号、输入健壮性、执行、案例的反例测试。"""
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.performances.models import Performance
from apps.projects.models import Project

MONEY_KEYS = {"internal_amount", "contract_amount", "unit_price", "total_gross", "total_net", "total_tax", "base_price", "amount", "line_gross", "line_net", "sums", "amount_total"}


def keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from keys(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from keys(x)


def confirm(client_for, base, project=None, ref="HT-1", amount="88140", who="leader", **kw):
    project = project or base["p_sales"]
    body = {"project": project.id, "legal_entity": base["ents"][0].id, "basis": "CONTRACT", "order_ref": ref, "confirm_now": True, **kw}
    if amount is not None:
        body["contract_amount"] = amount
    r = client_for(who).post("/api/order-confirmations/", body, format="json")
    assert r.status_code == 201, r.content
    return r.json()


# ======================= D5：财务无金额、台账关闭 =======================
def test_d5_finance_cannot_write_contract_amount(client_for, base):
    r = client_for("finance").post("/api/order-confirmations/", {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "basis": "CONTRACT", "order_ref": "F-1", "contract_amount": "1"}, format="json")
    assert r.status_code == 403


def test_d5_finance_can_confirm_without_amount_and_never_sees_amount(client_for, base, make_quote):
    make_quote()
    o = confirm(client_for, base, ref="F-2", amount=None, who="finance")
    assert "contract_amount" not in o
    # 负责人补录金额后，财务仍看不到
    for url in ["/api/order-confirmations/", f"/api/projects/{base['p_sales'].id}/related/", "/api/performances/", "/api/performances/stats/", "/api/dashboard/"]:
        r = client_for("finance").get(url)
        assert r.status_code == 200, url
        assert not (MONEY_KEYS & set(keys(r.json()))), (url, MONEY_KEYS & set(keys(r.json())))


def test_d5_finance_ledger_closed_for_everyone(client_for, base):
    for role in ("leader", "finance", "assistant", "admin"):
        c = client_for(role)
        assert c.get(f"/api/projects/{base['p_sales'].id}/finance/").status_code == 404
        assert c.post(f"/api/projects/{base['p_sales'].id}/finance/", {"kind": "RECEIPT", "amount": "1", "happened_on": "2026-11-01"}, format="json").status_code in (403, 404, 405)


def test_d5_finance_cannot_read_performance_attachments(client_for, users):
    p = Performance.objects.create(source="MANUAL", status="CONFIRMED", title="x", year=2026, internal_amount="1")
    r = client_for("assistant").post("/api/attachments/", {"owner_type": "performance", "owner_id": p.id, "file": SimpleUploadedFile("a.pdf", b"%PDF-1.4")}, format="multipart")
    assert r.status_code == 201
    assert client_for("finance").get(f"/api/attachments/{r.json()['id']}/download/").status_code == 403


# ======================= D2：销售无报价信息 =======================
def test_d2_sales_no_quote_count_no_scale_note(client_for, base, make_quote):
    make_quote()
    Project.objects.filter(pk=base["p_sales"].id).update(expected_amount_note="约 300 万")
    c = client_for("sales")
    lst = c.get("/api/projects/").content.decode()
    assert "quote_count" not in lst and "300 万" not in lst
    det = c.get(f"/api/projects/{base['p_sales'].id}/").json()
    assert "expected_amount_note" not in det
    # 盲写也无效
    c.patch(f"/api/projects/{base['p_sales'].id}/", {"expected_amount_note": "覆盖"}, format="json")
    assert Project.objects.get(pk=base["p_sales"].id).expected_amount_note == "约 300 万"
    assert client_for("assistant").get(f"/api/projects/{base['p_sales'].id}/").json()["expected_amount_note"] == "约 300 万"


def test_d2_sales_performance_has_no_order_ref(client_for, base, users):
    confirm(client_for, base, ref="SECRET-REF-77")
    rows = client_for("sales").get("/api/performances/").json()
    assert rows and "SECRET-REF-77" not in json.dumps(rows, ensure_ascii=False) and "order_ref" not in rows[0]


def test_d2_sales_products_without_price(client_for, base):
    rows = client_for("sales").get("/api/products/").json()
    assert rows and "base_price" not in rows[0]


# ======================= D7：项目可见 ≠ 可编辑 ≠ 业绩 =======================
def test_d7_default_sales_sees_all_projects_but_edits_only_own(client_for, base, settings):
    settings.PROJECT_VISIBILITY = "ALL"
    c = client_for("sales")
    ids = {p["id"]: p for p in c.get("/api/projects/").json()}
    assert base["p_other"].id in ids and ids[base["p_other"].id]["can_edit"] is False and ids[base["p_sales"].id]["can_edit"] is True
    other = base["p_other"].id
    assert c.get(f"/api/projects/{other}/").status_code == 200
    assert c.patch(f"/api/projects/{other}/", {"name": "x"}, format="json").status_code == 403
    assert c.post(f"/api/projects/{other}/parties/", {"account": base["accs"][0].id, "party_role": "主机厂"}, format="json").status_code == 403
    assert c.post(f"/api/projects/{other}/updates/", {"stream": "PRESALE", "title": "x"}, format="json").status_code == 403
    assert c.post("/api/attachments/", {"owner_type": "project", "owner_id": other, "file": SimpleUploadedFile("a.png", b"\x89PNG....")}, format="multipart").status_code == 403


def test_d7_project_visibility_does_not_widen_performance(client_for, base, settings, users):
    settings.PROJECT_VISIBILITY = "ALL"
    confirm(client_for, base, project=base["p_other"], ref="OTHER-1")
    assert client_for("sales2").get("/api/performances/").json()  # 属于 sales2
    assert client_for("sales").get("/api/performances/").json() == []
    rel = client_for("sales").get(f"/api/projects/{base['p_other'].id}/related/").json()
    assert rel["performances"] == []
    assert client_for("sales").get("/api/dashboard/").json()["performance_confirmed"] == 0


def test_d7_project_attachments_not_visible_to_non_editors(client_for, base, settings):
    settings.PROJECT_VISIBILITY = "ALL"
    r = client_for("sales2").post("/api/attachments/", {"owner_type": "project", "owner_id": base["p_other"].id, "file": SimpleUploadedFile("图纸.png", b"\x89PNG....")}, format="multipart")
    assert r.status_code == 201
    # RC2 变更说明：可见但不可编辑的项目，列表接口返回 200 且**只含**显式授权“销售可见”的交付附件（此处为空），
    # 不再整体 403（否则授权的交付文件无法提供）。未授权附件的内容与直链仍然 403。
    lst = client_for("sales").get(f"/api/attachments/?owner_type=project&owner_id={base['p_other'].id}")
    assert lst.status_code == 200 and lst.json() == []
    assert client_for("sales").get(f"/api/attachments/{r.json()['id']}/download/").status_code == 403


def test_d7_sales_cannot_grant_membership_or_owner(client_for, base, users):
    c = client_for("sales")
    pid = base["p_sales"].id
    assert c.patch(f"/api/projects/{pid}/", {"members": [users["sales2"].id]}, format="json").status_code == 403
    r = c.post("/api/projects/", {"name": "新项目", "members": [users["sales2"].id]}, format="json")
    assert r.status_code == 403
    r = c.post("/api/projects/", {"name": "新项目", "sales_owner": users["sales2"].id}, format="json")
    assert r.status_code == 403
    r = c.post("/api/projects/", {"name": "新项目"}, format="json")
    assert r.status_code == 201 and r.json()["sales_owner"] == users["sales"].id


def test_sales_cannot_mark_won_without_order(client_for, base):
    c = client_for("sales")
    pid = base["p_sales"].id
    assert c.patch(f"/api/projects/{pid}/", {"presale_phase": "成交"}, format="json").status_code == 400
    assert c.post(f"/api/projects/{pid}/updates/", {"stream": "PRESALE", "title": "x", "phase": "成交"}, format="json").status_code == 403
    assert Project.objects.get(pk=pid).presale_phase != "成交"


def test_attachment_owner_must_exist(client_for):
    r = client_for("finance").post("/api/attachments/", {"owner_type": "order", "owner_id": 999999, "file": SimpleUploadedFile("a.pdf", b"%PDF-1.4")}, format="multipart")
    assert r.status_code == 403


# ======================= 管理员为技术角色 =======================
@pytest.mark.parametrize("url", ["/api/quotations/", "/api/performances/", "/api/order-confirmations/", "/api/import-batches/template/products/"])
def test_admin_has_no_business_sensitive_access(client_for, url):
    assert client_for("admin").get(url).status_code == 403


def test_admin_system_status_and_dashboard_without_money(client_for, make_quote):
    make_quote()
    st = client_for("admin").get("/api/system/status/")
    assert st.status_code == 200 and st.json()["users"]["admins"] == 1
    d = client_for("admin").get("/api/dashboard/").json()
    assert "quote_count" not in d
    assert client_for("sales").get("/api/system/status/").status_code == 403


# ======================= 账号 =======================
def test_last_admin_cannot_be_demoted_or_disabled(client_for, users):
    c = client_for("admin")
    me = users["admin"].id
    assert c.patch(f"/api/users/{me}/", {"role": "SALES"}, format="json").json()["code"] == "last_admin"
    assert c.patch(f"/api/users/{me}/", {"is_active": False}, format="json").json()["code"] == "last_admin"
    c.post("/api/users/", {"username": "admin2", "role": "ADMIN", "password": "Abcdef12xyz"}, format="json")
    assert c.patch(f"/api/users/{me}/", {"role": "LEADER"}, format="json").status_code == 200


def test_password_policy_and_no_password_in_audit(client_for):
    from apps.audit.models import AuditEvent

    c = client_for("admin")
    assert c.post("/api/users/", {"username": "u1", "role": "SALES", "password": "abcdefgh"}, format="json").status_code == 400
    assert c.post("/api/users/", {"username": "u1", "role": "SALES", "password": "12345678"}, format="json").status_code == 400
    r = c.post("/api/users/", {"username": "u1", "role": "SALES", "password": "Zx9-strong-pw"}, format="json")
    assert r.status_code == 201
    c.patch(f"/api/users/{r.json()['id']}/", {"password": "Yy8-another-pw"}, format="json")
    dump = json.dumps(list(AuditEvent.objects.values("summary", "detail")), ensure_ascii=False)
    assert "Zx9-strong-pw" not in dump and "Yy8-another-pw" not in dump


def test_role_change_kills_sessions(users):
    c = APIClient()
    assert c.post("/api/auth/login/", {"username": "sales", "password": "Test@12345"}, format="json").status_code == 200
    admin = APIClient()
    admin.force_authenticate(users["admin"])
    admin.patch(f"/api/users/{users['sales'].id}/", {"role": "FINANCE"}, format="json")
    assert c.get("/api/auth/me/").status_code == 403


def test_login_csrf_enforced_for_real_clients(users):
    c = APIClient(enforce_csrf_checks=True)
    # RC2 变更：登录不再豁免 CSRF（防“登录 CSRF”）。无令牌 403；先取令牌再登录 200
    assert c.post("/api/auth/login/", {"username": "sales", "password": "Test@12345"}, format="json").status_code == 403
    tok = c.get("/api/auth/csrf/").cookies["csrftoken"].value
    assert c.post("/api/auth/login/", {"username": "sales", "password": "Test@12345"}, format="json", HTTP_X_CSRFTOKEN=tok).status_code == 200
    r = c.post("/api/projects/", {"name": "x"}, format="json")
    assert r.status_code == 403  # 已登录写操作必须带 CSRF


def test_public_info_and_init_admin(db):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    import os

    r = APIClient().get("/api/public-info/").json()
    assert r["initialized"] is False
    os.environ["ISH_INIT_ADMIN_PASSWORD"] = "Init9-admin-pw"
    try:
        call_command("init_admin", username="boss")
        with pytest.raises(CommandError):
            call_command("init_admin", username="boss2")
        call_command("init_admin", username="boss2", if_missing=True)
    finally:
        del os.environ["ISH_INIT_ADMIN_PASSWORD"]
    assert User.objects.filter(role="ADMIN").count() == 1
    assert APIClient().get("/api/public-info/").json()["initialized"] is True


def test_seed_demo_forbidden_outside_demo(db, settings):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    settings.DEMO_SEED_ALLOWED = False
    with pytest.raises(CommandError):
        call_command("seed_demo")
    assert not User.objects.exists()


# ======================= 输入健壮性：非法输入 4xx，不得 500 =======================
@pytest.mark.parametrize("who,method,url,body", [
    ("assistant", "get", "/api/quotations/?project=abc", None),
    ("assistant", "get", "/api/quotations/?date_from=2026-13-45", None),
    ("leader", "get", "/api/order-confirmations/?project=abc", None),
    ("leader", "get", "/api/performances/?year=abc", None),
    ("assistant", "get", "/api/products/?category=abc", None),
    ("assistant", "get", "/api/contacts/?account=abc", None),
    ("assistant", "get", "/api/accounts/check-duplicate/?name=abc&exclude=x", None),
    ("leader", "post", "/api/case-preview/", {"performance_ids": ["a", "b"]}),
    ("leader", "post", "/api/case-preview/", {"performance_ids": "1"}),
    ("leader", "post", "/api/case-releases/", {"performance_ids": ["x"]}),
    ("leader", "post", "/api/case-releases/", {"performance_ids": [1], "legal_entity": "abc"}),
    ("leader", "post", "/api/order-confirmations/", {"idempotency_key": 123, "project": 1}),
    ("leader", "post", "/api/order-confirmations/", {"project": "abc"}),
    ("assistant", "post", "/api/quotations/1/status/", {"status": [1]}),
])
def test_bad_inputs_are_4xx(client_for, base, make_quote, who, method, url, body):
    make_quote()
    c = client_for(who)
    r = getattr(c, method)(url, body, format="json") if body is not None else getattr(c, method)(url)
    assert 400 <= r.status_code < 500, (url, r.status_code, r.content[:200])


@pytest.mark.parametrize("body", ["[]", "123", '"x"', "null"])
def test_non_object_json_body_is_400(db, body):
    r = APIClient().post("/api/auth/login/", body, content_type="application/json")
    assert r.status_code == 400


def test_add_line_bad_product_id(client_for, make_quote):
    _, rid = make_quote(render=False)
    r = client_for("assistant").post(f"/api/quote-revisions/{rid}/lines/", {"product": "abc"}, format="json")
    assert r.status_code == 400


def test_missing_private_file_is_410_not_500(client_for, make_quote, private_dir):
    import shutil

    _, rid = make_quote()
    shutil.rmtree(private_dir / "quotes")
    r = client_for("assistant").get(f"/api/quote-revisions/{rid}/pdf/")
    assert r.status_code == 410


def test_corrupt_xlsx_and_nan_values(client_for):
    c = client_for("assistant")
    r = c.post("/api/import-batches/preview/", {"kind": "products", "file": SimpleUploadedFile("x.xlsx", b"PK\x03\x04garbage")}, format="multipart")
    assert r.status_code == 400
    import io

    from openpyxl import Workbook

    from apps.imports.service import TEMPLATES

    wb = Workbook()
    wb.active.append(TEMPLATES["products"])
    wb.active.append(["N-1", "N", "x", "c", "", "", "NaN", "Infinity", "", ""])
    wb.active.append(["N-2", "N", "x", "c", "", "", "13", "1e30", "", ""])
    b = io.BytesIO()
    wb.save(b)
    r = c.post("/api/import-batches/preview/", {"kind": "products", "file": SimpleUploadedFile("p.xlsx", b.getvalue())}, format="multipart")
    assert r.status_code == 200 and r.json()["summary"]["error"] == 2


def test_request_id_header_sanitized(client_for):
    r = client_for("sales").get("/api/projects/", HTTP_X_REQUEST_ID="evil\nline")
    assert "\n" not in r["X-Request-Id"] and r["X-Request-Id"] != "evil\nline"


# ======================= 执行节点 =======================
def test_milestones_only_after_order(client_for, base):
    pid = base["p_sales"].id
    assert client_for("finance").post(f"/api/projects/{pid}/milestones/").status_code == 403
    r = client_for("assistant").post(f"/api/projects/{pid}/milestones/")
    assert r.status_code == 409 and r.json()["code"] == "no_order"
    confirm(client_for, base, ref="M-1")
    assert Project.objects.get(pk=pid).milestones.count() == 8


def test_milestone_validation_and_delay_reason(client_for, base):
    confirm(client_for, base, ref="M-2")
    pid = base["p_sales"].id
    ms = client_for("finance").get(f"/api/projects/{pid}/milestones/").json()
    mid = next(m["id"] for m in ms if m["stage"] == "发货")
    c = client_for("finance")
    assert c.patch(f"/api/projects/{pid}/milestones/{mid}/", {"state": "DELAYED"}, format="json").status_code == 400
    assert c.patch(f"/api/projects/{pid}/milestones/{mid}/", {"state": "IN_PROGRESS", "actual_date": "2026-01-01"}, format="json").status_code == 400
    assert c.patch(f"/api/projects/{pid}/milestones/{mid}/", {"state": "DONE", "actual_date": "2099-01-01"}, format="json").status_code == 400
    r = c.patch(f"/api/projects/{pid}/milestones/{mid}/", {"state": "DELAYED", "delay_reason": "厂家排产推迟", "planned_date": "2026-12-20", "owner_name": "张三"}, format="json")
    assert r.status_code == 200 and r.json()["delay_reason"] == "厂家排产推迟"
    upd = client_for("sales").get(f"/api/projects/{pid}/updates/?stream=EXECUTION").json()
    assert any("厂家排产推迟" in u["content"] for u in upd)


def test_revoke_last_order_pauses_and_other_orders_keep_execution(client_for, base):
    pid = base["p_sales"].id
    o1 = confirm(client_for, base, ref="R-1")
    o2 = confirm(client_for, base, ref="R-2")
    lc = client_for("leader")
    lc.post(f"/api/order-confirmations/{o1['id']}/revoke/", {"reason": "合同作废"}, format="json")
    p = Project.objects.get(pk=pid)
    assert p.presale_phase == "成交" and p.execution_phase == "执行中"
    lc.post(f"/api/order-confirmations/{o2['id']}/revoke/", {"reason": "客户取消"}, format="json")
    p = Project.objects.get(pk=pid)
    assert p.presale_phase == "结果待定" and p.execution_phase == "暂停" and p.milestones.count() == 8  # 节点保留
    assert Performance.objects.filter(project=p, status="REVOKED").count() == 2


def test_performance_revoke_must_go_through_order(client_for, base):
    o = confirm(client_for, base, ref="PR-1")
    perf = Performance.objects.get(order_confirmation_id=o["id"])
    r = client_for("leader").post(f"/api/performances/{perf.id}/revoke/", {"reason": "x"}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "revoke_via_order"
    r = client_for("assistant").patch(f"/api/performances/{perf.id}/", {"internal_amount": "1"}, format="json")
    assert r.status_code == 409


# ======================= 订单与报价绑定 =======================
def test_order_binds_frozen_revision_and_checks_consistency(client_for, base, make_quote):
    qid, rid = make_quote(recipient=base["accs"][0], entity=base["ents"][0])
    c = client_for("assistant")
    r = c.post("/api/order-confirmations/", {"project": base["p_sales"].id, "quote": qid, "basis": "CONTRACT", "order_ref": "B-1", "legal_entity": base["ents"][1].id}, format="json")
    assert r.status_code == 400  # 主体与报价不一致
    r = c.post("/api/order-confirmations/", {"project": base["p_other"].id, "quote": qid, "basis": "CONTRACT", "order_ref": "B-1"}, format="json")
    assert r.status_code == 400  # 报价不属于该项目
    r = c.post("/api/order-confirmations/", {"project": base["p_sales"].id, "quote": qid, "basis": "CONTRACT", "order_ref": "B-1"}, format="json")
    assert r.status_code == 201 and r.json()["quote_revision"] == rid and r.json()["quote_code"].endswith("-V1")
    # 之后新建 V2 草稿不改变订单绑定
    c.post(f"/api/quotations/{qid}/revisions/")
    assert client_for("leader").get(f"/api/order-confirmations/{r.json()['id']}/").json()["quote_revision"] == rid
    # 只有草稿的报价不能作为订单依据
    q2, _ = make_quote(render=False)
    r = c.post("/api/order-confirmations/", {"project": base["p_sales"].id, "quote": q2, "basis": "CONTRACT", "order_ref": "B-2"}, format="json")
    assert r.status_code == 400


def test_order_ref_normalized_uniqueness(client_for, base):
    confirm(client_for, base, ref="ht-2026 001")
    r = client_for("leader").post("/api/order-confirmations/", {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "basis": "CONTRACT", "order_ref": "ＨＴ-2026001"}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "duplicate_order"


def test_idempotency_same_key_different_body_conflict(client_for, base):
    c = client_for("leader")
    body = {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "basis": "CONTRACT", "order_ref": "I-1", "idempotency_key": "k-x"}
    assert c.post("/api/order-confirmations/", body, format="json").status_code == 201
    assert c.post("/api/order-confirmations/", body, format="json").status_code == 200
    r = c.post("/api/order-confirmations/", {**body, "order_ref": "I-2"}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "idempotency_conflict"


def test_confirm_now_failure_leaves_no_draft(client_for, base):
    from apps.contracts.models import OrderConfirmation

    r = client_for("assistant").post("/api/order-confirmations/", {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "basis": "LEADER", "reason": "x", "confirm_now": True}, format="json")
    assert r.status_code == 403
    assert not OrderConfirmation.objects.exists()


def test_confirm_now_string_false_does_not_confirm(client_for, base):
    r = client_for("leader").post("/api/order-confirmations/", {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "basis": "CONTRACT", "order_ref": "CF-1", "confirm_now": "false"}, format="json")
    assert r.json()["status"] == "DRAFT"


def test_accepted_quote_with_order_cannot_be_reverted(client_for, base, make_quote):
    qid, _ = make_quote()
    client_for("assistant").post("/api/order-confirmations/", {"project": base["p_sales"].id, "quote": qid, "basis": "CONTRACT", "order_ref": "AQ-1", "confirm_now": True}, format="json")
    r = client_for("assistant").post(f"/api/quotations/{qid}/status/", {"status": "SENT"}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "has_order"


# ======================= 报价：乐观并发、参考价、GROSS 口径、手工行入库 =======================
def test_stale_version_rejected(client_for, make_quote):
    c = client_for("assistant")
    _, rid = make_quote(render=False)
    v = c.get(f"/api/quote-revisions/{rid}/").json()["lock_version"]
    assert c.patch(f"/api/quote-revisions/{rid}/", {"remark": "A", "expected_version": v}, format="json").status_code == 200
    r = c.patch(f"/api/quote-revisions/{rid}/", {"remark": "B", "expected_version": v}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "stale_version"


def test_gross_price_basis_and_reference_rates(client_for, base, make_quote, users):
    from apps.organizations.models import LegalEntity

    LegalEntity.objects.filter(pk=base["ents"][2].id).update(price_basis="GROSS")
    from tests.conftest import sign_template  # RC2：价税口径属于签收绑定配置，变更后需负责人重新签收

    sign_template(LegalEntity.objects.get(pk=base["ents"][2].id), users["leader"])
    c = client_for("assistant")
    _, rid = make_quote(entity=base["ents"][2], lines=[], render=False)
    r = c.post(f"/api/quote-revisions/{rid}/lines/", {"name": "工业拖令电缆", "model": "CAB-3×35mm²", "unit": "米", "qty": "120", "multiplier": "2", "unit_price": "113.00", "tax_rate": "0.13"}, format="json")
    ln = r.json()["lines"][0]
    assert (ln["line_gross"], ln["line_net"], ln["line_tax"]) == ("27120.00", "24000.00", "3120.00")
    c.patch(f"/api/quote-revisions/{rid}/", {"ref_copper_price": "长江有色 1# 铜 78,500 元/吨", "ref_valid_until": "2026-12-31"}, format="json")
    r = c.post(f"/api/quote-revisions/{rid}/render/")
    assert r.status_code == 200
    from apps.quotations.models import QuoteRevision

    snap = QuoteRevision.objects.get(pk=rid).snapshot
    assert snap["price_basis"] == "GROSS" and "78,500" in snap["reference"]["copper"]


def test_manual_line_save_as_product_does_not_touch_quote(client_for, base, make_quote):
    c = client_for("assistant")
    _, rid = make_quote(lines=[], render=False)
    r = c.post(f"/api/quote-revisions/{rid}/lines/", {"name": "非标支架", "unit_price": "999", "unit": "件"}, format="json")
    line = r.json()["lines"][0]
    r = c.post(f"/api/quote-revisions/{rid}/save-product/", {"line": line["id"], "sku": "NB-001", "category": base["prods"][0].category_id}, format="json")
    assert r.status_code == 201
    assert c.get(f"/api/quote-revisions/{rid}/").json()["lines"][0]["product"] is None
    assert client_for("sales").post(f"/api/quote-revisions/{rid}/save-product/", {}, format="json").status_code == 403


def test_frozen_preview_returns_archived_bytes(client_for, make_quote):
    import hashlib

    from apps.quotations import pdf as pdfmod

    c = client_for("assistant")
    _, rid = make_quote()
    sha = c.get(f"/api/quote-revisions/{rid}/").json()["pdf_sha256"]
    old = pdfmod.TEMPLATE_VERSION
    pdfmod.TEMPLATE_VERSION = "9999-upgrade"  # 模拟升级后模板变化
    try:
        assert hashlib.sha256(c.get(f"/api/quote-revisions/{rid}/preview/").content).hexdigest() == sha
        assert hashlib.sha256(c.get(f"/api/quote-revisions/{rid}/pdf/").content).hexdigest() == sha
    finally:
        pdfmod.TEMPLATE_VERSION = old


# ======================= 案例 =======================
def _pub(**kw):
    d = dict(source="MANUAL", status="CONFIRMED", title="公开案例", year=2026, region="华北", product_summary="制动器", internal_amount="123456", publishable=True, owner_org="秘密业主")
    d.update(kw)
    return Performance.objects.create(**d)


def test_sales_can_browse_and_generate_public_cases_only(client_for):
    p = _pub()
    hidden = _pub(publishable=False, title="内部")
    c = client_for("sales")
    lst = c.get("/api/public-cases/").json()
    assert [x["id"] for x in lst] == [p.id] and "123456" not in json.dumps(lst) and "秘密业主" not in json.dumps(lst, ensure_ascii=False)
    assert c.post("/api/case-releases/", {"performance_ids": [hidden.id]}, format="json").status_code == 400
    r = c.post("/api/case-releases/", {"performance_ids": [p.id]}, format="json")
    assert r.status_code == 201
    assert c.get(f"/api/case-releases/{r.json()['id']}/pdf/").status_code == 200


def test_case_withdraw_blocks_download(client_for):
    p = _pub()
    r = client_for("leader").post("/api/case-releases/", {"performance_ids": [p.id]}, format="json")
    cid = r.json()["id"]
    assert client_for("sales").post(f"/api/case-releases/{cid}/withdraw/", {"reason": "x"}, format="json").status_code == 403
    assert client_for("leader").post(f"/api/case-releases/{cid}/withdraw/", {"reason": "业主要求撤下"}, format="json").status_code == 200
    assert client_for("leader").get(f"/api/case-releases/{cid}/pdf/").status_code == 410
    assert all(x["id"] != cid for x in client_for("sales").get("/api/case-releases/").json())


# ======================= 导入 300–500 条 =======================
def test_import_400_performances_idempotent(client_for):
    import io

    from openpyxl import Workbook

    from apps.imports.service import TEMPLATES

    wb = Workbook()
    ws = wb.active
    ws.append(TEMPLATES["performances"])
    for i in range(400):
        ws.append([f"合成业绩{i:03d}", 2018 + i % 8, "华东", "港口", "制动器", "斗轮机", "", "", "", 1000 + i, "", f"SYN-{i:03d}"])
    b = io.BytesIO()
    wb.save(b)
    c = client_for("assistant")
    r = c.post("/api/import-batches/preview/", {"kind": "performances", "file": SimpleUploadedFile("h.xlsx", b.getvalue())}, format="multipart")
    assert r.json()["summary"] == {"new": 400, "duplicate": 0, "error": 0, "total": 400}
    assert c.post(f"/api/import-batches/{r.json()['id']}/commit/").json()["result"]["created"] == 400
    r = c.post("/api/import-batches/preview/", {"kind": "performances", "file": SimpleUploadedFile("h.xlsx", b.getvalue())}, format="multipart")
    assert r.json()["summary"]["duplicate"] == 400
    assert Performance.objects.count() == 400


@pytest.mark.parametrize("url", ["/api/projects/", "/api/accounts/", "/api/contacts/", "/api/public-cases/"])
def test_admin_cannot_browse_business_data(client_for, base, url):
    assert client_for("admin").get(url).status_code == 403


def test_admin_dashboard_has_no_business_numbers(client_for, base, make_quote):
    make_quote()
    d = client_for("admin").get("/api/dashboard/").json()
    assert set(d) == {"role", "detail"}


# ======================= 第二轮复审回归 =======================
def test_quote_date_bounded(client_for, make_quote):
    _, rid = make_quote(render=False)
    r = client_for("assistant").patch(f"/api/quote-revisions/{rid}/", {"quote_date": "9999-12-31"}, format="json")
    assert r.status_code == 400
    assert client_for("assistant").get("/api/quotations/").status_code == 200


def test_finance_can_pause_and_resume_only_with_order(client_for, base):
    o = confirm(client_for, base, ref="PZ-1")
    pid = base["p_sales"].id
    f = client_for("finance")
    assert f.post(f"/api/projects/{pid}/execution-phase/", {"phase": "暂停", "note": "等待现场"}, format="json").status_code == 200
    assert f.post(f"/api/projects/{pid}/execution-phase/", {"phase": "执行中"}, format="json").status_code == 200
    client_for("leader").post(f"/api/order-confirmations/{o['id']}/revoke/", {"reason": "作废"}, format="json")
    assert Project.objects.get(pk=pid).execution_phase == "暂停"
    r = f.post(f"/api/projects/{pid}/execution-phase/", {"phase": "执行中"}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "no_order"
    confirm(client_for, base, ref="PZ-2")
    assert Project.objects.get(pk=pid).execution_phase == "执行中"  # 重新确认后自动恢复


def test_sales_cannot_move_won_project_back(client_for, base):
    confirm(client_for, base, ref="WB-1")
    pid = base["p_sales"].id
    c = client_for("sales")
    assert c.patch(f"/api/projects/{pid}/", {"presale_phase": "投标中"}, format="json").status_code == 400
    assert c.post(f"/api/projects/{pid}/updates/", {"stream": "PRESALE", "title": "x", "phase": "投标中"}, format="json").status_code == 403
    assert Project.objects.get(pk=pid).presale_phase == "成交"


def test_cancelled_quote_cannot_be_edited_or_confirmed(client_for, base, make_quote):
    qid, rid = make_quote()
    c = client_for("assistant")
    o = c.post("/api/order-confirmations/", {"project": base["p_sales"].id, "quote": qid, "basis": "CONTRACT", "order_ref": "CQ-1"}, format="json").json()
    c.post(f"/api/quotations/{qid}/revisions/")
    c.post(f"/api/quotations/{qid}/status/", {"status": "CANCELLED"}, format="json")
    draft = c.get(f"/api/quotations/{qid}/").json()["latest"]["id"]
    assert c.patch(f"/api/quote-revisions/{draft}/", {"remark": "x"}, format="json").json()["code"] == "cancelled"
    r = client_for("leader").post(f"/api/order-confirmations/{o['id']}/confirm/")
    assert r.status_code == 409 and r.json()["code"] == "quote_cancelled"


def test_finance_confirm_then_leader_sets_amount_syncs_performance(client_for, base):
    o = confirm(client_for, base, ref="AM-1", amount=None, who="finance")
    assert client_for("finance").post(f"/api/order-confirmations/{o['id']}/amount/", {"contract_amount": "1"}, format="json").status_code == 403
    r = client_for("assistant").post(f"/api/order-confirmations/{o['id']}/amount/", {"contract_amount": "123456.70"}, format="json")
    assert r.status_code == 200 and r.json()["contract_amount"] == "123456.70"
    assert str(Performance.objects.get(order_confirmation_id=o["id"]).internal_amount) == "123456.70"


def test_admin_audit_hides_business_summaries(client_for, make_quote):
    make_quote()
    rows = client_for("admin").get("/api/audit-events/").json()["results"]
    biz = [r for r in rows if r["resource"] in ("quote", "quote_revision", "project")]
    assert biz and all("业务操作" in r["summary"] for r in biz)
    lead = client_for("leader").get("/api/audit-events/").json()["results"]
    assert any("QT-" in r["summary"] for r in lead)


def test_legal_entity_bank_info_hidden_from_sales_finance(client_for, base):
    from apps.organizations.models import LegalEntity

    LegalEntity.objects.update(bank_account="6222 0000 1111", tax_no="9100X")
    for role in ("sales", "finance"):
        body = client_for(role).get("/api/legal-entities/").content.decode()
        assert "6222" not in body and "9100X" not in body and "default_terms" not in body
    assert "6222" in client_for("assistant").get("/api/legal-entities/").content.decode()


def test_sales_cannot_delete_contact_and_order_attachments_hidden_from_finance(client_for, base):
    from apps.organizations.models import Contact

    cid = Contact.objects.first().id
    assert client_for("sales").delete(f"/api/contacts/{cid}/").status_code == 403
    o = confirm(client_for, base, ref="ATT-1")
    r = client_for("finance").post("/api/attachments/", {"owner_type": "order", "owner_id": o["id"], "file": SimpleUploadedFile("合同.pdf", b"%PDF-1.4 x")}, format="multipart")
    assert r.status_code == 201
    assert client_for("finance").get(f"/api/attachments/{r.json()['id']}/download/").status_code == 403
    assert client_for("leader").get(f"/api/attachments/{r.json()['id']}/download/").status_code == 200


def test_superscript_owner_id_is_400(client_for, base):
    assert client_for("sales").get("/api/attachments/?owner_type=project&owner_id=²").status_code == 400


def test_sales_execution_log_has_no_order_ref(client_for, base):
    confirm(client_for, base, ref="SECRET-LOG-9")
    body = client_for("sales").get(f"/api/projects/{base['p_sales'].id}/updates/").content.decode()
    assert "SECRET-LOG-9" not in body
    assert "SECRET-LOG-9" not in client_for("sales").get("/api/dashboard/").content.decode()
