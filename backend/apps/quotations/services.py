"""报价领域服务：金额计算、快照冻结、PDF 生成、新版本复制。"""
from django.db import transaction
from django.utils import timezone

from apps.common.errors import BusinessError
from apps.common.money import line_amounts, totals
from apps.common.pdfkit import canonical_json, read_private, sha256_bytes, store_private

from .models import Quote, QuoteLine, QuoteRevision
from .pdf import TEMPLATE_VERSION, render_quote_pdf


def next_quote_code():
    year = timezone.localdate().year
    prefix = f"QT-{year}-"
    last = Quote.objects.select_for_update().filter(code__startswith=prefix).order_by("-code").values_list("code", flat=True).first()
    n = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{n:04d}"


def recalc_line(line: QuoteLine):
    line.line_net, line.line_tax, line.line_gross = line_amounts(line.qty, line.unit_price, line.tax_rate, line.discount_rate)


def recalc_revision(rev: QuoteRevision, save=True):
    # 始终从数据库重新读取行，避免 prefetch 缓存导致合计使用旧数据
    lines = list(QuoteLine.objects.filter(revision_id=rev.pk).order_by("sort", "id"))
    for ln in lines:
        recalc_line(ln)
        if save:
            ln.save(update_fields=["line_net", "line_tax", "line_gross"])
    rev.total_net, rev.total_tax, rev.total_gross = totals([{"line_net": l.line_net, "line_tax": l.line_tax} for l in lines])
    if save:
        rev.save(update_fields=["total_net", "total_tax", "total_gross"])
    return rev


def ensure_editable(rev: QuoteRevision):
    if rev.status != QuoteRevision.DRAFT:
        raise BusinessError("该版本已生成并冻结，不能修改；请创建新版本。", code="revision_frozen")


def _s(v):
    return format(v, "f") if v is not None else None


def build_snapshot(rev: QuoteRevision) -> dict:
    q = rev.quote
    ent = q.legal_entity
    lines = []
    for ln in QuoteLine.objects.filter(revision_id=rev.pk).order_by("sort", "id"):
        visible = set(ln.visible_param_keys or [])
        shown = [{"key": p.get("key"), "label": p.get("label"), "value": p.get("value"), "unit": p.get("unit", "")} for p in (ln.params or []) if p.get("key") in visible]
        lines.append({
            "model": ln.model, "name": ln.name, "brand": ln.brand, "description": ln.description,
            "params_shown": shown, "unit": ln.unit, "qty": _s(ln.qty), "unit_price": _s(ln.unit_price),
            "tax_rate": _s(ln.tax_rate), "discount_rate": _s(ln.discount_rate),
            "line_net": _s(ln.line_net), "line_tax": _s(ln.line_tax), "line_gross": _s(ln.line_gross),
            "remark": ln.remark, "product_id": ln.product_id,
        })
    c = q.recipient_contact
    return {
        "schema": 1,
        "code": rev.code,
        "template_key": ent.template_key,
        "template_version": TEMPLATE_VERSION,
        "legal_entity": {k: getattr(ent, k) for k in ["code", "name", "short_name", "address", "phone", "fax", "email", "tax_no", "bank_name", "bank_account", "footer_note"]},
        "recipient": {"id": q.recipient_id, "name": q.recipient.name},
        "recipient_contact": {"name": c.name, "position": c.position, "mobile": c.mobile, "phone": c.phone} if c else None,
        "project": {"id": q.project_id, "code": q.project.code, "name": q.project.name},
        "title": q.title,
        "quote_date": rev.quote_date.isoformat(),
        "valid_days": rev.valid_days,
        "delivery_terms": rev.delivery_terms,
        "payment_terms": rev.payment_terms,
        "terms": rev.terms,
        "remark": rev.remark,
        "show_upper_amount": rev.show_upper_amount,
        "prepared_by": (rev.created_by.name if rev.created_by else ""),
        "lines": lines,
        "totals": {"net": _s(rev.total_net), "tax": _s(rev.total_tax), "gross": _s(rev.total_gross)},
    }


def generate_revision(rev_id: int, user) -> QuoteRevision:
    """生成 PDF 并冻结版本。成功后才标记 GENERATED；失败保留草稿与错误信息，可重试。"""
    with transaction.atomic():
        rev = QuoteRevision.objects.select_for_update(of=("self",)).select_related("quote__legal_entity", "quote__recipient", "quote__project", "quote__recipient_contact").get(pk=rev_id)
        ensure_editable(rev)
        if not rev.lines.exists():
            raise BusinessError("报价至少需要一行产品。", code="empty_quote")
        recalc_revision(rev)
        snap = build_snapshot(rev)
        try:
            pdf = render_quote_pdf(snap)
        except Exception as e:  # noqa: BLE001
            QuoteRevision.objects.filter(pk=rev.pk).update(last_error=str(e)[:300])
            raise BusinessError(f"PDF 生成失败，可修正后重试：{e}", code="pdf_failed", http_status=500)
        key = store_private("quotes", pdf)
        rev.snapshot = snap
        rev.snapshot_sha256 = sha256_bytes(canonical_json(snap))
        rev.template_key = snap["template_key"]
        rev.template_version = snap["template_version"]
        rev.pdf_storage_key = key
        rev.pdf_sha256 = sha256_bytes(pdf)
        rev.pdf_size = len(pdf)
        rev.status = QuoteRevision.GENERATED
        rev.generated_by = user
        rev.generated_at = timezone.now()
        rev.last_error = ""
        rev.save()
        q = rev.quote
        if q.status == "DRAFT":
            q.status = "GENERATED"
            q.save(update_fields=["status", "updated_at"])
        return rev


def rerender_frozen(rev: QuoteRevision) -> bytes:
    """仅用于验证：从冻结快照重新渲染，必须与存档 PDF 哈希一致。"""
    if not rev.snapshot:
        raise BusinessError("该版本尚未生成")
    return render_quote_pdf(rev.snapshot)


def read_revision_pdf(rev: QuoteRevision) -> bytes:
    data = read_private(rev.pdf_storage_key)
    if sha256_bytes(data) != rev.pdf_sha256:
        raise BusinessError("PDF 文件校验失败（哈希不一致），请联系管理员。", code="pdf_integrity", http_status=500)
    return data


@transaction.atomic
def create_next_revision(quote: Quote, user) -> QuoteRevision:
    """复制最新版本为新草稿版本；旧版本保持不变。"""
    quote = Quote.objects.select_for_update().get(pk=quote.pk)
    latest = quote.revisions.order_by("-version").first()
    if latest and latest.status == QuoteRevision.DRAFT:
        raise BusinessError(f"V{latest.version} 仍是草稿，可直接编辑，无需新建版本。", code="draft_exists")
    new = QuoteRevision.objects.create(
        quote=quote,
        version=(latest.version + 1) if latest else 1,
        quote_date=timezone.localdate(),
        valid_days=latest.valid_days if latest else 30,
        delivery_terms=latest.delivery_terms if latest else "",
        payment_terms=latest.payment_terms if latest else "",
        terms=latest.terms if latest else quote.legal_entity.default_terms,
        remark=latest.remark if latest else "",
        show_upper_amount=latest.show_upper_amount if latest else True,
        created_by=user,
    )
    if latest:
        for ln in latest.lines.all():
            ln.pk = None
            ln.id = None
            ln.revision = new
            ln.save()
        recalc_revision(new)
    quote.save(update_fields=["updated_at"])
    return new
