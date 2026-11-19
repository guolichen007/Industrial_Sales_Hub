"""POC-C：五角色服务端权限矩阵（行级+字段级）与对外案例脱敏。"""
import json
import re

import pytest

from apps.performances.models import Performance

SENSITIVE_KEYS = {"internal_amount", "contract_amount", "unit_price", "total_gross", "total_net", "base_price", "amount", "line_gross", "remark"}


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from _keys(x)


# ---------- 报价中心访问矩阵 ----------
@pytest.mark.parametrize("role,expected", [("admin", 200), ("leader", 200), ("assistant", 200), ("sales", 403), ("finance", 403)])
def test_quote_list_access(role, expected, client_for, make_quote):
    make_quote()
    assert client_for(role).get("/api/quotations/").status_code == expected


@pytest.mark.parametrize("role", ["sales", "finance"])
def test_U05_sales_finance_cannot_read_quote_detail_revision_or_pdf(role, client_for, make_quote, base):
    qid, rid = make_quote()
    c = client_for(role)
    for url in [f"/api/quotations/{qid}/", f"/api/quote-revisions/{rid}/", f"/api/quote-revisions/{rid}/pdf/", f"/api/quote-revisions/{rid}/preview/"]:
        assert c.get(url).status_code == 403, url
    assert c.post("/api/quotations/", {"project": base["p_sales"].id, "recipient": base["accs"][0].id, "legal_entity": base["ents"][0].id}, format="json").status_code == 403
    assert c.post(f"/api/quote-revisions/{rid}/render/").status_code == 403


def test_sales_project_detail_and_related_never_contain_amounts(client_for, make_quote, base):
    make_quote()
    lc = client_for("leader")
    lc.post("/api/order-confirmations/", {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "basis": "CONTRACT", "order_ref": "HT-S", "contract_amount": "99999", "confirm_now": True}, format="json")
    lc.post(f"/api/projects/{base['p_sales'].id}/finance/", {"kind": "RECEIPT", "amount": "50000", "happened_on": "2026-11-01"}, format="json")
    c = client_for("sales")
    for url in [f"/api/projects/{base['p_sales'].id}/", f"/api/projects/{base['p_sales'].id}/related/", "/api/projects/", "/api/dashboard/", "/api/performances/", f"/api/projects/{base['p_sales'].id}/milestones/", "/api/products/"]:
        r = c.get(url)
        assert r.status_code == 200, url
        body = r.content.decode()
        leaked = SENSITIVE_KEYS & set(_keys(r.json()))
        assert not leaked, (url, leaked)
        assert "99999" not in body and "50000" not in body and "39000" not in body and "88140" not in body, url
    rel = c.get(f"/api/projects/{base['p_sales'].id}/related/").json()
    assert rel["quotes"] == [] and rel["orders"] == []
    assert len(rel["performances"]) == 1


def test_sales_quote_summary_flag_returns_whitelisted_fields_only(client_for, make_quote, base, settings):
    settings.SALES_QUOTE_SUMMARY = True
    make_quote()
    rel = client_for("sales").get(f"/api/projects/{base['p_sales'].id}/related/").json()
    assert len(rel["quotes"]) == 1
    assert set(rel["quotes"][0]) == {"id", "code", "recipient_name", "legal_entity_name", "status_label", "updated_at"}


def test_sales_row_level_scope(client_for, base):
    c = client_for("sales")
    ids = {p["id"] for p in c.get("/api/projects/").json()}
    assert base["p_sales"].id in ids and base["p_other"].id not in ids
    assert c.get(f"/api/projects/{base['p_other'].id}/").status_code == 404
    assert c.patch(f"/api/projects/{base['p_other'].id}/", {"name": "x"}, format="json").status_code == 404
    assert c.post(f"/api/projects/{base['p_other'].id}/updates/", {"stream": "PRESALE", "title": "x"}, format="json").status_code == 404


def test_sales_see_all_flag_still_blocks_writes_to_others(client_for, base, settings):
    settings.SALES_SEE_ALL_PROJECTS = True
    c = client_for("sales")
    assert c.get(f"/api/projects/{base['p_other'].id}/").status_code == 200
    assert c.patch(f"/api/projects/{base['p_other'].id}/", {"name": "x"}, format="json").status_code == 403


def test_sales_cannot_change_owner(client_for, base, users):
    r = client_for("sales").patch(f"/api/projects/{base['p_sales'].id}/", {"sales_owner": users["sales2"].id}, format="json")
    assert r.status_code == 403


# ---------- 进度两条线的写权限 ----------
@pytest.mark.parametrize("role,presale,execution", [
    ("admin", 201, 201), ("leader", 201, 201), ("assistant", 201, 201), ("sales", 201, 403), ("finance", 403, 201),
])
def test_update_stream_write_matrix(role, presale, execution, client_for, base):
    pid = base["p_sales"].id
    c = client_for(role)
    assert c.post(f"/api/projects/{pid}/updates/", {"stream": "PRESALE", "title": "跟进"}, format="json").status_code == presale
    assert c.post(f"/api/projects/{pid}/updates/", {"stream": "EXECUTION", "title": "执行"}, format="json").status_code == execution


def test_U07_finance_updates_milestone_sales_sees_safe_timeline(client_for, base):
    pid = base["p_sales"].id
    client_for("finance").post(f"/api/projects/{pid}/milestones/")
    ms = client_for("finance").get(f"/api/projects/{pid}/milestones/").json()
    mid = next(m["id"] for m in ms if m["stage"] == "发货")
    r = client_for("finance").patch(f"/api/projects/{pid}/milestones/{mid}/", {"state": "DONE", "note": "已发货"}, format="json")
    assert r.status_code == 200
    assert client_for("sales").patch(f"/api/projects/{pid}/milestones/{mid}/", {"state": "PENDING"}, format="json").status_code == 403
    view = client_for("sales").get(f"/api/projects/{pid}/").json()
    assert any(m["stage"] == "发货" and m["state"] == "DONE" for m in view["milestones"])
    assert client_for("sales").get(f"/api/projects/{pid}/finance/").status_code == 403
    assert client_for("assistant").get(f"/api/projects/{pid}/finance/").status_code == 403
    assert client_for("finance").get(f"/api/projects/{pid}/finance/").status_code == 200


def test_finance_cannot_edit_quotes_or_presale(client_for, make_quote, base):
    qid, rid = make_quote(render=False)
    c = client_for("finance")
    assert c.patch(f"/api/quote-revisions/{rid}/", {"remark": "x"}, format="json").status_code == 403
    assert c.post(f"/api/quotations/{qid}/status/", {"status": "CANCELLED"}, format="json").status_code == 403
    assert c.patch(f"/api/projects/{base['p_sales'].id}/", {"name": "x"}, format="json").status_code == 403


# ---------- 产品与账号 ----------
@pytest.mark.parametrize("role,expected", [("admin", 201), ("leader", 201), ("assistant", 201), ("sales", 403), ("finance", 403)])
def test_product_write_matrix(role, expected, client_for, base):
    r = client_for(role).post("/api/products/", {"sku": f"N-{role}", "model": "N", "name": "新品", "category": base["prods"][0].category_id, "base_price": "10"}, format="json")
    assert r.status_code == expected


def test_products_cannot_be_deleted(client_for, base):
    assert client_for("admin").delete(f"/api/products/{base['prods'][0].id}/").status_code == 405


@pytest.mark.parametrize("role,expected", [("admin", 200), ("leader", 403), ("assistant", 403), ("sales", 403), ("finance", 403)])
def test_user_management_admin_only(role, expected, client_for):
    assert client_for(role).get("/api/users/").status_code == expected


def test_disabled_user_session_invalidated(users):
    from rest_framework.test import APIClient

    c = APIClient()
    assert c.post("/api/auth/login/", {"username": "sales", "password": "Test@12345"}, format="json").status_code == 200
    assert c.get("/api/auth/me/").status_code == 200
    admin = APIClient()
    admin.force_authenticate(users["admin"])
    assert admin.patch(f"/api/users/{users['sales'].id}/", {"is_active": False}, format="json").status_code == 200
    assert c.get("/api/auth/me/").status_code == 403
    assert c.post("/api/auth/login/", {"username": "sales", "password": "Test@12345"}, format="json").status_code == 400


def test_unauthenticated_is_rejected(db):
    from rest_framework.test import APIClient

    for url in ["/api/projects/", "/api/quotations/", "/api/performances/", "/api/dashboard/"]:
        assert APIClient().get(url).status_code == 403


def test_login_rate_limited(users):
    from django.core.cache import cache
    from rest_framework.test import APIClient

    cache.clear()
    c = APIClient()
    for _ in range(8):
        c.post("/api/auth/login/", {"username": "leader", "password": "bad"}, format="json")
    r = c.post("/api/auth/login/", {"username": "leader", "password": "Test@12345"}, format="json")
    assert r.status_code == 429
    cache.clear()


# ---------- 业绩与案例 ----------
def _perf(**kw):
    d = dict(source="MANUAL", status="CONFIRMED", title="东部电厂输煤制动器项目", year=2026, region="华北", industry="电厂", product_summary="安全制动器",
             machine_type="皮带机", host_maker="秘密主机厂", owner_org="秘密业主集团", investor="秘密投资方", internal_amount="987654.32",
             remark="内部备注-毛利率高", public_summary="完成输煤系统制动设备升级。", publishable=True)
    d.update(kw)
    return Performance.objects.create(**d)


@pytest.mark.parametrize("role,see_amount", [("admin", True), ("leader", True), ("assistant", True), ("finance", True), ("sales", False)])
def test_performance_amount_field_level(role, see_amount, client_for, users):
    _perf(sales_owner=users["sales"])
    rows = client_for(role).get("/api/performances/").json()
    assert len(rows) == 1
    assert ("internal_amount" in rows[0]) is see_amount


def test_U08_case_pdf_and_api_contain_no_sensitive_data(client_for, base):
    p1 = _perf()
    p2 = _perf(title="石化园区集中润滑项目", owner_org="可公开业主", public_name_authorized=True, internal_amount="320000")
    c = client_for("assistant")
    prev = c.post("/api/case-preview/", {"performance_ids": [p1.id, p2.id]}, format="json")
    assert prev.status_code == 200
    body = prev.content.decode()
    for bad in ["987654", "320000", "秘密业主集团", "秘密主机厂", "秘密投资方", "内部备注", "internal_amount"]:
        assert bad not in body
    assert "可公开业主" in body  # 已授权公开的业主名称可以出现
    r = c.post("/api/case-releases/", {"performance_ids": [p1.id, p2.id], "title": "项目案例集", "legal_entity": base["ents"][0].id}, format="json")
    assert r.status_code == 201, r.content
    rel = r.json()
    assert "987654" not in json.dumps(rel, ensure_ascii=False)
    pdf = c.get(f"/api/case-releases/{rel['id']}/pdf/").content
    assert pdf.startswith(b"%PDF")
    # 字节级扫描（包含 metadata）：金额数字不可出现
    for bad in [b"987654", b"987,654", b"320000", b"320,000"]:
        assert bad not in pdf
    meta = re.findall(rb"/(Title|Author|Subject|Keywords|Creator|Producer) \(([^)]*)\)", pdf)
    assert all(b"98765" not in v for _, v in meta)
    # 文本层扫描
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf)
        f.flush()
        try:
            text = subprocess.run(["pdftotext", f.name, "-"], capture_output=True, text=True, timeout=30).stdout
        except FileNotFoundError:
            text = None
    if text is not None:
        for bad in ["987654", "987,654", "秘密业主集团", "秘密主机厂", "秘密投资方", "内部备注"]:
            assert bad not in text
        assert "东部电厂输煤制动器项目" in text


def test_case_release_rejects_unconfirmed_or_unpublishable(client_for):
    p1 = _perf(status="PENDING")
    p2 = _perf(publishable=False)
    c = client_for("leader")
    for pid in (p1.id, p2.id):
        r = c.post("/api/case-releases/", {"performance_ids": [pid]}, format="json")
        assert r.status_code == 400 and r.json()["code"] == "not_publishable"


@pytest.mark.parametrize("role,expected", [("admin", 201), ("leader", 201), ("assistant", 201), ("sales", 403), ("finance", 403)])
def test_case_release_write_matrix(role, expected, client_for):
    p = _perf()
    assert client_for(role).post("/api/case-releases/", {"performance_ids": [p.id]}, format="json").status_code == expected


def test_audit_log_records_and_redacts(client_for, make_quote):
    from apps.audit.models import AuditEvent

    make_quote()
    assert AuditEvent.objects.filter(action="generate_pdf").exists()
    for e in AuditEvent.objects.all():
        assert "39000" not in json.dumps(e.detail, ensure_ascii=False)
    assert client_for("sales").get("/api/audit-events/").status_code == 403
    assert client_for("leader").get("/api/audit-events/").status_code == 200


def test_error_shape_has_request_id(client_for):
    r = client_for("sales").get("/api/quotations/")
    assert set(r.json()) == {"code", "message", "field_errors", "request_id"} and r.json()["request_id"]
