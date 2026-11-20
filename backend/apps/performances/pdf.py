"""客户案例册 PDF：只接受白名单快照。"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.common.pdfkit import make_numbered_canvas, mx, read_private, register_fonts

from .public import PUBLIC_FIELDS


def _photo_strip(photos, width, style):
    """已授权照片缩略图（最多 3 张一行）。按快照中的 sha256 读取并校验，内容不一致则拒绝生成。"""
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image

    from apps.common.pdfkit import sha256_bytes
    from apps.performances.models import CasePhoto

    cells = []
    for ph in photos[:3]:
        obj = CasePhoto.objects.filter(pk=ph["id"], sha256=ph["sha256"], authorized=True).first()
        if not obj:
            raise ValueError("案例照片未授权或已变化")
        data = read_private(obj.storage_key)
        if sha256_bytes(data) != ph["sha256"]:
            raise ValueError("案例照片校验失败")
        w, h = ImageReader(io.BytesIO(data)).getSize()
        k = min((width / 3 - 4 * mm) / w, 38 * mm / h)
        cells.append([Image(io.BytesIO(data), width=w * k, height=h * k), Paragraph(mx(ph.get("caption") or ""), style)])
    t = Table([[c[0] for c in cells], [c[1] for c in cells]], colWidths=[width / 3] * len(cells), hAlign="LEFT")
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 2)]))
    return t


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
        if c.get("photos"):
            rows.append(["", _photo_strip(c["photos"], W - 12 * mm, small)])
        t = Table(rows, colWidths=[12 * mm, W - 12 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEABOVE", (0, 0), (-1, 0), 0.6, colors.HexColor("#d9e3ea")), ("TOPPADDING", (0, 0), (-1, 0), 8), ("TOPPADDING", (0, 1), (-1, -1), 1.5), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story += [KeepTogether([t]), Spacer(1, 3 * mm)]

    doc.build(story, canvasmaker=make_numbered_canvas(" · ".join(x for x in [issuer, "项目案例", "内容已经过公开字段审核"] if x), F))
    return buf.getvalue()
