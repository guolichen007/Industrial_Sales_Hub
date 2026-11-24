"""报价模板 2026.11-r4（RC3 定向整改）：依据三份真实样张的版式重做的三套固定模板。唯一输入：冻结快照 dict。

  A 双语规格表（参照电缆报价样张）：公司名抬头 + 右上 Logo；“报价单 / QUOTATION”；双栏双语业务字段
    （项目/发往/收件人/发件人/交货条款/修订版号/报价编号 | 铜价/品牌/货币/质保期/付款方式/有效期/报价日期）；
    明细列 项号Item / 产品名称Product / 规格型号Specification Model / 描述说明Description / 数量Qty. / 单位Unit /
    单价Unit Price / 总价Total Price；“总价：”；英文商务条款（Terms and conditions）+ 公司英文名与地址。
    铜/铝调价公式只作为条款文字展示，不做行情接口、不自动调价（D4）。
  B 中英抬头 + 参数子行（参照制动器报价样张）：Logo + 中文公司名 + 英文公司名；左栏 To/Sub/Email/Attn/Tel，
    右栏 Add/Web./Email/From/Tel./Date；开头语 + “币种：人民币（元）”；明细 序号/名称/规格、型号/数量/单位/单价/总价/备注，
    每项下方逐条打印“参数名: 参数值”子行（如 制动力矩）；合计：、人民币金额(大写)：、注：编号条款、此致 + 公司名。
  C 双语抬头 + 部位/点数 + 多台总计（参照润滑报价样张）：Logo，左英文名/地址/网址/邮箱，右中文名/地址/电话/传真；
    “报 价 单”；项目/报价单号、To/致、Tel/座机、Mobile/移动、Attn/联系人 | From/自、Tel、Mobile、Email；
    明细 序号/名称/润滑部位/单位/数量/单价/总价/备注（同名连续行合并显示名称）；合计：、“N台××总计：”、
    人民币金额(大写)：、注：编号条款；右下公司名 + 日期。

共同规则
- 金额全部来自后端 Decimal 计算（apps.common.money），模板只格式化，不做任何计算；“总计 = 合计 × 整单台数”在服务层计算后写入快照。
- 金额单行适配（F-1）：明细单价/总价、合计区、整单台数总计等金额一律单行完整显示，绝不折行、截断或省略。
  数字宽度用与渲染完全相同的嵌入字体按字号线性测量（reportlab stringWidth 与字号成正比）：先按可用宽度求所需字号，
  在 [6.0pt, 基准字号] 之间确定性缩放；明细金额列宽固定故仅缩放字号，合计区在需要时先加宽金额列（至多占 62% 页宽）
  再缩放字号。若最小可读字号 6.0pt 仍放不下，则**拒绝生成正式报价**（AmountTooWide），绝不产出不可读 PDF。
  正常金额（可单行放下）字号与列宽均与 r3 完全一致，版式不退化。
- 模板版本号写入页脚与签收门禁：本版（r4）修订单行适配后正式渲染结果变化，故提升版本号；旧版签收不授权新版本，需负责人重新签收。
- 未签收（模板 + 模板版本 + 主体关键配置未经负责人签收）时只能生成“试用报价”：每页斜向水印“试用 · 模板未签收 · 非正式报价”、
  标题前缀【试用】、页脚注明，且元数据 Subject 标记 TRIAL —— 不可能被误认为正式报价。
- 历史 PDF 永远返回存档原字节；RC1（2026.11-r2）快照由 pdf_r2 渲染，本模块不改变其输出。
"""
import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.common import pdfkit
from apps.common.money import D, rmb_upper
from apps.common.pdfkit import draw_mixed, make_numbered_canvas, mx, register_fonts

TEMPLATE_VERSION = "2026.11-r4"
TRIAL_MARK = "试用 · 模板未签收 · 非正式报价"
DRAFT_MARK = "草稿预览 · 非正式报价"

INK = colors.HexColor("#1b1f24")
GREY = colors.HexColor("#5b6470")
LINE = colors.HexColor("#9aa3ad")
BLUE_A = colors.HexColor("#2f6db5")      # 样张 A：公司名蓝色
BAND_A = colors.HexColor("#dfe9f5")      # 样张 A：明细隔行底色
BLUE_B = colors.HexColor("#3f6fd8")      # 样张 B：公司名/网址蓝色
BLUE_C = colors.HexColor("#1e6fd0")      # 样张 C：抬头分隔线


def money_fmt(v, places=2):
    return f"{D(v):,.{places}f}"


def plain(v):
    """样张 B/C 的“常规”数字格式：整数不带小数与千分位，非整数两位小数。"""
    d = D(v)
    return f"{d:.0f}" if d == d.to_integral() else f"{d:.2f}"


def qty_fmt(v):
    d = D(v).normalize()
    return format(d, "f")


# ============================================================ 金额单行适配（F-1）
_AMOUNT_FLOOR_PT = 6.0      # 金额最小可读字号：低于此值不再缩小，改为拒绝生成
_AMOUNT_SAFETY_PT = 1.0     # 测量安全余量，抵消内边距/取整误差，确保真正单行


class AmountTooWide(ValueError):
    """金额在最小可读字号下仍无法单行排入列宽：明确阻断，拒绝生成不可读的正式 PDF。"""


def _amount_font(bold):
    """金额实际渲染所用字体（ASCII 数字/逗号/点为拉丁字体，见 pdfkit.mx 的混排）。"""
    info = pdfkit.register_fonts()
    if info.get("latin"):
        return pdfkit.LATIN_BOLD if bold else pdfkit.LATIN
    return info["bold"] if bold else info["font"]


def _amount_width(text, size, bold):
    return pdfmetrics.stringWidth(str(text), _amount_font(bold), size)


def _fit_font_size(strings, avail_pt, base_pt, bold, floor=_AMOUNT_FLOOR_PT):
    """单行适配：返回 <= base_pt 的最大字号，使每个字符串都能在一行内放下。

    reportlab 的 stringWidth 与字号严格线性相关，故所需字号可精确求解（确定性、可复算、跨主机一致，
    字体为仓库内置固定版本）。所需字号低于下限 floor 时抛 AmountTooWide，绝不输出不可读 PDF。
    """
    widest = max((_amount_width(s, base_pt, bold) for s in strings), default=0.0)
    if widest <= avail_pt:
        return base_pt
    need = base_pt * avail_pt / widest
    need = int(need * 100) / 100.0  # 向下取到 0.01pt，避免向上舍入后仍溢出
    if need < floor:
        widest_s = max((str(s) for s in strings), key=lambda x: _amount_width(x, base_pt, bold))
        raise AmountTooWide(
            f"金额“{widest_s}”在最小可读字号 {floor}pt 下仍超宽（需 {need:.2f}pt，列可用 {avail_pt:.1f}pt）："
            "为避免生成折行/裁切而不可读的报价单，已拒绝正式生成，请改用更宽的列、拆分报价单或缩短金额位数。")
    return need


def _fit_para(ctx, text, avail_pt, base_style):
    """把一个金额字符串排入给定可用宽度：能单行放下时按基准字号（输出与 r3 完全一致），否则缩放字号。"""
    bold = base_style.fontName == ctx.FB
    size = _fit_font_size([text], avail_pt - _AMOUNT_SAFETY_PT, base_style.fontSize, bold)
    if abs(size - base_style.fontSize) < 1e-9:
        return ctx.p(text, base_style)
    st = ParagraphStyle(f"{base_style.name}-fit", parent=base_style, fontSize=size, leading=size + 2.6)
    return Paragraph(mx(text, bold=bold), st)


def _layout_amount_cols(W, cols, flex_idx, pad, amount_cols, base):
    """明细表金额列单行适配：把金额列加宽到能放下其最大值，宽度从可伸缩的“描述/部位”列借用。

    - 正常金额：所需宽度不超过原列宽 → 列宽不变（正常版式完全不退化，与 r3 一致）。
    - 大额金额：从描述列借宽，金额字号仍为基准字号（可读性最好）。
    - 描述列被压到下限仍不够：只加宽能加的部分，余下由 _fit_para 缩字号（下限 6pt），再不够则阻断。
    amount_cols: {列序号: [该列将渲染的金额字符串...]}；返回 (调整后的列宽列表, {列序号: 该列加宽后宽度pt})。
    """
    fixed = sum(c for i, c in enumerate(cols) if i != flex_idx and c is not None)
    flex_base = W - fixed
    min_flex = 18 * mm   # 描述/部位列的最小可用宽度
    widths = list(cols)
    extra = {}
    for i, strs in amount_cols.items():
        need = max((_amount_width(s, base, False) for s in strs), default=0.0) + pad + 2
        extra[i] = max(0.0, need - widths[i])
    total_extra = sum(extra.values())
    flex_avail = max(0.0, flex_base - min_flex)
    final = {}
    if total_extra <= flex_avail + 1e-9:
        for i in amount_cols:
            widths[i] += extra[i]
            final[i] = widths[i]
        widths[flex_idx] = flex_base - total_extra
    else:
        left = flex_avail
        for i in sorted(amount_cols, key=lambda k: (-extra[k], k)):
            give = min(extra[i], left)
            left -= give
            widths[i] += give
            final[i] = widths[i]
        widths[flex_idx] = flex_base - sum(widths[i] - cols[i] for i in amount_cols)
    return widths, final


class _Ctx:
    def __init__(self, snap):
        self.snap = snap
        self.ent = snap["legal_entity"]
        f = register_fonts()
        self.F, self.FB = f["font"], f["bold"]
        self.trial = bool(snap.get("trial"))
        self.watermark = snap.get("watermark") or ("TRIAL" if self.trial else None)
        s = lambda name, **kw: ParagraphStyle(name, **{"fontName": self.F, "fontSize": 8.6, "leading": 11.6, "textColor": INK, **kw})
        self.base = s("base")
        self.small = s("small", fontSize=7.8, leading=10.4, textColor=GREY)
        self.cell = s("cell", fontSize=8.4, leading=11)
        self.cell_c = s("cellc", fontSize=8.4, leading=11, alignment=TA_CENTER)
        self.cell_r = s("cellr", fontSize=8.4, leading=11, alignment=TA_RIGHT)
        self.head = s("head", fontSize=8.2, leading=10.4, alignment=TA_CENTER, fontName=self.FB)
        self.bold_r = s("boldr", fontSize=9, leading=12, alignment=TA_RIGHT, fontName=self.FB)
        self.bold = s("bold", fontSize=9, leading=12, fontName=self.FB)

    def p(self, text, style=None, bold=False):
        style = style or self.base
        return Paragraph(mx(text, bold=bold or style.fontName == self.FB), style)

    def logo(self, max_w, max_h):
        key, sha = self.ent.get("logo_key"), self.ent.get("logo_sha256")
        if not key:
            return None
        from apps.common.pdfkit import read_private, sha256_bytes

        data = read_private(key)
        if sha256_bytes(data) != sha:
            raise ValueError("Logo 文件校验失败（哈希不一致）")
        ir = ImageReader(io.BytesIO(data))
        w, h = ir.getSize()
        k = min(max_w / w, max_h / h)
        return Image(io.BytesIO(data), width=w * k, height=h * k)


def _title(ctx, zh, en=None, size=17):
    t = ("【试用】" if ctx.trial else "") + zh
    st = ParagraphStyle("t", fontName=ctx.FB, fontSize=size, leading=size + 4, alignment=TA_CENTER, textColor=INK)
    out = [Paragraph(mx(t, bold=True), st)]
    if en:
        out.append(Paragraph(mx(en, bold=True), ParagraphStyle("te", fontName=ctx.FB, fontSize=size - 4, leading=size, alignment=TA_CENTER, textColor=INK)))
    return out


def _reference_line(ctx, include_copper):
    """参考价（铜价/汇率/有效期/说明）：仅手工录入并印刷，不参与任何自动调价（D4）。"""
    ref = ctx.snap.get("reference") or {}
    parts = [f"参考铜价：{ref['copper']}" if include_copper and ref.get("copper") else "", f"参考汇率/Exchange rate：{ref['fx']}" if ref.get("fx") else "",
             f"参考值有效期至：{ref['valid_until']}" if ref.get("valid_until") else "", ref.get("note") or ""]
    parts = [x for x in parts if x]
    if not parts:
        return []
    return [Paragraph(mx("；".join(parts) + "。上述参考值仅作报价依据说明，价格以本报价单所列为准。"), ctx.small), Spacer(1, 2 * mm)]


def _terms(snap, header_has_commercial=False):
    """条款：交货期/付款/质保/有效期 + 自由条款（每行一条），保持样张“注：1. …”的编号习惯。
    header_has_commercial=True（模板 A：抬头已有交货/付款/质保/有效期字段）时只输出自由条款，避免重复。"""
    out = []
    terms_text = [x.strip() for x in (snap.get("terms") or "").splitlines() if x.strip()]
    if header_has_commercial:
        return terms_text
    joined = "".join(terms_text)
    if snap.get("delivery_terms") and "交货" not in joined:
        out.append(f"交货期：{snap['delivery_terms']}")
    if snap.get("payment_terms") and "付款" not in joined:
        out.append(f"付款方式：{snap['payment_terms']}")
    if snap.get("warranty") and "质保" not in joined:
        out.append(f"质保期：{snap['warranty']}")
    if "有效期" not in joined:
        out.append(f"报价有效期：自报价日期起 {snap['valid_days']} 天。")
    return out + terms_text


def _contact(snap):
    rc = snap.get("recipient_contact") or {}
    return rc


def _sender(snap):
    s = snap.get("sender") or {}
    return s.get("name") or snap.get("prepared_by") or "", s.get("phone") or "", s.get("mobile") or "", s.get("email") or ""


# ============================================================ 模板 A
def _build_a(ctx, W):
    snap, ent = ctx.snap, ctx.ent
    story = []
    name_st = ParagraphStyle("na", fontName=ctx.FB, fontSize=15, leading=19, textColor=BLUE_A)
    logo = ctx.logo(40 * mm, 16 * mm)
    head = Table([[Paragraph(mx(ent["name"], bold=True), name_st), logo or ""]], colWidths=[W - 45 * mm, 45 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "RIGHT"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 5 * mm)] + _title(ctx, ent.get("quote_title") or "报价单", "QUOTATION") + [Spacer(1, 3 * mm)]
    ref = snap.get("reference") or {}
    rc = _contact(snap)
    sname, sphone, smobile, _semail = _sender(snap)
    lab = ParagraphStyle("la", parent=ctx.cell, textColor=INK)
    rows = [
        ("项目名称/Project Name:", snap["project"]["name"], "铜价(人民币/吨)/Cu(CNY/Ton)：", ref.get("copper") or "—"),
        ("发往/To：", snap["recipient"]["name"], "产品品牌/Product Brand：", snap.get("brand_summary") or "—"),
        ("收件人/Attn：", " ".join(x for x in [rc.get("name"), rc.get("position")] if x) or "—", "货币/Currency：", ent.get("currency_label") or "人民币 CNY"),
        ("发件人/Sender：", "  ".join(x for x in [sname, smobile or sphone] if x) or "—", "质保期/WP：", snap.get("warranty") or "—"),
        ("交货条款/Delivery Terms：", snap.get("delivery_terms") or "—", "付款方式/Payment Terms：", snap.get("payment_terms") or "—"),
        ("修订版号/Revision No.：", f"Rev {snap['version']}.0", "报价有效期/Offer Validity Days：", f"{snap['valid_days']} 天"),
        ("报价编号/Quotation No.：", snap["code"], "报价日期/Date Of Offer：", snap["quote_date"]),
    ]
    data = [[ctx.p(a, lab), ctx.p(b, ctx.cell), ctx.p(c, lab), ctx.p(d, ctx.cell)] for a, b, c, d in rows]
    it = Table(data, colWidths=[W * 0.24, W * 0.26, W * 0.30, W * 0.20])
    it.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.8, BLUE_A), ("LINEBELOW", (0, -1), (-1, -1), 0.8, BLUE_A),
                            ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#d5dbe2")),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2), ("LEFTPADDING", (0, 0), (-1, -1), 1)]))
    story += [it, Spacer(1, 2 * mm)] + _reference_line(ctx, include_copper=False) + [Spacer(1, 2 * mm)]
    hdr = ["项号\nItem", "产品名称\nProduct", "规格型号\nSpecification  Model", "描述说明\nDescription", "数量\nQty.", "单位\nUnit", "单价\nUnit Price", "总价\nTotal Price"]
    cols = [11 * mm, 30 * mm, 33 * mm, None, 16 * mm, 12 * mm, 22 * mm, 26 * mm]
    _unit_strs = [money_fmt(ln["unit_price"]) for ln in snap["lines"]]
    _amt_strs = [money_fmt(ln["amount"]) for ln in snap["lines"]]
    cols, _aw = _layout_amount_cols(W, cols, 3, 4, {6: _unit_strs, 7: _amt_strs}, ctx.cell_r.fontSize)  # 单价/总价列 2+2 内边距
    rows = [[ctx.p(h, ctx.head) for h in hdr]]
    for i, ln in enumerate(snap["lines"], 1):
        desc = ln.get("description") or ""
        params = "；".join(f"{p['label']}：{p['value']}{p.get('unit') or ''}" for p in ln.get("params_shown", []))
        desc = "；".join(x for x in [desc, params, ln.get("note") or ""] if x)
        rows.append([ctx.p(i, ctx.cell_c), ctx.p(ln["name"], ctx.cell_c), ctx.p(ln["model"], ctx.cell_c), ctx.p(desc or "—", ctx.cell_c),
                     ctx.p(ln["qty_total"], ctx.cell_c), ctx.p(ln["unit"], ctx.cell_c),
                     _fit_para(ctx, money_fmt(ln["unit_price"]), _aw[6] - 4, ctx.cell_r),   # 单价列（可加宽）
                     _fit_para(ctx, money_fmt(ln["amount"]), _aw[7] - 4, ctx.cell_r)])        # 总价列（可加宽）
    lt = Table(rows, colWidths=cols, repeatRows=1)
    st = [("GRID", (0, 0), (-1, -1), 0.5, INK), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
          ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]
    for r in range(1, len(rows), 2):
        st.append(("BACKGROUND", (0, r), (-1, r), BAND_A))
    lt.setStyle(TableStyle(st))
    story.append(lt)
    story += _totals_block(ctx, W, label_total="总价：", show_upper=snap.get("show_upper_amount"))
    # 英文商务条款（原文由主体默认条款提供，每行一条；空行分段）
    tst = ParagraphStyle("tc", parent=ctx.base, fontSize=8.4, leading=11.4)
    block = [Spacer(1, 4 * mm), ctx.p("Terms and conditions:", ctx.bold)]
    for line in _terms(snap, header_has_commercial=True):
        block.append(Paragraph(mx(line), tst))
    if snap.get("remark"):
        block.append(Paragraph(mx("Remark / 备注：" + snap["remark"]), tst))
    story.append(KeepTogether(block))
    tail = [x for x in [ent.get("name_en"), (("Address: " + ent["address_en"]) if ent.get("address_en") else "")] if x]
    if tail:
        story += [Spacer(1, 3 * mm)] + [Paragraph(mx(x), tst) for x in tail]
    return story


# ============================================================ 模板 B
def _build_b(ctx, W):
    snap, ent = ctx.snap, ctx.ent
    story = []
    logo = ctx.logo(42 * mm, 17 * mm)
    nm = ParagraphStyle("nb", fontName=ctx.FB, fontSize=14, leading=18, textColor=BLUE_B, alignment=TA_RIGHT)
    en = ParagraphStyle("eb", fontName=ctx.FB, fontSize=8.6, leading=11, textColor=BLUE_B, alignment=TA_RIGHT)
    right = [Paragraph(mx(ent["name"], bold=True), nm)]
    if ent.get("name_en"):
        right.append(Paragraph(mx(ent["name_en"], bold=True), en))
    head = Table([[logo or "", right]], colWidths=[W * 0.42, W * 0.58])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LINEBELOW", (0, 0), (-1, 0), 0.8, LINE), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story.append(head)
    if ctx.trial:
        story += [Spacer(1, 1 * mm)] + _title(ctx, ent.get("quote_title") or "报价单", size=12)
    rc = _contact(snap)
    sname, sphone, smobile, semail = _sender(snap)
    lab = ParagraphStyle("lb", parent=ctx.cell, textColor=INK)
    link = ParagraphStyle("lk", parent=ctx.cell, textColor=BLUE_B)
    left = [("To:", snap["recipient"]["name"]), ("Sub:", snap["project"]["name"]), ("Email:", rc.get("email") or ""), ("Attn:", " ".join(x for x in [rc.get("name"), rc.get("position")] if x)), ("Tel:", rc.get("mobile") or rc.get("phone") or "")]
    rightc = [("Add:", ent.get("address") or ""), ("Web.:", ent.get("website") or ""), ("Email:", ent.get("email") or semail), ("From:", sname), ("Tel.:", smobile or sphone or ent.get("phone") or ""), ("Date:", snap["quote_date"])]
    n = max(len(left), len(rightc))
    left += [("", "")] * (n - len(left))
    data = [[ctx.p(a, lab), ctx.p(b, ctx.cell), ctx.p(c, lab if c != "Web.:" else link), ctx.p(d, ctx.cell)] for (a, b), (c, d) in zip(left, rightc)]
    it = Table(data, colWidths=[13 * mm, W * 0.5 - 13 * mm, 13 * mm, W * 0.5 - 13 * mm])
    it.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [Spacer(1, 1.5 * mm), it, Spacer(1, 4 * mm)]
    intro = Table([[ctx.p(ent.get("intro_text") or "感谢您对我公司产品的关注，有关报价如下："), ctx.p(f"币种：{ent.get('currency_label') or '人民币（元）'}", ctx.cell_r)]], colWidths=[W * 0.7, W * 0.3])
    intro.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [intro, Spacer(1, 2 * mm)] + _reference_line(ctx, include_copper=True)
    hdr = ["序号", "名称", "规格、型号", "数量", "单位", "单价", "总价", "备注"]
    cols = [12 * mm, 30 * mm, None, 15 * mm, 13 * mm, 24 * mm, 27 * mm, 22 * mm]
    _unit_strs = [plain(ln["unit_price"]) for ln in snap["lines"]]
    _amt_strs = [plain(ln["amount"]) for ln in snap["lines"]]
    cols, _aw = _layout_amount_cols(W, cols, 2, 4, {5: _unit_strs, 6: _amt_strs}, ctx.cell_r.fontSize)  # 单价/总价列 2+2 内边距
    rows = [[ctx.p(h, ParagraphStyle("hb", parent=ctx.cell_c)) for h in hdr]]
    sub_rows = []
    for i, ln in enumerate(snap["lines"], 1):
        rows.append([ctx.p(i, ctx.cell_c), ctx.p(ln["name"], ctx.cell_c), ctx.p(ln["model"], ctx.cell), ctx.p(ln["qty_total"], ctx.cell_c), ctx.p(ln["unit"], ctx.cell_c),
                     _fit_para(ctx, plain(ln["unit_price"]), _aw[5] - 4, ctx.cell_r),   # 单价列（可加宽）
                     _fit_para(ctx, plain(ln["amount"]), _aw[6] - 4, ctx.cell_r),        # 总价列（可加宽）
                     ctx.p(ln.get("note") or "", ctx.cell_c)])
        subs = [(f"{p['label']}:", f"{p['value']}{p.get('unit') or ''}") for p in ln.get("params_shown", [])]
        if ln.get("description"):
            subs.append(("说明:", ln["description"]))
        for a, b in subs:
            sub_rows.append(len(rows))
            rows.append(["", ctx.p(a, ParagraphStyle("sa", parent=ctx.small, alignment=TA_RIGHT)), ctx.p(b, ctx.small), "", "", "", "", ""])
        rows.append(None)  # 项目分隔线占位
    final, seps, r = [], [], 0
    for row in rows:
        if row is None:
            seps.append(len(final) - 1)
            continue
        final.append(row)
    lt = Table(final, colWidths=cols, repeatRows=1)
    st = [("LINEABOVE", (0, 0), (-1, 0), 0.8, INK), ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]
    for s_ in seps:
        st.append(("LINEBELOW", (0, s_), (-1, s_), 0.5, INK))
    lt.setStyle(TableStyle(st))
    story.append(lt)
    story += _totals_block(ctx, W, label_total="合计：", show_upper=True, upper_left=True, compact_numbers=True)
    story += _note_block(ctx, W)
    story += [Spacer(1, 6 * mm), ctx.p("此致"), Spacer(1, 4 * mm), Paragraph(mx(ent["name"]), ParagraphStyle("sg", parent=ctx.base, leftIndent=12 * mm))]
    return story


# ============================================================ 模板 C
def _build_c(ctx, W):
    snap, ent = ctx.snap, ctx.ent
    story = []
    logo = ctx.logo(36 * mm, 20 * mm)
    if logo:
        logo.hAlign = "LEFT"
        story.append(logo)
    sm = ParagraphStyle("cs", parent=ctx.small, textColor=INK, fontSize=7.8, leading=10.2)
    left = [Paragraph(mx(ent.get("name_en") or ""), ParagraphStyle("cen", parent=sm, fontSize=8.4))]
    left += [Paragraph(mx(x), sm) for x in (ent.get("address_en") or "").splitlines() if x.strip()]
    if ent.get("website"):
        left.append(Paragraph(mx("Website: " + ent["website"]), sm))
    if ent.get("email"):
        left.append(Paragraph(mx("E-mail: " + ent["email"]), sm))
    right = [Paragraph(mx(ent["name"], bold=True), ParagraphStyle("czh", parent=sm, fontName=ctx.FB, fontSize=8.6))]
    right += [Paragraph(mx(x), sm) for x in (ent.get("address") or "").splitlines() if x.strip()]
    if ent.get("phone"):
        right.append(Paragraph(mx("Tel.: " + ent["phone"]), sm))
    if ent.get("fax"):
        right.append(Paragraph(mx("Fax: " + ent["fax"]), sm))
    head = Table([[left, right]], colWidths=[W * 0.6, W * 0.4])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 1.6, BLUE_C), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story += [head, Spacer(1, 2 * mm)] + _title(ctx, ent.get("quote_title") or "报 价 单", size=15) + [Spacer(1, 2 * mm)]
    rc = _contact(snap)
    sname, sphone, smobile, semail = _sender(snap)
    lab = ParagraphStyle("lc", parent=ctx.cell)
    info = [
        [ctx.p("项目：", lab), ctx.p(snap["project"]["name"]), ctx.p("报价单号：", lab), ctx.p(snap["code"])],
        [ctx.p("To/致：", lab), ctx.p(snap["recipient"]["name"]), ctx.p(f"From/自：{sname}", lab), ""],
        [ctx.p("Tel/座机：", lab), ctx.p(rc.get("phone") or ""), ctx.p(f"Tel/座机：{sphone or ent.get('phone') or ''}", lab), ""],
        [ctx.p("Mobile/移动：", lab), ctx.p(rc.get("mobile") or ""), ctx.p(f"Mobile/移动：{smobile}", lab), ""],
        [ctx.p("Attn/联系人：", lab), ctx.p(" ".join(x for x in [rc.get("name"), rc.get("position")] if x)), ctx.p(f"Email/邮箱：{semail or ent.get('email') or ''}", lab), ""],
    ]
    it = Table(info, colWidths=[24 * mm, W * 0.62 - 24 * mm, 20 * mm, W * 0.38 - 20 * mm])
    it.setStyle(TableStyle([("SPAN", (2, 1), (3, 1)), ("SPAN", (2, 2), (3, 2)), ("SPAN", (2, 3), (3, 3)), ("SPAN", (2, 4), (3, 4)),
                            ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [it, Spacer(1, 3 * mm)] + _reference_line(ctx, include_copper=True)
    hdr = ["序号", "名称", "润滑部位" if snap.get("part_label") is None else snap["part_label"], "单位", "数量", "单价", "总价", "备注"]
    cols = [11 * mm, 32 * mm, None, 12 * mm, 18 * mm, 21 * mm, 23 * mm, 24 * mm]
    _unit_strs = [plain(ln["unit_price"]) for ln in snap["lines"]]
    _amt_strs = [plain(ln["amount"]) for ln in snap["lines"]]
    cols, _aw = _layout_amount_cols(W, cols, 2, 12, {5: _unit_strs, 6: _amt_strs}, ctx.cell_c.fontSize)  # 单价/总价列默认 6+6 内边距
    rows = [[ctx.p(h, ctx.cell_c) for h in hdr]]
    spans, prev, start = [], None, None
    # 样张中连续同名系统合并“名称”单元格。合并单元格无法跨页拆分：明细超过 18 行（可能跨页）时改为逐行重复名称
    allow_merge = len(snap["lines"]) <= 18
    for i, ln in enumerate(snap["lines"], 1):
        r = len(rows)
        same = allow_merge and prev is not None and ln["name"] == prev
        rows.append([ctx.p(i, ctx.cell_c), "" if same else ctx.p(ln["name"], ctx.cell_c), ctx.p(ln["model"], ctx.cell_c), ctx.p(ln["unit"], ParagraphStyle("ub", parent=ctx.cell_c, fontName=ctx.FB)),
                     ctx.p(ln["qty_total"], ctx.cell_c),
                     _fit_para(ctx, plain(ln["unit_price"]), _aw[5] - 12, ctx.cell_c),   # 单价列（可加宽）
                     _fit_para(ctx, plain(ln["amount"]), _aw[6] - 12, ctx.cell_c),        # 总价列（可加宽）
                     ctx.p(_c_remark(ln), ctx.cell_c)])
        if same:
            spans.append((start, r))
        else:
            start = r
        prev = ln["name"]
    lt = Table(rows, colWidths=cols, repeatRows=1)
    st = [("GRID", (0, 0), (-1, -1), 0.5, INK), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
    merged = {}
    for a, b in spans:
        merged[a] = max(merged.get(a, a), b)
    for a, b in merged.items():
        st.append(("SPAN", (1, a), (1, b)))
    lt.setStyle(TableStyle(st))
    story.append(lt)
    story += _totals_block(ctx, W, label_total="合计：", show_upper=True, upper_left=True, compact_numbers=True)
    story += _note_block(ctx, W)
    sign = Table([["", ctx.p(ent["name"])], ["", ctx.p(snap["quote_date"])]], colWidths=[W * 0.62, W * 0.38])
    sign.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [Spacer(1, 10 * mm), KeepTogether([sign])]
    return story


def _c_remark(ln):
    parts = [f"{p['value']}{p.get('unit') or ''}" if p.get("label") in ("润滑点数", "点数") else f"{p['label']}{p['value']}{p.get('unit') or ''}" for p in ln.get("params_shown", [])]
    parts += [ln.get("note") or ""]
    return "；".join(x for x in parts if x)


# ============================================================ 共用：合计、大写、注
def _totals_block(ctx, W, label_total, show_upper, upper_left=False, compact_numbers=False):
    snap = ctx.snap
    t = snap["totals"]
    fmt = plain if compact_numbers else money_fmt
    hq = int(snap.get("host_qty") or 1)
    # 先把右列金额字符串集中，用于“动态列宽 + 有限字号缩放”的单行适配（F-1）
    spec = []
    if snap.get("show_tax_breakdown"):
        # 未税单价口径：明细金额为未税额，合计区逐项列出未税合计、税额与含税合计（不让客户误把未税额当成总价）
        spec.append(("未税合计：", fmt(t["set_net"])))
        spec.append(("税额：", fmt(t["set_tax"])))
        label_total = label_total.rstrip("：") + "（含税）："
    spec.append((label_total, fmt(t["set_gross"])))
    if hq > 1:
        spec.append((f"{hq}{snap.get('host_label') or '台'}总计：", fmt(t["gross"])))

    base = ctx.bold_r.fontSize
    amounts = [a for _lbl, a in spec]
    pad = 6 + 2  # 合计区表格右列：默认左内边距 6 + 显式右内边距 2
    cap = W * 0.62  # 金额列最多占 62% 页宽，保证左侧标签列仍可读
    widest_base = max((_amount_width(a, base, True) for a in amounts), default=0.0)
    need_w = widest_base + pad + 2
    if need_w <= cap:
        amt_w, size = max(32 * mm, need_w), base          # 加宽金额列即可单行放下，字号不变（正常金额→仍为 32mm/9pt）
    else:
        amt_w = cap
        size = _fit_font_size(amounts, (amt_w - pad) - _AMOUNT_SAFETY_PT, base, True)  # 极端金额再缩放字号，下限以下则拒绝
    amt_style = ctx.bold_r if abs(size - base) < 1e-9 else ParagraphStyle("amt-tot", parent=ctx.bold_r, fontSize=size, leading=size + 3)
    rows = [[ctx.p(lbl, ctx.bold_r), Paragraph(mx(a, bold=True), amt_style)] for lbl, a in spec]
    tt = Table(rows, colWidths=[W - amt_w, amt_w])
    tt.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    out = [tt]
    if show_upper:
        up = rmb_upper(t["gross"])
        if upper_left:
            u = Table([[ctx.p("人民币金额(大写)："), ctx.p(up, ctx.bold)]], colWidths=[38 * mm, W - 38 * mm])
            u.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, INK), ("LEFTPADDING", (0, 0), (-1, -1), 6 * mm), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
            out.append(u)
        else:
            out.append(ctx.p(f"大写金额：{up}", ParagraphStyle("upr", parent=ctx.bold_r)))
    return out


def _note_block(ctx, W):
    items = _terms(ctx.snap)
    if ctx.snap.get("remark"):
        items.append("备注：" + ctx.snap["remark"])
    rows = [[ctx.p(f"{i}", ctx.cell_c), ctx.p(x)] for i, x in enumerate(items, 1)]
    t = Table(rows, colWidths=[12 * mm, W - 12 * mm])
    t.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return [Spacer(1, 1 * mm), ctx.p("注：", ctx.bold), t]


BUILDERS = {"A": _build_a, "B": _build_b, "C": _build_c}


def _mark_canvas(base_cls, font, kind):
    mark = TRIAL_MARK if kind == "TRIAL" else DRAFT_MARK
    top = ("【试用报价】模板未经负责人签收，仅供内部试用与样张比对，不得作为正式报价发送。" if kind == "TRIAL"
           else "【草稿预览】尚未生成冻结版本，内容可能变化，不得发送给客户。")

    class MarkCanvas(base_cls):
        def _draw_footer(self, total):
            super()._draw_footer(total)
            w, h = self._pagesize
            self.saveState()
            self.setFillColorRGB(0.78, 0.18, 0.18, alpha=0.16)
            self.translate(w / 2, h / 2)
            self.rotate(35)
            draw_mixed(self, 0, 0, mark, font, 30, align="center")
            self.restoreState()
            self.saveState()
            self.setFillColorRGB(0.7, 0.1, 0.1)
            draw_mixed(self, 15 * mm, h - 7 * mm, top, font, 7.5)
            self.restoreState()

    return MarkCanvas


def render_quote_pdf(snap: dict) -> bytes:
    if snap.get("template_version") == "2026.11-r2":
        from .pdf_r2 import render_quote_pdf as r2

        return r2(snap)
    ctx = _Ctx(snap)
    ent = ctx.ent
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=18 * mm,
                            title=("【试用】" if ctx.trial else "") + f"报价单 {snap['code']}", author=ent["name"],
                            subject="TRIAL-未签收试用报价" if ctx.trial else "报价单", creator="Industrial Sales Hub", producer="Industrial Sales Hub", invariant=1, lang="zh-CN")
    W = A4[0] - 28 * mm
    story = BUILDERS.get(snap["template_key"], _build_a)(ctx, W)
    footer = f"{ent.get('short_name') or ent['name']} · {snap['code']} · 模板 {snap['template_key']}/{TEMPLATE_VERSION}"
    footer += " · 试用（未签收）" if ctx.trial else (f" · {ent.get('footer_note')}" if ent.get("footer_note") else "")
    canvas_cls = make_numbered_canvas(footer, ctx.F)
    if ctx.watermark:
        canvas_cls = _mark_canvas(canvas_cls, ctx.F, ctx.watermark)
    doc.build(story, canvasmaker=canvas_cls)
    return buf.getvalue()
