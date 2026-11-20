"""RC1 报价模板（模板版本 2026.11-r2）——**冻结，不得修改**。

仅用于重渲染 RC1 期间生成的快照（template_version == "2026.11-r2"）做工程核对；正式下载永远返回存档原字节。
RC2 起新报价使用 apps/quotations/pdf.py（2026.11-r3 及以后）。
"""
import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.common.money import D, rmb_upper
from apps.common.pdfkit import make_numbered_canvas, mx, register_fonts

TEMPLATE_VERSION = "2026.11-r2"  # r2：主体可配置标题/开头语/税列/含税口径、参考价、乘数；内置可分发字体

THEMES = {
    "A": {"name": "模板A·蓝色抬头", "primary": colors.HexColor("#163a5c"), "accent": colors.HexColor("#1f6fa8"), "light": colors.HexColor("#eaf1f8"), "band": True},
    "B": {"name": "模板B·绿色抬头", "primary": colors.HexColor("#0b6e5f"), "accent": colors.HexColor("#14907d"), "light": colors.HexColor("#e7f5f1"), "band": False},
    "C": {"name": "模板C·灰色简洁", "primary": colors.HexColor("#333a42"), "accent": colors.HexColor("#5c6670"), "light": colors.HexColor("#f1f2f4"), "band": False},
}


def _fmt(v, places=2):
    d = D(v)
    q = f"{d:,.{places}f}"
    return q


def _qty(v):
    d = D(v).normalize()
    s = format(d, "f")
    return s


def _p(text, style):
    return Paragraph(mx(text, bold=style.fontName.endswith("Bold")), style)


def render_quote_pdf(snap: dict) -> bytes:
    fonts = register_fonts()
    F, FB = fonts["font"], fonts["bold"]
    tk = snap["template_key"]
    th = THEMES.get(tk, THEMES["A"])
    ent = snap["legal_entity"]

    base = ParagraphStyle("base", fontName=F, fontSize=8.6, leading=12.2, textColor=colors.HexColor("#1f2a36"))
    small = ParagraphStyle("small", parent=base, fontSize=7.6, leading=10.6, textColor=colors.HexColor("#4a5663"))
    cell = ParagraphStyle("cell", parent=base, fontSize=8, leading=11)
    cell_b = ParagraphStyle("cellb", parent=cell, fontName=FB)
    cell_r = ParagraphStyle("cellr", parent=cell, alignment=TA_RIGHT)
    head_c = ParagraphStyle("headc", parent=cell, fontName=FB, alignment=TA_CENTER, textColor=colors.white)
    title_style = ParagraphStyle("title", parent=base, fontName=FB, fontSize=19, leading=24, alignment=TA_CENTER, textColor=th["primary"])
    company_style = ParagraphStyle("company", parent=base, fontName=FB, fontSize=14, leading=18, textColor=colors.white if th["band"] else th["primary"])
    company_sub = ParagraphStyle("companysub", parent=small, textColor=colors.HexColor("#dbe6f0") if th["band"] else colors.HexColor("#5b6772"))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=13 * mm,
        bottomMargin=19 * mm,
        title=f"报价单 {snap['code']}",
        author=ent["name"],
        subject="报价单",
        creator="Industrial Sales Hub",
        producer="Industrial Sales Hub",
        invariant=1,
        lang="zh-CN",
    )
    W = A4[0] - 30 * mm
    story = []

    # ---- 抬头 ----
    contact_line = "　".join(x for x in [
        f"地址：{ent.get('address')}" if ent.get("address") else "",
        f"电话：{ent.get('phone')}" if ent.get("phone") else "",
        f"传真：{ent.get('fax')}" if ent.get("fax") else "",
        f"邮箱：{ent.get('email')}" if ent.get("email") else "",
    ] if x)
    header = Table([[_p(ent["name"], company_style)], [_p(contact_line, company_sub)]], colWidths=[W])
    hs = [("LEFTPADDING", (0, 0), (-1, -1), 9 if th["band"] or tk == "B" else 0), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if th["band"]:
        hs += [("BACKGROUND", (0, 0), (-1, -1), th["primary"]), ("TOPPADDING", (0, 0), (-1, 0), 9), ("BOTTOMPADDING", (0, -1), (-1, -1), 9)]
    elif tk == "B":
        hs += [("LINEBEFORE", (0, 0), (0, -1), 4, th["accent"])]
    else:
        hs += [("LINEBELOW", (0, -1), (-1, -1), 1.2, th["primary"])]
    header.setStyle(TableStyle(hs))
    story += [header, Spacer(1, 7 * mm), _p(ent.get("quote_title") or "报　价　单", title_style), Spacer(1, 5 * mm)]

    # ---- 基本信息 ----
    lab = ParagraphStyle("lab", parent=cell, textColor=colors.HexColor("#5a6571"))
    rc = snap.get("recipient_contact") or {}
    info = [
        [_p("致：", lab), _p(snap["recipient"]["name"], cell_b), _p("报价编号：", lab), _p(snap["code"], cell_b)],
        [_p("联系人：", lab), _p(" ".join(x for x in [rc.get("name"), rc.get("position")] if x) or "—", cell), _p("报价日期：", lab), _p(snap["quote_date"], cell)],
        [_p("电话：", lab), _p(rc.get("mobile") or rc.get("phone") or "—", cell), _p("有效期：", lab), _p(f"{snap['valid_days']} 天", cell)],
        [_p("项目名称：", lab), _p(snap["project"]["name"], cell), _p("报价人：", lab), _p(snap.get("prepared_by") or "—", cell)],
    ]
    it = Table(info, colWidths=[18 * mm, W * 0.5 - 18 * mm, 18 * mm, W * 0.5 - 18 * mm])
    it.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [it, Spacer(1, 3 * mm), _p(ent.get("intro_text") or "感谢贵司的信任与支持，现将相关产品报价如下：", small), Spacer(1, 2.5 * mm)]
    ref = snap.get("reference") or {}
    if any(ref.get(k) for k in ("copper", "fx", "note")):
        parts = [x for x in [f"参考铜价：{ref['copper']}" if ref.get("copper") else "", f"参考汇率：{ref['fx']}" if ref.get("fx") else "",
                             f"有效期至：{ref['valid_until']}" if ref.get("valid_until") else "", ref.get("note") or ""] if x]
        story += [_p("；".join(parts) + "。上述参考值仅作报价依据说明，价格以本报价单所列为准。", small), Spacer(1, 2 * mm)]

    # ---- 明细 ----
    gross_basis = snap.get("price_basis") == "GROSS"
    show_tax = ent.get("show_tax_columns", True)
    cols = [9 * mm, 42 * mm, None, 11 * mm, 15 * mm, 22 * mm, 12 * mm, 25 * mm]
    if not show_tax:
        cols = [9 * mm, 44 * mm, None, 11 * mm, 17 * mm, 24 * mm, 27 * mm]
    fixed = sum(c for c in cols if c)
    cols[2] = W - fixed
    price_label = "含税单价" if gross_basis else "未税单价"
    headers = ["序号", "产品名称 / 型号", "技术参数", "单位", "数量", price_label, "税率", "含税金额"]
    if not show_tax:
        headers = ["序号", "产品名称 / 型号", "技术参数", "单位", "数量", price_label, "金额"]
    rows = [[_p(h, head_c) for h in headers]]
    for i, ln in enumerate(snap["lines"], 1):
        name = f"<font name='{FB}'>{mx(ln['name'], bold=True)}</font><br/>{mx(ln['model'])}"
        if ln.get("brand"):
            name += f"<br/><font color='#6a7480'>{mx('品牌：' + ln['brand'])}</font>"
        params = [mx(f"{p['label']}：{p['value']}{p.get('unit') or ''}") for p in ln.get("params_shown", [])]
        desc = ln.get("description") or ""
        ptxt = "<br/>".join(params)
        if desc:
            ptxt = (ptxt + "<br/>" if ptxt else "") + mx(desc)
        if ln.get("remark"):
            ptxt = (ptxt + "<br/>" if ptxt else "") + f"<font color='#8a5a12'>{mx('备注：' + ln['remark'])}</font>"
        if D(ln.get("discount_rate", 0)) > 0:
            pct = _fmt(D(ln['discount_rate']) * 100, 2).rstrip('0').rstrip('.')
            ptxt = (ptxt + "<br/>" if ptxt else "") + f"<font color='#6a7480'>{mx('优惠：' + pct + '%')}</font>"
        qty_txt = _qty(ln["qty"])
        if ln.get("multiplier") and D(ln["multiplier"]) != 1:
            qty_txt = f"{qty_txt}×{_qty(ln['multiplier'])}"
        row = [
            _p(i, ParagraphStyle("c", parent=cell, alignment=TA_CENTER)),
            Paragraph(name, cell),
            Paragraph(ptxt or "—", cell),
            _p(ln["unit"], ParagraphStyle("c2", parent=cell, alignment=TA_CENTER)),
            _p(qty_txt, cell_r),
            _p(_fmt(ln["unit_price"]), cell_r),
            _p(f"{(D(ln['tax_rate']) * 100).normalize():f}%", ParagraphStyle("c3", parent=cell, alignment=TA_CENTER)),
            _p(_fmt(ln["line_gross"]), cell_r),
        ]
        if not show_tax:
            del row[6]
        rows.append(row)
    t = snap["totals"]
    lt = Table(rows, colWidths=cols, repeatRows=1)
    st = [
        ("BACKGROUND", (0, 0), (-1, 0), th["primary"]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("VALIGN", (1, 1), (2, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d1d9")),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    for r in range(2, len(rows), 2):
        st.append(("BACKGROUND", (0, r), (-1, r), th["light"]))
    lt.setStyle(TableStyle(st))
    story.append(lt)

    tot_rows = [
        [_p("未税合计", cell_r), _p("¥ " + _fmt(t["net"]), cell_r)],
        [_p("税额合计", cell_r), _p("¥ " + _fmt(t["tax"]), cell_r)],
        [_p("含税总计", ParagraphStyle("tb", parent=cell_r, fontName=FB)), _p("¥ " + _fmt(t["gross"]), ParagraphStyle("tg", parent=cell_r, fontName=FB, textColor=th["primary"]))],
    ]
    if snap.get("show_upper_amount"):
        tot_rows.append([_p("大写金额", cell_r), _p(rmb_upper(t["gross"]), ParagraphStyle("up", parent=cell_r, fontName=FB))])
    tt = Table(tot_rows, colWidths=[W - 62 * mm, 62 * mm])
    tt.setStyle(TableStyle([("LINEBELOW", (0, -1), (-1, -1), 0.8, th["primary"]), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story += [tt, Spacer(1, 5 * mm)]

    # ---- 商务条款 ----
    sec = ParagraphStyle("sec", parent=base, fontName=FB, fontSize=10, leading=14, textColor=th["primary"])
    terms = []
    if snap.get("delivery_terms"):
        terms.append(f"交货期：{snap['delivery_terms']}")
    if snap.get("payment_terms"):
        terms.append(f"付款方式：{snap['payment_terms']}")
    terms.append(f"报价有效期：自报价日期起 {snap['valid_days']} 天。")
    terms += [x.strip() for x in (snap.get("terms") or "").splitlines() if x.strip()]
    block = [_p("商务条款", sec), Spacer(1, 1.5 * mm)] + [_p(f"{i}. {x}", small) for i, x in enumerate(terms, 1)]
    if snap.get("remark"):
        block += [Spacer(1, 2 * mm), _p("备注：" + snap["remark"], small)]
    story.append(KeepTogether(block))

    # ---- 落款 ----
    bank = "　".join(x for x in [f"开户行：{ent.get('bank_name')}" if ent.get("bank_name") else "", f"账号：{ent.get('bank_account')}" if ent.get("bank_account") else "", f"税号：{ent.get('tax_no')}" if ent.get("tax_no") else ""] if x)
    sign = Table([[_p(bank, small), _p(f"{ent['name']}\n{snap['quote_date']}", ParagraphStyle("sg", parent=cell, alignment=TA_RIGHT))]], colWidths=[W * 0.6, W * 0.4])
    sign.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [Spacer(1, 7 * mm), KeepTogether([sign])]

    footer = f"{ent.get('short_name') or ent['name']} · {snap['code']} · {ent.get('footer_note') or '本报价单由系统生成，未经盖章仅供参考'}"
    doc.build(story, canvasmaker=make_numbered_canvas(footer, F))
    return buf.getvalue()
