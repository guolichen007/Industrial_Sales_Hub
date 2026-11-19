"""客户案例册 PDF：只接受白名单快照。"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.common.pdfkit import make_numbered_canvas, mx, register_fonts

from .public import PUBLIC_FIELDS


def render_case_pdf(snap: dict) -> bytes:
    for c in snap["cases"]:
        extra = set(c) - set(PUBLIC_FIELDS)
        if extra:
            raise ValueError(f"案例快照包含非白名单字段：{extra}")
    f = register_fonts()
    F, FB = f["font"], f["bold"]
    navy, teal = colors.HexColor("#10263b"), colors.HexColor("#0b948b")
    base = ParagraphStyle("b", fontName=F, fontSize=9, leading=13.5, textColor=colors.HexColor("#25323f"))
    small = ParagraphStyle("s", parent=base, fontSize=8, leading=11, textColor=colors.HexColor("#5b6772"))
    h1 = ParagraphStyle("h1", parent=base, fontName=FB, fontSize=22, leading=28, textColor=navy)
    h2 = ParagraphStyle("h2", parent=base, fontName=FB, fontSize=11.5, leading=15, textColor=navy)
    tag = ParagraphStyle("t", parent=small, textColor=teal, fontName=FB)

    buf = io.BytesIO()
    issuer = snap.get("issuer") or ""
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=17 * mm, rightMargin=17 * mm, topMargin=16 * mm, bottomMargin=20 * mm,
                            title=snap["title"], author=issuer or "Industrial Sales Hub", subject="项目案例", creator="Industrial Sales Hub", producer="Industrial Sales Hub", invariant=1)
    W = A4[0] - 34 * mm
    story = []
    band = Table([[Paragraph(f"<font color='white'>{mx(snap['title'], True)}</font>", ParagraphStyle("bt", parent=h1, textColor=colors.white))],
                  [Paragraph(f"<font color='#cfe0ea'>{mx((snap.get('subtitle') or '') + '　' + issuer)}</font>", small)]], colWidths=[W])
    band.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), navy), ("LEFTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, 0), 14), ("BOTTOMPADDING", (0, -1), (-1, -1), 12)]))
    story += [band, Spacer(1, 6 * mm), Paragraph(mx(f"共收录 {len(snap['cases'])} 个项目案例"), small), Spacer(1, 4 * mm)]

    for i, c in enumerate(snap["cases"], 1):
        meta = "　·　".join(x for x in [str(c["year"]), c["region"], c["industry"], c["machine_type"]] if x)
        rows = [
            [Paragraph(mx(f"{i:02d}", True), ParagraphStyle("n", parent=h2, textColor=teal)), Paragraph(mx(c["title"], True), h2)],
            ["", Paragraph(mx(meta, True), tag)],
            ["", Paragraph(mx("供货产品：" + (c["product_summary"] or "—")), base)],
        ]
        if c.get("owner_display"):
            rows.append(["", Paragraph(mx("项目业主：" + c["owner_display"]), base)])
        if c.get("public_summary"):
            rows.append(["", Paragraph(mx(c["public_summary"]), small)])
        t = Table(rows, colWidths=[12 * mm, W - 12 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEABOVE", (0, 0), (-1, 0), 0.6, colors.HexColor("#d9e3ea")), ("TOPPADDING", (0, 0), (-1, 0), 8), ("TOPPADDING", (0, 1), (-1, -1), 1.5), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story += [KeepTogether([t]), Spacer(1, 3 * mm)]

    doc.build(story, canvasmaker=make_numbered_canvas(" · ".join(x for x in [issuer, "项目案例", "内容已经过公开字段审核"] if x), F))
    return buf.getvalue()
