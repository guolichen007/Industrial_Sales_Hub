"""RC2-R2：三套真实版式模板（2026.11-r3）、签收门禁、试用水印、历史冻结。

全部使用虚构数据（公开仓库）。真实样张逐项对照报告与财务黄金用例只保存在仓库外私有目录。

反例（RC1）：template_verified 只是任何“出具主体可写”角色（含管理员、商务）都能勾选的布尔值，服务端从不检查；
未签收模板生成的 PDF 与正式 PDF 无任何区别，可被误认为正式报价发送。
"""
import hashlib
import re
import subprocess
import tempfile

import pytest

from apps.organizations.models import LegalEntity, TemplateSignoff
from apps.quotations.models import QuoteRevision
from apps.quotations.pdf import TEMPLATE_VERSION
from tests.conftest import sign_template


def _text(pdf: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf)
        f.flush()
        try:
            return subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True, timeout=30).stdout
        except FileNotFoundError:
            pytest.skip("需要 pdftotext")


def _flat(pdf):
    return re.sub(r"\s+", "", _text(pdf))


def _unsign(ent):
    TemplateSignoff.objects.filter(entity=ent).delete()


def _quote(c, base, ent, lines, **rev):
    r = c.post("/api/quotations/", {"project": base["p_sales"].id, "recipient": base["accs"][0].id, "legal_entity": ent.id}, format="json")
    assert r.status_code == 201, r.content
    q = r.json()
    rid = q["latest"]["id"]
    if rev:
        assert c.patch(f"/api/quote-revisions/{rid}/", rev, format="json").status_code == 200
    for ln in lines:
        assert c.post(f"/api/quote-revisions/{rid}/lines/", ln, format="json").status_code == 201
    return q["id"], rid


# ------------------------------------------------------------------ 签收门禁
@pytest.mark.django_db
def test_unsigned_template_blocks_formal_pdf_but_allows_watermarked_trial(client_for, base):
    ent = base["ents"][0]
    _unsign(ent)
    c = client_for("assistant")
    qid, rid = _quote(c, base, ent, [{"name": "演示电缆", "model": "3x50mm²", "unit": "米", "qty": "610", "unit_price": "385.16"}])
    r = c.post(f"/api/quote-revisions/{rid}/render/")
    assert r.status_code == 409 and r.json()["code"] == "template_unsigned"
    assert QuoteRevision.objects.get(pk=rid).status == "DRAFT"
    r = c.post(f"/api/quote-revisions/{rid}/render/", {"trial": True}, format="json")
    assert r.status_code == 200 and r.json()["is_trial"] is True
    pdf = c.get(f"/api/quote-revisions/{rid}/pdf/")
    assert "TRIAL-" in pdf["Content-Disposition"] and "%E8%AF%95%E7%94%A8-" in pdf["Content-Disposition"] and pdf["X-Quote-Trial"] == "1"
    t = _flat(pdf.content)
    # 每页顶部红字说明 + 标题前缀；斜向水印为旋转文字（pdftotext 抽取顺序不稳定），以每页出现的说明文字与元数据为准
    assert "【试用报价】模板未经负责人签收" in t and "【试用】" in t and "试用（未签收）" in t
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:  # 元数据 Subject/Title 标记试用
        f.write(pdf.content)
        f.flush()
        info = subprocess.run(["pdfinfo", f.name], capture_output=True, text=True).stdout
    assert "TRIAL" in info and "【试用】" in info
    # 试用报价不能标记已发送/已接受，也不能作为合同依据
    for st in ("SENT", "ACCEPTED"):
        r = c.post(f"/api/quotations/{qid}/status/", {"status": st}, format="json")
        assert r.status_code == 409 and r.json()["code"] == "trial_quote"
    r = client_for("leader").post("/api/order-confirmations/", {"project": base["p_sales"].id, "quote": qid, "basis": "CONTRACT", "order_ref": "HT-T-1"}, format="json")
    assert r.status_code == 400 and "试用" in r.content.decode()


@pytest.mark.django_db
def test_draft_preview_always_watermarked(client_for, base):
    c = client_for("assistant")
    _, rid = _quote(c, base, base["ents"][1], [{"name": "演示制动器", "model": "RD-0", "unit": "台", "qty": "1", "unit_price": "100"}])
    t = _flat(c.get(f"/api/quote-revisions/{rid}/preview/").content)
    assert "【草稿预览】尚未生成冻结版本" in t  # 已签收主体：草稿预览也带水印与说明
    _unsign(base["ents"][1])
    t = _flat(c.get(f"/api/quote-revisions/{rid}/preview/").content)
    assert "【试用报价】模板未经负责人签收" in t


@pytest.mark.django_db
def test_signed_template_produces_clean_formal_pdf(client_for, base):
    c = client_for("assistant")
    qid, rid = _quote(c, base, base["ents"][0], [{"name": "演示电缆", "model": "3x50mm²", "unit": "米", "qty": "10", "unit_price": "1"}])
    r = c.post(f"/api/quote-revisions/{rid}/render/")
    assert r.status_code == 200 and r.json()["is_trial"] is False
    t = _flat(c.get(f"/api/quote-revisions/{rid}/pdf/").content)
    assert "试用" not in t and "草稿预览" not in t
    rev = QuoteRevision.objects.get(pk=rid)
    assert rev.snapshot["signoff"]["by"] == "leader" and rev.snapshot["template_version"] == TEMPLATE_VERSION
    assert c.post(f"/api/quotations/{qid}/status/", {"status": "SENT"}, format="json").status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["admin", "assistant", "sales", "finance"])
def test_only_leader_can_sign_off(client_for, base, who):
    ent = base["ents"][2]
    _unsign(ent)
    r = client_for(who).post(f"/api/legal-entities/{ent.id}/signoff/", {"note": "已与真实样张逐项核对"}, format="json")
    assert r.status_code == 403
    assert not TemplateSignoff.objects.filter(entity=ent).exists()


@pytest.mark.django_db
def test_leader_signoff_is_recorded_versioned_and_audited(client_for, base):
    from apps.audit.models import AuditEvent

    ent = base["ents"][2]
    _unsign(ent)
    c = client_for("leader")
    assert c.post(f"/api/legal-entities/{ent.id}/signoff/", {"note": "短"}, format="json").status_code == 409
    r = c.post(f"/api/legal-entities/{ent.id}/signoff/", {"note": "已与 2026-11 真实样张逐项比对；财务黄金用例 10/10"}, format="json")
    assert r.status_code == 201
    so = r.json()["signoff"]
    assert so["template_version"] == TEMPLATE_VERSION and so["signed_by"] == "leader" and so["template_key"] == "C"
    assert AuditEvent.objects.filter(action="signoff", resource="legal_entity", resource_id=str(ent.id)).exists()
    # 旧字段不再暴露，也不能通过 PATCH 伪造签收
    assert "template_verified" not in r.json()
    client_for("admin").patch(f"/api/legal-entities/{ent.id}/", {"template_verified": True, "signoff": {"x": 1}}, format="json")
    assert TemplateSignoff.objects.filter(entity=ent).count() == 1


@pytest.mark.django_db
def test_signoff_invalidated_by_key_config_change_and_revocable(client_for, base, users):
    ent = base["ents"][0]
    c = client_for("assistant")
    assert ent.current_signoff() is not None
    # 关键配置（价税口径）变化 → 签收失效
    assert c.patch(f"/api/legal-entities/{ent.id}/", {"price_basis": "GROSS"}, format="json").status_code == 200
    ent.refresh_from_db()
    assert ent.current_signoff() is None
    _, rid = _quote(c, base, ent, [{"name": "x", "model": "y", "unit": "台", "qty": "1", "unit_price": "1"}])
    assert c.post(f"/api/quote-revisions/{rid}/render/").status_code == 409
    # 非关键字段（地址）变化 → 签收仍有效
    sign_template(ent, users["leader"])
    assert c.patch(f"/api/legal-entities/{ent.id}/", {"address": "新地址"}, format="json").status_code == 200
    ent.refresh_from_db()
    assert ent.current_signoff() is not None
    # 负责人撤销
    assert client_for("assistant").post(f"/api/legal-entities/{ent.id}/signoff/revoke/", {"reason": "x"}, format="json").status_code == 403
    assert client_for("leader").post(f"/api/legal-entities/{ent.id}/signoff/revoke/", {"reason": "发现大写位置不符"}, format="json").status_code == 200
    ent.refresh_from_db()
    assert ent.current_signoff() is None


@pytest.mark.django_db
def test_signoff_for_older_template_version_does_not_count(base, users):
    ent = base["ents"][1]
    _unsign(ent)
    TemplateSignoff.objects.create(entity=ent, template_key=ent.template_key, template_version="2026.11-r2", config_sha256=ent.signoff_config_sha256(), config={}, note="旧版本签收", signed_by=users["leader"])
    assert ent.current_signoff() is None


# ------------------------------------------------------------------ 历史冻结
@pytest.mark.django_db
def test_rc1_r2_snapshots_still_render_with_frozen_r2_engine(client_for, make_quote):
    """RC1 存档 PDF 原字节不变；r2 快照的工程重渲染仍走冻结的 pdf_r2（模板升级不影响旧版本）。"""
    from apps.quotations import pdf_r2
    from apps.quotations.pdf import render_quote_pdf

    _, rid = make_quote()
    rev = QuoteRevision.objects.get(pk=rid)
    snap = dict(rev.snapshot)
    # 构造一份 RC1 形态（schema 2 / r2）的快照
    r2snap = {k: snap[k] for k in ["code", "template_key", "price_basis", "recipient", "recipient_contact", "project", "title", "quote_date", "valid_days", "delivery_terms",
                                    "payment_terms", "terms", "remark", "reference", "show_upper_amount", "prepared_by"]}
    r2snap.update(schema=2, template_version="2026.11-r2", legal_entity={k: snap["legal_entity"][k] for k in ["code", "name", "short_name", "address", "phone", "fax", "email", "tax_no", "bank_name", "bank_account", "footer_note", "quote_title", "intro_text", "show_tax_columns"]},
                  lines=[{k: ln[k] for k in ["model", "name", "brand", "description", "params_shown", "unit", "qty", "multiplier", "unit_price", "tax_rate", "discount_rate", "line_net", "line_tax", "line_gross", "remark", "product_id"]} for ln in snap["lines"]],
                  totals={"net": snap["totals"]["net"], "tax": snap["totals"]["tax"], "gross": snap["totals"]["gross"]})
    assert render_quote_pdf(r2snap) == pdf_r2.render_quote_pdf(r2snap)
    # 下载永远是存档原字节
    c = client_for("assistant")
    assert hashlib.sha256(c.get(f"/api/quote-revisions/{rid}/pdf/").content).hexdigest() == rev.pdf_sha256


def test_r2_engine_source_is_frozen():
    """pdf_r2.py 是 RC1 模板的冻结副本：内容哈希固定（任何修改都会让本测试失败，提醒“不得改旧模板”）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "apps" / "quotations" / "pdf_r2.py").read_bytes()
    body = src.split(b'"""', 2)[2]  # 去掉文件头说明（仅说明文字可改）
    assert hashlib.sha256(body).hexdigest() == R2_BODY_SHA256


R2_BODY_SHA256 = "0d35b4d709049bfb974e6e428800e71f84a8b383a1da2f557732bfa6e1fb894e"  # = git 875ef0c:backend/apps/quotations/pdf.py 去掉文件头说明后的哈希


# ------------------------------------------------------------------ 三套版式（虚构数据）
@pytest.mark.django_db
def test_template_a_bilingual_spec_layout(client_for, base):
    ent = LegalEntity.objects.get(code="A")
    ent.name_en, ent.address_en = "Demo Cable Equipment (Example) Co., Ltd.", "Building 0, Example Road, Example City"
    ent.default_terms = "1. General\nThis offer is subject to the conditions indicated in the general terms.\n3. Metal Price\nFormula to apply: P1=P0+(C1-C0) x copper weight+(A1-A0) x aluminium weight"
    ent.save()
    sign_template(ent, base["p_sales"].sales_owner.__class__.objects.get(username="leader"))
    c = client_for("assistant")
    _, rid = _quote(c, base, ent, [
        {"name": "阻燃拖令电缆", "model": "3x50mm²", "description": "动力电缆 C级阻燃", "unit": "米", "qty": "610", "unit_price": "385.16", "brand": "演示牌"},
        {"name": "阻燃拖令电缆", "model": "16x1.5mm²C", "description": "控制屏蔽电缆 C级阻燃", "unit": "米", "qty": "740", "unit_price": "136.55", "brand": "演示牌"},
    ], ref_copper_price="110500", delivery_terms="DDP", payment_terms="预付30%,发货前付清。", warranty="12个月", terms="This offer is subject to the conditions indicated in the general terms.")
    assert c.post(f"/api/quote-revisions/{rid}/render/").status_code == 200
    t = _flat(c.get(f"/api/quote-revisions/{rid}/pdf/").content)
    for s in ["报价单", "QUOTATION", "项目名称/ProjectName", "发往/To", "收件人/Attn", "发件人/Sender", "交货条款/DeliveryTerms", "修订版号/RevisionNo.", "Rev1.0",
              "报价编号/QuotationNo.", "铜价(人民币/吨)/Cu(CNY/Ton)", "110500", "产品品牌/ProductBrand", "演示牌", "货币/Currency", "质保期/WP", "12个月",
              "付款方式/PaymentTerms", "报价有效期/OfferValidityDays", "报价日期/DateOfOffer", "项号", "Item", "规格型号", "Specification", "描述说明", "Description", "UnitPrice", "TotalPrice",
              "3x50mm²", "动力电缆C级阻燃", "Termsandconditions:", "DemoCableEquipment(Example)Co.,Ltd.", "Address:Building0"]:
        assert s in t, s
    # 金额：610×385.16=234,947.60（未税），740×136.55=101,047.00；含税总价 = (234947.60+101047.00)×1.13 逐行计税
    assert "234,947.60" in t and "101,047.00" in t and "总价（含税）" in t


@pytest.mark.django_db
def test_template_b_param_subrows_and_upper(client_for, base):
    ent = LegalEntity.objects.get(code="B")
    ent.name_en, ent.website, ent.price_basis, ent.intro_text, ent.currency_label = "Demo Transmission (Example) Co., Ltd", "www.example.cn", "GROSS", "感谢您对我公司产品的关注，有关报价如下：", "人民币（元）"
    ent.show_tax_columns = False  # 样张 B：含税单价，合计区不拆分税额
    ent.save()
    from apps.accounts.models import User

    sign_template(ent, User.objects.get(username="leader"))
    c = client_for("assistant")
    _, rid = _quote(c, base, ent, [
        {"name": "鼓式制动器", "model": "RD400-DEMO1", "unit": "台", "qty": "4", "unit_price": "42500", "params": [{"key": "m", "label": "制动力矩", "value": "1610", "unit": "Nm"}]},
        {"name": "鼓式制动器", "model": "RD500-DEMO2", "unit": "台", "qty": "4", "unit_price": "58500", "params": [{"key": "m", "label": "制动力矩", "value": "5000", "unit": "Nm"}]},
    ], terms="报价产品为进口品牌（演示）。\n以上报价为含增值税专用发票，用户指定地点交货价。")
    assert c.post(f"/api/quote-revisions/{rid}/render/").status_code == 200
    t = _flat(c.get(f"/api/quote-revisions/{rid}/pdf/").content)
    for s in ["DemoTransmission(Example)Co.,Ltd", "To:", "Sub:", "Attn:", "Add:", "Web.:", "www.example.cn", "From:", "Date:", "币种：人民币（元）",
              "序号", "名称", "规格、型号", "数量", "单位", "单价", "总价", "备注", "制动力矩:", "1610Nm", "5000Nm", "170000", "234000", "合计：", "404000",
              "人民币金额(大写)：", "肆拾万肆仟元整", "注：", "此致"]:
        assert s in t, s
    assert "税额" not in t  # 含税口径：不出现未税/税额拆分


@pytest.mark.django_db
def test_template_c_parts_points_and_multi_unit_total(client_for, base):
    ent = LegalEntity.objects.get(code="C")
    ent.name_en, ent.address_en, ent.website, ent.price_basis, ent.quote_title = "Demo Lubrication (Example) Co.,Ltd.", "Room 000, Example Road\nExample District, 100000", "www.example.cn", "GROSS", "报 价 单"
    ent.email, ent.fax = "info@example.cn", "000-0000-101"
    ent.show_tax_columns = False  # 样张 C：含 13% 税，合计区不拆分税额
    ent.save()
    from apps.accounts.models import User

    sign_template(ent, User.objects.get(username="leader"))
    c = client_for("assistant")
    _, rid = _quote(c, base, ent, [
        {"name": "防爆单线电动润滑系统", "model": "行走机构", "unit": "套", "qty": "1", "unit_price": "68000", "params": [{"key": "pts", "label": "润滑点数", "value": "48", "unit": "个润滑点"}]},
        {"name": "防爆单线电动润滑系统", "model": "回转+俯仰机构", "unit": "套", "qty": "1", "unit_price": "65000", "params": [{"key": "pts", "label": "润滑点数", "value": "30", "unit": "个润滑点"}]},
        {"name": "手动润滑系统", "model": "斗轮机构", "unit": "套", "qty": "1", "unit_price": "12000", "params": [{"key": "pts", "label": "润滑点数", "value": "2", "unit": "个润滑点"}]},
    ], host_qty=2, host_label="台斗轮", terms="以上报价含增值税13%专用发票。")
    r = c.post(f"/api/quote-revisions/{rid}/render/")
    assert r.status_code == 200
    rev = r.json()
    assert rev["total_gross"] == "290000.00"  # 单台合计 145000 × 2 台
    t = _flat(c.get(f"/api/quote-revisions/{rid}/pdf/").content)
    for s in ["DemoLubrication(Example)Co.,Ltd.", "Website:www.example.cn", "E-mail:info@example.cn", "Fax:000-0000-101", "报价单", "项目：", "报价单号：", "To/致：", "Tel/座机：",
              "Mobile/移动：", "Attn/联系人：", "From/自：", "Email/邮箱：", "润滑部位", "行走机构", "回转+俯仰机构", "斗轮机构", "48个润滑点", "30个润滑点", "2个润滑点",
              "合计：145000", "2台斗轮总计：290000", "人民币金额(大写)：贰拾玖万元整", "注："]:
        assert s in t, s
    assert t.count("防爆单线电动润滑系统") == 1  # 连续同名系统合并单元格


@pytest.mark.django_db
@pytest.mark.parametrize("code", ["A", "B", "C"])
def test_long_list_pagination_per_template(client_for, base, code):
    ent = LegalEntity.objects.get(code=code)
    c = client_for("assistant")
    lines = [{"name": f"长清单测试项{i}", "model": f"M-{i:03d}-ABCDEFGHIJKLMNOPQRSTUVWXYZ", "unit": "件", "qty": "1", "unit_price": "10", "description": "说明" * 20,
              "params": [{"key": "a", "label": "参数", "value": f"{i}", "unit": "mm²"}]} for i in range(1, 46)]
    _, rid = _quote(c, base, ent, lines, host_qty=3)
    assert c.post(f"/api/quote-revisions/{rid}/render/").status_code == 200
    pdf = c.get(f"/api/quote-revisions/{rid}/pdf/").content
    t = _flat(pdf)
    n = len(re.findall(rb"/Type /Page[^s]", pdf))
    assert n >= 2 and f"第{n}页/共{n}页" in t and "长清单测试项45" in t
    assert "3台总计：" in t
