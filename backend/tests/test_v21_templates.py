"""V2.1 报价模板（工程层面）：三模板 × 短/长清单、特殊字符、口径、分页、冻结字节。

真实样张未提供：本文件只能验证模板引擎与工程口径，**不代表真实模板或财务口径签收**（LAN-06 业务验收 BLOCKED）。
"""
import re
import subprocess
import tempfile
from decimal import Decimal

import pytest

from apps.common.money import line_amounts, rmb_upper, round2
from apps.organizations.models import LegalEntity
from apps.quotations.models import QuoteRevision

# 工程计算样例（非财务签收）：(口径, 数量, 乘数, 单价, 税率, 折扣, 期望未税, 期望税额, 期望含税)
ENGINEERING_SAMPLES = [
    ("NET", "2", "1", "39000", "0.13", "0", "78000.00", "10140.00", "88140.00"),
    ("NET", "120", "1", "126", "0.13", "0", "15120.00", "1965.60", "17085.60"),
    ("NET", "4", "1", "18200", "0.13", "0.05", "69160.00", "8990.80", "78150.80"),
    ("NET", "3", "1", "0.335", "0.13", "0", "1.01", "0.13", "1.14"),
    ("NET", "2", "150", "86.5", "0.13", "0", "25950.00", "3373.50", "29323.50"),  # 2 盘 × 150 米/盘
    ("NET", "1", "1", "0", "0.13", "0", "0.00", "0.00", "0.00"),
    ("GROSS", "120", "2", "113.00", "0.13", "0", "24000.00", "3120.00", "27120.00"),
    ("GROSS", "3", "1", "33333.33", "0.13", "0", "88495.57", "11504.42", "99999.99"),
    ("GROSS", "1", "1", "1", "0.13", "0", "0.88", "0.12", "1.00"),
    ("NET", "0.5", "1", "28600", "0.09", "0.03", "13871.00", "1248.39", "15119.39"),
]


@pytest.mark.parametrize("basis,qty,mul,price,tax,disc,net,tx,gross", ENGINEERING_SAMPLES)
def test_engineering_samples(basis, qty, mul, price, tax, disc, net, tx, gross):
    n, t, g = line_amounts(Decimal(qty) * Decimal(mul), price, tax, disc, price_basis=basis)
    assert (str(n), str(t), str(g)) == (net, tx, gross)
    assert n + t == g


def test_upper_amount_edge_cases():
    assert rmb_upper("0") == "零元整"
    assert rmb_upper("99999.99") == "玖万玖仟玖佰玖拾玖元玖角玖分"
    assert rmb_upper("100000000.10") == "壹亿元壹角整"


STRESS_LINES = [
    {"name": "超长型号测试电缆卷筒总成（含集电器、导缆架、限位开关）", "model": "CRL-800/120-YJV22-0.6/1kV-3×35+1×16mm²-ABCDEFGHIJKLMNOPQRSTUVWXYZ-0123456789", "unit": "套", "qty": "1", "unit_price": "28600"},
    {"name": "精密测量传感器", "model": "µS-200", "unit": "台", "qty": "3", "unit_price": "1250.5",
     "params": [{"key": "r", "label": "量程", "value": "0～200", "unit": "µm"}, {"key": "t", "label": "工作温度", "value": "-20～60", "unit": "℃"}, {"key": "m", "label": "制动力矩", "value": "6000", "unit": "N·m"}]},
    {"name": "工业拖令电缆", "model": "CAB-3×35", "unit": "米", "qty": "2", "multiplier": "150", "unit_price": "86.5"},
    {"name": "赠送备件", "model": "—", "unit": "件", "qty": "1", "unit_price": "0"},
    {"name": "折扣项目", "model": "BRK-600", "unit": "台", "qty": "4", "unit_price": "18200", "discount_rate": "0.05", "description": "长参数说明：" + "液压释放、手动释放、磨损自动补偿、限位检测、" * 12},
]


def _make(client_for, base, entity, lines, render=True, **rev):
    c = client_for("assistant")
    r = c.post("/api/quotations/", {"project": base["p_sales"].id, "recipient": base["accs"][1].id, "legal_entity": entity.id}, format="json")
    rid = r.json()["latest"]["id"]
    if rev:
        c.patch(f"/api/quote-revisions/{rid}/", rev, format="json")
    for ln in lines:
        rr = c.post(f"/api/quote-revisions/{rid}/lines/", ln, format="json")
        assert rr.status_code == 201, rr.content
    if render:
        rr = c.post(f"/api/quote-revisions/{rid}/render/")
        assert rr.status_code == 200, rr.content.decode()[:600]
    return rid


def _text(pdf: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf)
        f.flush()
        try:
            return subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True, timeout=30).stdout
        except FileNotFoundError:
            pytest.skip("需要 pdftotext")


def _pages(pdf):
    return len(re.findall(rb"/Type /Page[^s]", pdf))


@pytest.mark.parametrize("code", ["A", "B", "C"])
def test_each_template_short_and_long(client_for, base, code):
    ent = LegalEntity.objects.get(code=code)
    c = client_for("assistant")
    short = _make(client_for, base, ent, STRESS_LINES[:2])
    long_ = _make(client_for, base, ent, [dict(STRESS_LINES[i % 5], name=f"{STRESS_LINES[i % 5]['name']}-{i + 1}") for i in range(40)])
    ps = c.get(f"/api/quote-revisions/{short}/pdf/").content
    pl = c.get(f"/api/quote-revisions/{long_}/pdf/").content
    assert _pages(ps) == 1 and _pages(pl) >= 3
    t = re.sub(r"\s+", "", _text(pl))
    n = _pages(pl)
    assert f"第{n}页/共{n}页" in t and "第1页/共" in t
    rev = QuoteRevision.objects.get(pk=long_)
    assert rmb_upper(rev.total_gross) in t


def test_special_characters_render_and_are_extractable(client_for, base):
    ent = LegalEntity.objects.get(code="A")
    rid = _make(client_for, base, ent, STRESS_LINES, ref_copper_price="1# 电解铜 78,500 元/吨", ref_exchange_rate="USD/CNY 7.12", ref_valid_until="2026-12-31")
    t = re.sub(r"\s+", "", _text(client_for("assistant").get(f"/api/quote-revisions/{rid}/pdf/").content))
    for s in ["µm", "℃", "N·m", "mm²", "3×35", "2×150", "78,500", "USD/CNY7.12", "优惠：5%", "0～200µm"]:
        assert s in t, s


def test_gross_basis_and_no_tax_column_template(client_for, base, users):
    """RC2 调整说明：r3 模板 C 按真实样张只有“单价/总价”列（含税口径在条款中说明），不再打印“含税单价”列名；
    改为验证：GROSS 口径不出现未税合计/税额行、标题可配置、无税率列、乘数与金额正确。另：价税口径属于签收绑定配置，修改后需重新签收。"""
    from tests.conftest import sign_template

    ent = LegalEntity.objects.get(code="C")
    ent.price_basis, ent.show_tax_columns, ent.quote_title = "GROSS", False, "产 品 报 价 单"
    ent.save()
    sign_template(ent, users["leader"])
    rid = _make(client_for, base, ent, [{"name": "电缆", "model": "C", "unit": "米", "qty": "120", "multiplier": "2", "unit_price": "113", "tax_rate": "0.13"}])
    t = re.sub(r"\s+", "", _text(client_for("assistant").get(f"/api/quote-revisions/{rid}/pdf/").content))
    assert "未税合计" not in t and "税额" not in t and "产品报价单" in t and "税率" not in t
    assert "27120" in t and "120×2" in t


def test_empty_contact_and_zero_amount(client_for, base):
    ent = LegalEntity.objects.get(code="B")
    rid = _make(client_for, base, ent, [{"name": "赠品", "model": "—", "unit": "件", "qty": "1", "unit_price": "0"}])
    t = _text(client_for("assistant").get(f"/api/quote-revisions/{rid}/pdf/").content)
    assert "零元整" in t.replace(" ", "")


def test_archive_bytes_survive_entity_change(client_for, base):
    """冻结后修改主体资料/模板口径：旧 PDF 原字节与快照不变，新版本才使用新资料。"""
    import hashlib

    ent = LegalEntity.objects.get(code="A")
    rid = _make(client_for, base, ent, STRESS_LINES[:1])
    c = client_for("assistant")
    before = c.get(f"/api/quote-revisions/{rid}/pdf/").content
    ent.name, ent.price_basis = "已更名的公司", "GROSS"
    ent.save()
    after = c.get(f"/api/quote-revisions/{rid}/pdf/").content
    assert hashlib.sha256(before).digest() == hashlib.sha256(after).digest()
    assert QuoteRevision.objects.get(pk=rid).snapshot["legal_entity"]["name"] != "已更名的公司"


def test_bundled_fonts_are_used_and_checksummed():
    import hashlib
    from pathlib import Path

    from apps.common.pdfkit import BUNDLED_DIR, register_fonts

    info = register_fonts()
    assert info["path"] == "DroidSansFallbackFull.ttf" and info["latin_path"] == "DejaVuSans.ttf"
    sums = dict(reversed(line.split()) for line in (BUNDLED_DIR / "SHA256SUMS").read_text().splitlines())
    for name, sha in sums.items():
        assert hashlib.sha256((BUNDLED_DIR / name).read_bytes()).hexdigest() == sha
    assert Path(BUNDLED_DIR / "LICENSE-DroidSansFallback-Apache-2.0.txt").exists()
