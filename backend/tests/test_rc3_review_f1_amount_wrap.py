"""RC3 定向整改 F-1：报价金额单行适配（A/B/C × 含税/未税 × 十亿/百亿 × 单套/多台 × 短单/45 行长单）。

反例（75073de 基线）：`_totals_block` 合计区金额列固定 32mm，默认 Paragraph 会把
`2,261,699,465.25` 之类的尾数挤到下一行（实测 88.8pt > 可用 82.7pt），明细单价/总价列同理，
导致金额被拆行/裁切、不可读，且可能被误读。本文件锁定修正后：金额一律单行完整显示；
超过最小可读字号仍放不下的极端金额明确拒绝（AmountTooWide），绝不产出不可读 PDF。

同时锁定：修正属于版式变更 → TEMPLATE_VERSION 提升；旧版本（r3）签收不再授权新版本（需负责人重新签收）。
"""
import re
import subprocess
import tempfile
from decimal import Decimal

import pytest

from apps.organizations.models import LegalEntity, TemplateSignoff
from apps.quotations import pdf as pdfmod
from apps.quotations.models import QuoteRevision
from apps.quotations.pdf import TEMPLATE_VERSION, AmountTooWide, _fit_font_size, money_fmt, plain

# 金额阶梯：正常 / 十亿级 / 百亿级
LADDER = [
    ("normal", "234947.60", 1),
    ("1e9", "2261699465.25", 1),
    ("1e10", "12345678901.23", 1),
    ("1e9-multi", "2261699465.25", 2),   # 整单多台，触发“N台总计”行
    ("1e10-multi", "12345678901.23", 3),
]


def _layout(pdf: bytes):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf)
        f.flush()
        try:
            out = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True, timeout=60).stdout
        except FileNotFoundError:
            pytest.skip("需要 pdftotext")
    return out.splitlines()


def _on_one_line(lines, s):
    return any(s in ln for ln in lines)


def _make_quote(c, base, ent, lines, **rev):
    r = c.post("/api/quotations/", {"project": base["p_sales"].id, "recipient": base["accs"][0].id, "legal_entity": ent.id}, format="json")
    assert r.status_code == 201, r.content
    rid = r.json()["latest"]["id"]
    if rev:
        assert c.patch(f"/api/quote-revisions/{rid}/", rev, format="json").status_code == 200
    for ln in lines:
        assert c.post(f"/api/quote-revisions/{rid}/lines/", ln, format="json").status_code == 201
    return rid


def _expected_amounts(snap):
    """模板将渲染的全部金额字符串（与 pdf._totals_block / 明细列一致）。"""
    fmt = money_fmt if snap["template_key"] == "A" else plain
    out = [fmt(snap["totals"]["set_gross"])]
    if snap.get("show_tax_breakdown"):
        out += [fmt(snap["totals"]["set_net"]), fmt(snap["totals"]["set_tax"])]
    if int(snap.get("host_qty") or 1) > 1:
        out.append(fmt(snap["totals"]["gross"]))
    out += [fmt(ln["amount"]) for ln in snap["lines"]]
    return out


# ------------------------------------------------------------------ 单元：适配函数确定性 + 阻断
def test_template_version_bumped_for_layout_change():
    assert TEMPLATE_VERSION == "2026.11-r4"


def test_fit_font_size_is_deterministic_and_blocks_when_impossible():
    # 单行放得下：保持基准字号（正常版式不退化）
    assert _fit_font_size(["265,490.79"], 82.7, 9, bold=True) == 9
    # 十亿级放不下：缩放但不低于下限，且结果确定（多次调用一致）
    s1 = _fit_font_size(["2,261,699,465.25"], 82.7 - 1.0, 9, bold=True)
    s2 = _fit_font_size(["2,261,699,465.25"], 82.7 - 1.0, 9, bold=True)
    assert 6.0 <= s1 < 9 and s1 == s2
    # 极端金额：即使最小字号也放不下 → 明确拒绝（而非生成不可读 PDF）
    with pytest.raises(AmountTooWide):
        _fit_font_size(["9,999,999,999,999,999.99"], 58.0, 8.4, bold=False)


def test_render_blocks_amount_that_cannot_fit(client_for, base, make_quote):
    """真正超出可安全排版的金额（远超数据库字段范围、无法单行放下）：渲染层明确拒绝，而非产出不可读 PDF。"""
    import copy

    from apps.quotations.pdf import render_quote_pdf

    _, rid = make_quote()
    snap = copy.deepcopy(QuoteRevision.objects.get(pk=rid).snapshot)
    huge = "9999999999999999999999999999.99"  # 30 位整数
    snap["show_tax_breakdown"] = False
    snap["totals"]["set_gross"] = huge
    snap["lines"][0]["amount"] = huge
    with pytest.raises(AmountTooWide):
        render_quote_pdf(snap)


# ------------------------------------------------------------------ DB：矩阵单行适配
@pytest.mark.django_db
@pytest.mark.parametrize("code", ["A", "B", "C"])
@pytest.mark.parametrize("basis", ["NET", "GROSS"])
@pytest.mark.parametrize("tag,unit_price,host_qty", LADDER)
def test_amounts_stay_on_single_line(client_for, base, users, code, basis, tag, unit_price, host_qty):
    from tests.conftest import sign_template

    ent = LegalEntity.objects.get(code=code)
    ent.price_basis = basis
    ent.show_tax_columns = basis == "NET"   # NET 展示未税/税额拆分；GROSS 不拆（与真实样张一致）
    ent.save()
    sign_template(ent, users["leader"])
    c = client_for("assistant")
    rid = _make_quote(c, base, ent, [{"name": "大额物料", "model": "X-1", "unit": "台", "qty": "1", "unit_price": unit_price}], host_qty=host_qty)
    r = c.post(f"/api/quote-revisions/{rid}/render/")
    assert r.status_code == 200, r.content[:300]
    pdf = c.get(f"/api/quote-revisions/{rid}/pdf/").content
    lines = _layout(pdf)
    rev = QuoteRevision.objects.get(pk=rid)
    expected = _expected_amounts(rev.snapshot)
    missing = [s for s in expected if not _on_one_line(lines, s)]
    assert not missing, f"{code}/{basis}/{tag}: 金额未单行完整显示（折行或裁切）：{missing}"
    # 金额算法不变：DB 汇总与冻结快照一致（版式修改不得改变任何金额）
    assert Decimal(rev.snapshot["totals"]["gross"]) == rev.total_gross


@pytest.mark.django_db
@pytest.mark.parametrize("code", ["A", "B", "C"])
def test_long_45_lines_with_large_amount_still_single_line(client_for, base, users, code):
    """45 行长单 + 含大额行：分页正常、大额仍单行完整。"""
    from tests.conftest import sign_template

    ent = LegalEntity.objects.get(code=code)
    ent.price_basis, ent.show_tax_columns = "NET", True
    ent.save()
    sign_template(ent, users["leader"])
    c = client_for("assistant")
    lines = [{"name": f"清单项{i}", "model": f"M-{i:03d}", "unit": "件", "qty": "1", "unit_price": "10"} for i in range(44)]
    lines.append({"name": "大额行", "model": "BIG", "unit": "台", "qty": "1", "unit_price": "2261699465.25"})
    rid = _make_quote(c, base, ent, lines, host_qty=2)
    assert c.post(f"/api/quote-revisions/{rid}/render/").status_code == 200
    pdf = c.get(f"/api/quote-revisions/{rid}/pdf/").content
    lines_out = _layout(pdf)
    rev = QuoteRevision.objects.get(pk=rid)
    for s in _expected_amounts(rev.snapshot):
        assert _on_one_line(lines_out, s), f"{code}: 大额未单行：{s}"
    n = len(re.findall(rb"/Type /Page[^s]", pdf))
    assert n >= 2 and f"第{n}页/共{n}页" in re.sub(r"\s+", "", "\n".join(lines_out))


# ------------------------------------------------------------------ 签收门禁：旧版本不授权新版本
@pytest.mark.django_db
def test_old_r3_signoff_does_not_authorize_r4(base, users):
    ent = base["ents"][0]
    TemplateSignoff.objects.filter(entity=ent).delete()
    TemplateSignoff.objects.create(entity=ent, template_key=ent.template_key, template_version="2026.11-r3",
                                   config_sha256=ent.signoff_config_sha256(), config={}, note="旧 r3 版式签收", signed_by=users["leader"])
    assert ent.current_signoff() is None  # 当前版本为 r4，旧 r3 签收不再授权
    assert ent.current_signoff("2026.11-r3") is not None  # 仅对旧版本号本身有效
