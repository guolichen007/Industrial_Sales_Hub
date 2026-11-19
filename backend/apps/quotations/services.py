"""报价领域服务：金额计算、快照冻结、PDF 生成、新版本复制。

并发规则（V2.1，L2-1）：
- 所有写操作都在请求级事务内（WriteTransactionMiddleware），并按固定顺序加锁：Quote → QuoteRevision；
- 加锁后重新读取状态并校验 DRAFT，杜绝“先检查后写入”的竞态；
- 草稿携带 lock_version，客户端可传 expected_version 做乐观并发防覆盖；
- 生成 PDF：至多成功一次；失败时整个事务回滚，草稿保持原样，并在独立事务记录 last_error。
"""
from django.db import transaction
from django.utils import timezone

from apps.common.errors import BusinessError
from apps.common.money import D, line_amounts, totals
from apps.common.pdfkit import canonical_json, read_private, sha256_bytes, store_private

from .models import Quote, QuoteLine, QuoteRevision
from .pdf import TEMPLATE_VERSION, render_quote_pdf


def next_quote_code():
    year = timezone.localdate().year
    prefix = f"QT-{year}-"
    last = Quote.objects.filter(code__startswith=prefix).order_by("-code").values_list("code", flat=True).first()
    n = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{n:04d}"


def recalc_line(line: QuoteLine, price_basis="NET"):
    qty = D(line.qty) * D(line.multiplier or 1)
    line.line_net, line.line_tax, line.line_gross = line_amounts(qty, line.unit_price, line.tax_rate, line.discount_rate, price_basis=price_basis)


def recalc_revision(rev: QuoteRevision, save=True):
    lines = list(QuoteLine.objects.filter(revision_id=rev.pk).order_by("sort", "id"))
    for ln in lines:
        recalc_line(ln, rev.price_basis)
        if save:
            ln.save(update_fields=["line_net", "line_tax", "line_gross"])
    rev.total_net, rev.total_tax, rev.total_gross = totals([{"line_net": l.line_net, "line_tax": l.line_tax} for l in lines])
    if save:
        rev.save(update_fields=["total_net", "total_tax", "total_gross"])
    return rev


def ensure_editable(rev: QuoteRevision):
    if rev.status != QuoteRevision.DRAFT:
        raise BusinessError("该版本已生成并冻结，不能修改；请创建新版本。", code="revision_frozen")


def lock_revision_for_edit(rev_id: int, expected_version=None) -> QuoteRevision:
    """固定锁顺序 Quote → Revision；加锁后校验草稿状态与乐观并发版本。"""
    quote_id = QuoteRevision.objects.filter(pk=rev_id).values_list("quote_id", flat=True).first()
    if quote_id is None:
        raise BusinessError("报价版本不存在", code="not_found", http_status=404)
    quote = Quote.objects.select_for_update().get(pk=quote_id)
    rev = QuoteRevision.objects.select_for_update(of=("self",)).select_related("quote__legal_entity").get(pk=rev_id)
    ensure_editable(rev)
    if quote.status == "CANCELLED":
        raise BusinessError("报价已取消，不能再修改或生成。", code="cancelled")
    if expected_version not in (None, "") and int(expected_version) != rev.lock_version:
        raise BusinessError("该草稿已被其他人修改，请刷新后再保存。", code="stale_version")
    return rev


def bump(rev: QuoteRevision):
    QuoteRevision.objects.filter(pk=rev.pk).update(lock_version=rev.lock_version + 1)
    rev.lock_version += 1


def _s(v):
    return format(v, "f") if v is not None else None


def build_snapshot(rev: QuoteRevision) -> dict:
    return build_snapshot_from(rev, QuoteLine.objects.filter(revision_id=rev.pk).order_by("sort", "id"))


def build_snapshot_from(rev: QuoteRevision, line_objs) -> dict:
    q = rev.quote
    ent = q.legal_entity
    lines = []
    for ln in line_objs:
        visible = set(ln.visible_param_keys or [])
        shown = [{"key": p.get("key"), "label": p.get("label"), "value": p.get("value"), "unit": p.get("unit", "")} for p in (ln.params or []) if p.get("key") in visible]
        lines.append({
            "model": ln.model, "name": ln.name, "brand": ln.brand, "description": ln.description,
            "params_shown": shown, "unit": ln.unit, "qty": _s(ln.qty), "multiplier": _s(ln.multiplier), "unit_price": _s(ln.unit_price),
            "tax_rate": _s(ln.tax_rate), "discount_rate": _s(ln.discount_rate),
            "line_net": _s(ln.line_net), "line_tax": _s(ln.line_tax), "line_gross": _s(ln.line_gross),
            "remark": ln.remark, "product_id": ln.product_id,
        })
    c = q.recipient_contact
    return {
        "schema": 2,
        "code": rev.code,
        "template_key": ent.template_key,
        "template_version": TEMPLATE_VERSION,
        "price_basis": rev.price_basis,
        "legal_entity": {k: getattr(ent, k) for k in ["code", "name", "short_name", "address", "phone", "fax", "email", "tax_no", "bank_name", "bank_account", "footer_note", "quote_title", "intro_text", "show_tax_columns"]},
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
        "reference": {"copper": rev.ref_copper_price, "fx": rev.ref_exchange_rate, "valid_until": rev.ref_valid_until.isoformat() if rev.ref_valid_until else "", "note": rev.ref_note},
        "show_upper_amount": rev.show_upper_amount,
        "prepared_by": (rev.created_by.name if rev.created_by else ""),
        "lines": lines,
        "totals": {"net": _s(rev.total_net), "tax": _s(rev.total_tax), "gross": _s(rev.total_gross)},
    }


class PdfFailed(Exception):
    def __init__(self, rev_id, message):
        super().__init__(message)
        self.rev_id = rev_id
        self.message = message


def generate_revision(rev_id: int, user) -> QuoteRevision:
    """生成 PDF 并冻结版本：至多成功一次；失败不留下半状态。

    顺序：加锁 → 内存中重算并构建快照（不写库）→ 渲染 PDF → 成功后才写入行金额、快照、文件与状态。
    渲染失败：只在同一事务里记录 last_error 并抛出 PdfFailed，由视图返回 500 并提交这条错误记录（草稿内容不变）。
    （不能用第二个数据库连接去写同一行：该行已被本事务 FOR UPDATE 锁住，会无限等待。）
    """
    failed = None
    with transaction.atomic():
        rev = lock_revision_for_edit(rev_id)
        rev = QuoteRevision.objects.select_related("quote__legal_entity", "quote__recipient", "quote__project", "quote__recipient_contact", "created_by").get(pk=rev.pk)
        lines_mem = list(QuoteLine.objects.filter(revision_id=rev.pk).order_by("sort", "id"))
        if not lines_mem:
            raise BusinessError("报价至少需要一行产品。", code="empty_quote")
        for ln in lines_mem:
            recalc_line(ln, rev.price_basis)
        rev.total_net, rev.total_tax, rev.total_gross = totals([{"line_net": l.line_net, "line_tax": l.line_tax} for l in lines_mem])
        snap = build_snapshot_from(rev, lines_mem)
        try:
            pdf = render_quote_pdf(snap)
        except Exception as e:  # noqa: BLE001
            failed = str(e)[:300] or type(e).__name__
            QuoteRevision.objects.filter(pk=rev.pk).update(last_error=failed)
        if failed is None:
            for ln in lines_mem:
                ln.save(update_fields=["line_net", "line_tax", "line_gross"])
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
            rev.lock_version += 1
            rev.save()
            q = rev.quote
            if q.status == "DRAFT":
                q.status = "GENERATED"
                q.save(update_fields=["status", "updated_at"])
    if failed is not None:
        raise PdfFailed(rev_id, failed)
    return rev


def rerender_frozen(rev: QuoteRevision) -> bytes:
    """仅用于工程验证（同模板/字体版本下）；正式下载始终返回存档原字节。"""
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
    """复制最新版本为新草稿；旧版本保持不变。"""
    quote = Quote.objects.select_for_update().get(pk=quote.pk)
    if quote.status == "CANCELLED":
        raise BusinessError("已取消的报价不能新建版本。", code="cancelled")
    latest = QuoteRevision.objects.select_for_update().filter(quote=quote).order_by("-version").first()
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
        ref_copper_price=latest.ref_copper_price if latest else "",
        ref_exchange_rate=latest.ref_exchange_rate if latest else "",
        ref_valid_until=latest.ref_valid_until if latest else None,
        ref_note=latest.ref_note if latest else "",
        price_basis=quote.legal_entity.price_basis,
        created_by=user,
    )
    if latest:
        for ln in QuoteLine.objects.filter(revision=latest).order_by("sort", "id"):
            ln.pk = None
            ln.id = None
            ln.revision = new
            ln.save()
        recalc_revision(new)
    quote.save(update_fields=["updated_at"])
    return new
