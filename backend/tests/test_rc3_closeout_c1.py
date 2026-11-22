"""RC3 收口 C1（D-2）：财务使用完整报价中心，但报价权限与合同金额、业绩内部金额、模板签收、公开授权彻底解耦。

决策：《Industrial_Sales_Hub_RC3_最终业务决策与收口任务.md》D-2（2026-11-22）。
反例（02b5464）：财务 GET /api/quotations/ 403；若打开旧 ISH_FINANCE_QUOTE=1，则同时获得 order.amount 与 performance.see_amount。
"""
import pytest

from tests.test_v21_permissions_and_business import MONEY_KEYS, confirm, keys

pytestmark = pytest.mark.django_db
QUOTE_KEYS = {"total_gross", "total_net", "total_tax", "unit_price", "line_gross", "line_net", "base_price"}
AMOUNT_KEYS = MONEY_KEYS - QUOTE_KEYS  # 合同/业绩金额类键


def test_C1_finance_full_quote_workflow(client_for, base, users):
    from apps.organizations.models import TemplateSignoff
    from apps.quotations.pdf import TEMPLATE_VERSION

    ent = base["ents"][0]
    TemplateSignoff.objects.create(entity=ent, template_key=ent.template_key, template_version=TEMPLATE_VERSION, config_sha256=ent.signoff_config_sha256(), note="测试签收", signed_by=users["leader"])
    f = client_for("finance")
    assert {p["id"] for p in f.get("/api/products/").json()} and "base_price" in f.get("/api/products/").json()[0]  # 报价选型需要产品单价
    q = f.post("/api/quotations/", {"project": base["p_sales"].id, "recipient": base["accs"][0].id, "legal_entity": ent.id}, format="json")
    assert q.status_code == 201
    rid = q.json()["latest"]["id"]
    assert f.post(f"/api/quote-revisions/{rid}/lines/", {"product": base["prods"][0].id, "qty": "2"}, format="json").status_code == 201
    assert f.patch(f"/api/quote-revisions/{rid}/", {"payment_terms": "预付 30%"}, format="json").status_code == 200
    r = f.post(f"/api/quote-revisions/{rid}/render/")
    assert r.status_code == 200 and r.json()["is_trial"] is False and r.json()["total_gross"] not in ("0", "0.00")  # 含税正式报价
    pdf = f.get(f"/api/quote-revisions/{rid}/pdf/")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert f.post(f"/api/quotations/{q.json()['id']}/status/", {"status": "SENT"}, format="json").status_code == 200
    assert len(f.get("/api/quote-lines/history/").json()) == 1
    assert f.post(f"/api/quotations/{q.json()['id']}/revisions/").status_code == 201
    # 报价附件跟随 quote.access
    from django.core.files.uploadedfile import SimpleUploadedFile

    up = f.post("/api/attachments/", {"owner_type": "quote", "owner_id": q.json()["id"], "file": SimpleUploadedFile("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, content_type="image/png")}, format="multipart")
    assert up.status_code == 201


def test_C1_finance_still_denied_contract_and_performance_amounts(client_for, base, make_quote):
    qid, _ = make_quote()
    f = client_for("finance")
    # 订单：可关联报价，但不能录入/修改合同金额；读取时不含合同金额
    r = f.post("/api/order-confirmations/", {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "quote": qid, "basis": "CONTRACT", "order_ref": "C1-1", "contract_amount": "1"}, format="json")
    assert r.status_code == 403
    o = f.post("/api/order-confirmations/", {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "quote": qid, "basis": "CONTRACT", "order_ref": "C1-2", "confirm_now": True}, format="json")
    assert o.status_code == 201 and "contract_amount" not in o.json() and o.json()["quote_code"]
    assert f.post(f"/api/order-confirmations/{o.json()['id']}/amount/", {"contract_amount": "100"}, format="json").status_code == 403
    client_for("leader").post(f"/api/order-confirmations/{o.json()['id']}/amount/", {"contract_amount": "123456.78"}, format="json")
    for url in ("/api/order-confirmations/", f"/api/projects/{base['p_sales'].id}/related/", "/api/performances/", "/api/performances/stats/", "/api/dashboard/", f"/api/projects/{base['p_sales'].id}/"):
        body = f.get(url)
        assert body.status_code == 200, url
        assert not (AMOUNT_KEYS & set(keys(body.json()))), (url, AMOUNT_KEYS & set(keys(body.json())))
        assert "123456.78" not in body.content.decode(), url
    # 业绩：金额/内部备注/公开授权写入拒绝；业绩审批、模板签收、案例撤回拒绝
    pid = f.get("/api/performances/").json()[0]["id"]
    for body in ({"internal_amount": "1"}, {"remark": "x"}, {"publishable": True}):
        assert f.patch(f"/api/performances/{pid}/", body, format="json").status_code == 403, body
    assert f.post(f"/api/performances/{pid}/approve/").status_code == 403
    assert f.post(f"/api/legal-entities/{base['ents'][0].id}/signoff/", {"note": "财务尝试签收模板"}, format="json").status_code == 403
    xl = f.get("/api/performances/export/")
    from io import BytesIO

    from openpyxl import load_workbook

    head = [c.value for c in next(load_workbook(BytesIO(xl.content)).active.iter_rows())]
    assert "内部金额" not in head and "内部备注" not in head
    # 订单附件（合同证据）仍按 order.amount：财务不可读
    assert f.get(f"/api/attachments/?owner_type=order&owner_id={o.json()['id']}").status_code == 403


def test_C1_sales_and_admin_never_get_quotes(client_for, base, make_quote):
    qid, rid = make_quote()
    for who in ("sales", "admin"):
        c = client_for(who)
        for url in ("/api/quotations/", f"/api/quotations/{qid}/", f"/api/quote-revisions/{rid}/pdf/", "/api/quote-lines/history/"):
            assert c.get(url).status_code == 403, (who, url)
    assert "base_price" not in str(client_for("sales").get("/api/products/").json())


def test_C1_rc2_scope_keeps_finance_out_of_quotes(client_for, base, make_quote, settings):
    settings.FINANCE_SCOPE, settings.FINANCE_QUOTE = "RC2", False
    qid, _ = make_quote()
    f = client_for("finance")
    assert f.get("/api/quotations/").status_code == 403 and f.get("/api/quote-lines/history/").status_code == 403
    assert "base_price" not in str(f.get("/api/products/").json())
    o = confirm(client_for, base, ref="C1-RC2", amount=None, who="finance")
    assert "quote_code" not in o and "contract_amount" not in o
