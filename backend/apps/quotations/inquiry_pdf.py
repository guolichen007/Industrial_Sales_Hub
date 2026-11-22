"""RC3 收口 C3（决策 D-7）：内部《项目询价登记单》PDF。

只读取 Inquiry 一条记录的白名单字段；**不含任何价格/金额**（询价明细在写入时已拒绝价格键，这里再按白名单取值）。
标注“内部询价登记单 / 非对外正式报价 / 不含价格”；页脚记录生成人与生成时间。不做模板上传、盖章、邮件发送。
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.common.pdfkit import make_numbered_canvas, mx, register_fonts

INQUIRY_ITEM_FIELDS = ("model", "name", "qty", "unit", "note")
INQUIRY_MARK = "内部询价登记单 · 非对外正式报价 · 不含价格字段"


def inquiry_snapshot(inq) -> dict:
    """白名单快照（便于测试断言“只用这些字段”）。"""
    return {
        "code": inq.code, "status": inq.get_status_display(), "project": f"{inq.project.code} {inq.project.name}",
        "inquirer": inq.inquirer.name if inq.inquirer_id else "", "inquirer_contact": inq.inquirer_contact,
        "received_on": inq.received_on.isoformat() if inq.received_on else "", "due_on": inq.due_on.isoformat() if inq.due_on else "",
        "requirement": inq.requirement or "",
        "items": [{k: str((it or {}).get(k, ""))[:120] for k in INQUIRY_ITEM_FIELDS} for it in (inq.items or []) if isinstance(it, dict)],
        "created_by": inq.created_by.name if inq.created_by_id else "", "created_at": inq.created_at.strftime("%Y-%m-%d %H:%M") if inq.created_at else "",
        "updated_by": inq.updated_by.name if inq.updated_by_id else "", "updated_at": inq.updated_at.strftime("%Y-%m-%d %H:%M") if inq.updated_at else "",
        "quote_codes": [q.code for q in inq.quotes.all()],  # 仅编号，不含金额
    }


def render_inquiry_pdf(snap: dict, generator: str, generated_at: str) -> bytes:
    f = register_fonts()
    F, FB = f["font"], f["bold"]
    base = ParagraphStyle("b", fontName=F, fontSize=9.5, leading=13)
    small = ParagraphStyle("s", parent=base, fontSize=8, leading=11, textColor=colors.HexColor("#5b6772"))
    lab = ParagraphStyle("l", parent=base, fontName=FB, textColor=colors.HexColor("#10263b"))
    head = ParagraphStyle("h", parent=base, fontName=FB, textColor=colors.white)
    h1 = ParagraphStyle("h1", parent=base, fontName=FB, fontSize=17, leading=22, textColor=colors.HexColor("#10263b"))
    warn = ParagraphStyle("w", parent=base, textColor=colors.HexColor("#b03030"))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"项目询价登记单 {snap['code']}", author=generator, subject="INTERNAL-询价登记", creator="Industrial Sales Hub", producer="Industrial Sales Hub", invariant=1)
    W = A4[0] - 32 * mm
    story = [Paragraph(mx("项目询价登记单", True), h1), Spacer(1, 1.5 * mm),
             Paragraph(mx(f"【{INQUIRY_MARK}】本单记录客户/主机厂的询价需求，用于内部登记与查询；对客户报价请以报价中心生成的正式报价单为准。"
                          "本单不含结构化价格字段；“需求说明”“备注”为人工填写的文字，外发前请人工核对其中不含价格。"), warn),
             Spacer(1, 4 * mm)]
    info = [["询价编号", snap["code"], "状态", snap["status"]],
            ["项目", snap["project"], "关联报价编号", "、".join(snap["quote_codes"]) or "—"],
            ["询价方", snap["inquirer"] or "—", "询价联系人", snap["inquirer_contact"] or "—"],
            ["收到日期", snap["received_on"] or "—", "要求回复日期", snap["due_on"] or "—"],
            ["登记人 / 时间", f"{snap['created_by']} {snap['created_at']}", "最后修改", f"{snap['updated_by']} {snap['updated_at']}"]]
    t = Table([[Paragraph(mx(a), lab), Paragraph(mx(b), base), Paragraph(mx(c), lab), Paragraph(mx(d), base)] for a, b, c, d in info],
              colWidths=[26 * mm, W / 2 - 26 * mm, 28 * mm, W / 2 - 28 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c9d1d9")), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f4f7")),
                           ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f1f4f7")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [t, Spacer(1, 4 * mm), Paragraph(mx("需求说明", True), lab), Spacer(1, 1 * mm),
              Paragraph(mx(snap["requirement"] or "（无）").replace("\n", "<br/>"), base), Spacer(1, 4 * mm), Paragraph(mx("产品明细（不含价格）", True), lab), Spacer(1, 1 * mm)]
    rows = [[Paragraph(mx(h, True), head) for h in ("序号", "型号", "名称", "数量", "单位", "备注")]]
    for i, it in enumerate(snap["items"], 1):
        rows.append([Paragraph(mx(str(x)), base) for x in (i, it["model"], it["name"], it["qty"], it["unit"], it["note"])])
    if len(rows) == 1:
        rows.append([Paragraph(mx("（无明细）"), small)] + [""] * 5)
    it_t = Table(rows, colWidths=[12 * mm, 34 * mm, None, 18 * mm, 16 * mm, 40 * mm], repeatRows=1)
    it_t._argW[2] = W - (12 + 34 + 18 + 16 + 40) * mm
    it_t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#10263b")), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c9d1d9")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [it_t, Spacer(1, 6 * mm), Paragraph(mx(f"生成人：{generator}　生成时间：{generated_at}　（系统生成的内部登记单，不作为报价或合同依据）"), small)]
    doc.build(story, canvasmaker=make_numbered_canvas(f"询价登记单 {snap['code']} · {generator} · {generated_at} · {INQUIRY_MARK}", F))
    return buf.getvalue()
