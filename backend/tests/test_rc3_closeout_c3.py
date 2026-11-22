"""RC3 收口 C3（D-7）：内部《项目询价登记单》PDF。

反例（02b5464）：GET /api/inquiries/{id}/pdf/ 不存在（404），询价只能登记与列表查询。
"""
import re

import pytest

from apps.audit.models import AuditEvent
from tests.test_rc3_g3_internal_pdf import _text

pytestmark = pytest.mark.django_db


def _mk(client_for, base, who="finance"):
    r = client_for(who).post("/api/inquiries/", {"project": base["p_sales"].id, "inquirer": base["accs"][0].id, "inquirer_contact": "王工 139xxxx", "received_on": "2026-11-18",
                                                 "due_on": "2026-11-25", "requirement": "斗轮机集中润滑改造\n需含安装指导",
                                                 "items": [{"model": "LUB-320", "name": "集中润滑系统", "qty": "2", "unit": "套", "note": "含管路"}]}, format="json")
    assert r.status_code == 201
    return r.json()


def test_C3_inquiry_pdf_content_marks_and_audit(client_for, base, make_quote):
    inq = _mk(client_for, base)
    # 关联一份含价格的报价：PDF 只能出现报价编号，不能出现任何价格
    q = client_for("assistant").post("/api/quotations/", {"project": base["p_sales"].id, "recipient": base["accs"][0].id, "legal_entity": base["ents"][0].id, "inquiry": inq["id"]}, format="json").json()
    client_for("assistant").post(f"/api/quote-revisions/{q['latest']['id']}/lines/", {"product": base["prods"][0].id, "qty": "2", "unit_price": "38888"}, format="json")
    r = client_for("finance").get(f"/api/inquiries/{inq['id']}/pdf/")
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and r.content.startswith(b"%PDF")
    t = re.sub(r"[ \t]+", "", _text(r.content))
    for want in ("项目询价登记单", inq["code"], "内部询价登记单", "非对外正式报价", "不含价格", "LUB-320", "集中润滑系统", "含管路", "王工", "2026-11-18", "2026-11-25",
                 "斗轮机集中润滑改造", q["code"], "生成人：finance", "报价中", "第1页/共1页"):
        assert want.replace(" ", "") in t, want
    for secret in ("38888", "39000", "单价", "含税", "合计", "¥"):
        assert secret not in t, secret
    assert b"38888" not in r.content
    ev = AuditEvent.objects.filter(resource="inquiry", action="export_pdf").latest("id")
    assert ev.resource_id == str(inq["id"]) and ev.actor_name == "finance" and ev.detail["pdf_sha256"] == r["X-Content-SHA256"] and ev.at is not None


def test_C3_closed_inquiry_still_downloadable_and_roles(client_for, base):
    inq = _mk(client_for, base)
    assert client_for("finance").patch(f"/api/inquiries/{inq['id']}/", {"status": "CLOSED"}, format="json").status_code == 200
    for who in ("leader", "assistant", "finance"):
        r = client_for(who).get(f"/api/inquiries/{inq['id']}/pdf/")
        assert r.status_code == 200, who
    assert "已关闭" in _text(client_for("leader").get(f"/api/inquiries/{inq['id']}/pdf/").content)
    for who in ("sales", "sales2", "admin"):
        assert client_for(who).get(f"/api/inquiries/{inq['id']}/pdf/").status_code == 403, who
    assert client_for("finance").get("/api/inquiries/999999/pdf/").status_code == 404
    assert client_for("finance").post(f"/api/inquiries/{inq['id']}/pdf/").status_code == 405


def test_C3_whitelist_snapshot_only():
    from apps.quotations import inquiry_pdf

    assert inquiry_pdf.INQUIRY_ITEM_FIELDS == ("model", "name", "qty", "unit", "note")
    src = open(inquiry_pdf.__file__, encoding="utf-8").read()
    for bad in ("unit_price", "total_gross", "contract_amount", "internal_amount"):
        assert bad not in src, bad
