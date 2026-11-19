"""POC-A：三固定报价模板 PDF、金额黄金样例、版本冻结与哈希不变。"""
import hashlib
import json
from decimal import Decimal

import pytest

from apps.common.money import line_amounts, rmb_upper, totals
from apps.products.models import Product
from apps.quotations.models import QuoteRevision
from apps.quotations.services import rerender_frozen

# ---- 金额黄金样例（口径：未税单价，逐行舍入，ROUND_HALF_UP；D4 待甲方/财务确认）----
GOLDEN = [
    # qty, price, tax, discount, net, tax, gross
    ("2", "39000", "0.13", "0", "78000.00", "10140.00", "88140.00"),
    ("4", "18200", "0.13", "0", "72800.00", "9464.00", "82264.00"),
    ("120", "126", "0.13", "0", "15120.00", "1965.60", "17085.60"),
    ("3", "0.335", "0.13", "0", "1.01", "0.13", "1.14"),  # 1.005 → 1.01 (HALF_UP)
    ("1", "100", "0.13", "0.05", "95.00", "12.35", "107.35"),
    ("2.5", "1999.99", "0.09", "0", "4999.98", "450.00", "5449.98"),  # 4999.975 → 4999.98
    ("7", "33.33", "0.06", "0.1", "209.98", "12.60", "222.58"),
    ("1", "0", "0.13", "0", "0.00", "0.00", "0.00"),
    ("1000", "0.01", "0.13", "0", "10.00", "1.30", "11.30"),
    ("0.5", "28600", "0.13", "0.03", "13871.00", "1803.23", "15674.23"),
]


@pytest.mark.parametrize("qty,price,tax,disc,net,tx,gross", GOLDEN)
def test_golden_amounts(qty, price, tax, disc, net, tx, gross):
    assert tuple(map(str, line_amounts(qty, price, tax, disc))) == (net, tx, gross)


def test_float_is_never_used_for_money():
    # 0.1+0.2 浮点误差不应影响
    n, t, g = line_amounts(3, "0.1", "0", "0")
    assert str(n) == "0.30"
    assert totals([{"line_net": "0.10", "line_tax": "0.01"}, {"line_net": "0.20", "line_tax": "0.03"}]) == (Decimal("0.30"), Decimal("0.04"), Decimal("0.34"))


@pytest.mark.parametrize("amount,upper", [("88140", "捌万捌仟壹佰肆拾元整"), ("10010", "壹万零壹拾元整"), ("1001.05", "壹仟零壹元零伍分"), ("100000000", "壹亿元整"), ("0.5", "伍角整"), ("2010000", "贰佰零壹万元整")])
def test_rmb_upper(amount, upper):
    assert rmb_upper(amount) == upper


def test_three_templates_generate_pdf(make_quote, base, client_for):
    c = client_for("assistant")
    shas = set()
    for ent in base["ents"]:
        _, rid = make_quote(entity=ent, lines=[(0, "2", "0"), (1, "4", "0.05")])
        r = c.get(f"/api/quote-revisions/{rid}/pdf/")
        assert r.status_code == 200 and r.content.startswith(b"%PDF")
        rev = QuoteRevision.objects.get(pk=rid)
        assert rev.template_key == ent.template_key and rev.template_version
        assert hashlib.sha256(r.content).hexdigest() == rev.pdf_sha256
        shas.add(rev.pdf_sha256)
    assert len(shas) == 3  # 三套模板输出不同


def test_long_quote_paginates(make_quote, client_for):
    _, rid = make_quote(lines=[(i % 2, str(i + 1), "0") for i in range(30)])
    pdf = client_for("assistant").get(f"/api/quote-revisions/{rid}/pdf/").content
    import re

    assert len(re.findall(rb"/Type /Page[^s]", pdf)) >= 2


def test_frozen_revision_is_immutable_and_v2_reflects_changes(make_quote, base, client_for):
    c = client_for("assistant")
    qid, rid = make_quote(lines=[(0, "2", "0")])
    v1 = QuoteRevision.objects.get(pk=rid)
    v1_pdf_sha, v1_snap_sha, v1_total = v1.pdf_sha256, v1.snapshot_sha256, v1.total_gross

    # 修改产品库价格与技术参数
    p = base["prods"][0]
    p.base_price = Decimal("45000")
    p.params = [{"key": "cap", "label": "储油量", "value": "500", "unit": "L"}]
    p.save()

    # 旧版本不能修改
    assert c.patch(f"/api/quote-revisions/{rid}/", {"remark": "x"}, format="json").status_code == 409
    line_id = v1.lines.first().id
    assert c.patch(f"/api/quote-revisions/{rid}/lines/{line_id}/", {"unit_price": "1"}, format="json").status_code == 409
    assert c.post(f"/api/quote-revisions/{rid}/lines/", {"product": p.id}, format="json").status_code == 409
    assert c.post(f"/api/quote-revisions/{rid}/render/").status_code == 409

    # 从冻结快照重新渲染，PDF 字节哈希不变
    v1.refresh_from_db()
    assert hashlib.sha256(rerender_frozen(v1)).hexdigest() == v1_pdf_sha
    assert v1.total_gross == v1_total and v1.snapshot_sha256 == v1_snap_sha
    assert hashlib.sha256(c.get(f"/api/quote-revisions/{rid}/pdf/").content).hexdigest() == v1_pdf_sha

    # V2：复制后加入新价格产品行，才发生变化
    r = c.post(f"/api/quotations/{qid}/revisions/")
    assert r.status_code == 201
    v2 = r.json()
    assert v2["version"] == 2 and v2["status"] == "DRAFT"
    assert v2["lines"][0]["unit_price"] == "39000.00"  # 复制的是 V1 冻结价格，而非产品库新价
    r = c.post(f"/api/quote-revisions/{v2['id']}/lines/", {"product": p.id, "qty": "1"}, format="json")
    assert r.json()["lines"][-1]["unit_price"] == "45000.00"
    assert c.post(f"/api/quote-revisions/{v2['id']}/render/").status_code == 200
    v2r = QuoteRevision.objects.get(pk=v2["id"])
    assert v2r.pdf_sha256 != v1_pdf_sha
    v1.refresh_from_db()
    assert v1.pdf_sha256 == v1_pdf_sha


def test_snapshot_contains_only_visible_params(make_quote, client_for):
    c = client_for("assistant")
    qid, rid = make_quote(render=False)
    line = c.get(f"/api/quote-revisions/{rid}/").json()["lines"][0]
    c.patch(f"/api/quote-revisions/{rid}/lines/{line['id']}/", {"visible_param_keys": ["pr"], "unit_price": "38000"}, format="json")
    c.post(f"/api/quote-revisions/{rid}/render/")
    snap = QuoteRevision.objects.get(pk=rid).snapshot
    assert [p["key"] for p in snap["lines"][0]["params_shown"]] == ["pr"]
    assert snap["lines"][0]["unit_price"] == "38000.00"
    # 手工改价不改变产品库
    assert str(Product.objects.get(sku="LUB-320").base_price) == "39000.00"


def test_empty_quote_cannot_generate(make_quote, client_for):
    _, rid = make_quote(lines=[], render=False)
    r = client_for("assistant").post(f"/api/quote-revisions/{rid}/render/")
    assert r.status_code == 409 and r.json()["code"] == "empty_quote"


def test_backend_recalculates_ignoring_client_totals(make_quote, client_for):
    c = client_for("assistant")
    _, rid = make_quote(render=False)
    line = c.get(f"/api/quote-revisions/{rid}/").json()["lines"][0]
    r = c.patch(f"/api/quote-revisions/{rid}/lines/{line['id']}/", {"line_gross": "1.00", "qty": "3"}, format="json")
    assert r.json()["total_gross"] == "132210.00"


def test_negative_and_invalid_inputs_rejected(make_quote, client_for):
    c = client_for("assistant")
    _, rid = make_quote(render=False)
    line = c.get(f"/api/quote-revisions/{rid}/").json()["lines"][0]
    for bad in [{"qty": "-1"}, {"unit_price": "-5"}, {"tax_rate": "1.3"}, {"discount_rate": "1"}]:
        r = c.patch(f"/api/quote-revisions/{rid}/lines/{line['id']}/", bad, format="json")
        assert r.status_code == 400, bad
        assert set(r.json()) == {"code", "message", "field_errors", "request_id"}


def test_quote_codes_unique_and_status_flow(make_quote, client_for):
    c = client_for("assistant")
    q1, _ = make_quote()
    q2, _ = make_quote()
    codes = {c.get(f"/api/quotations/{q}/").json()["code"] for q in (q1, q2)}
    assert len(codes) == 2
    assert c.post(f"/api/quotations/{q1}/status/", {"status": "SENT"}, format="json").status_code == 200
    assert c.post(f"/api/quotations/{q1}/status/", {"status": "DRAFT"}, format="json").status_code == 409


def test_totals_follow_line_edits_and_deletes(make_quote, client_for):
    c = client_for("assistant")
    _, rid = make_quote(lines=[(0, "1", "0"), (1, "1", "0")], render=False)
    rev = c.get(f"/api/quote-revisions/{rid}/").json()
    assert rev["total_gross"] == "64636.00"
    r = c.patch(f"/api/quote-revisions/{rid}/lines/{rev['lines'][1]['id']}/", {"qty": "2"}, format="json")
    assert r.json()["total_gross"] == "85202.00"
    r = c.delete(f"/api/quote-revisions/{rid}/lines/{rev['lines'][0]['id']}/")
    assert r.json()["total_gross"] == "41132.00" and len(r.json()["lines"]) == 1
    assert c.get(f"/api/quote-revisions/{rid}/").json()["total_gross"] == "41132.00"
