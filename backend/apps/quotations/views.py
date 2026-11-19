from urllib.parse import quote as urlquote

from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.audit.service import audit
from apps.common.errors import BusinessError
from apps.common.money import rmb_upper
from apps.common.params import int_param, str_param
from apps.common.roles import HasCap, can
from apps.products.models import Product

from . import services
from .models import Quote, QuoteLine, QuoteRevision

STATUS_FLOW = {
    "DRAFT": {"CANCELLED"},
    "GENERATED": {"SENT", "CANCELLED", "ACCEPTED", "LOST"},
    "SENT": {"ACCEPTED", "LOST", "CANCELLED"},
    "ACCEPTED": {"SENT"},
    "LOST": {"SENT"},
    "CANCELLED": set(),
}
STATUS_LABELS = dict(Quote._meta.get_field("status").choices)


class QuoteLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuoteLine
        fields = ["id", "sort", "product", "model", "name", "brand", "description", "params", "visible_param_keys", "unit", "qty", "multiplier", "unit_price", "tax_rate", "discount_rate", "line_net", "line_tax", "line_gross", "remark"]
        read_only_fields = ["line_net", "line_tax", "line_gross", "product"]

    def validate(self, data):
        for f, msg in [("qty", "数量"), ("unit_price", "单价")]:
            if f in data and data[f] < 0:
                raise serializers.ValidationError({f: f"{msg}不能为负"})
        if "multiplier" in data and data["multiplier"] <= 0:
            raise serializers.ValidationError({"multiplier": "乘数必须大于 0"})
        if "tax_rate" in data and not (0 <= data["tax_rate"] < 1):
            raise serializers.ValidationError({"tax_rate": "税率应在 0～1 之间"})
        if "discount_rate" in data and not (0 <= data["discount_rate"] < 1):
            raise serializers.ValidationError({"discount_rate": "折扣率应在 0～1 之间"})
        if "params" in data:
            if not isinstance(data["params"], list) or not all(isinstance(p, dict) for p in data["params"]):
                raise serializers.ValidationError({"params": "参数格式错误"})
            data["params"] = [{"key": str(p.get("key") or f"p{i + 1}")[:40], "label": str(p.get("label", ""))[:40], "value": str(p.get("value", ""))[:120], "unit": str(p.get("unit", ""))[:20]} for i, p in enumerate(data["params"][:60])]
        if "visible_param_keys" in data:
            v = data["visible_param_keys"]
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise serializers.ValidationError({"visible_param_keys": "格式错误"})
        return data


class RevisionSerializer(serializers.ModelSerializer):
    lines = serializers.SerializerMethodField()
    code = serializers.CharField(read_only=True)
    total_upper = serializers.SerializerMethodField()
    generated_by_name = serializers.CharField(source="generated_by.name", read_only=True, default="")

    class Meta:
        model = QuoteRevision
        fields = ["id", "quote", "version", "code", "status", "quote_date", "valid_days", "delivery_terms", "payment_terms", "terms", "remark", "show_upper_amount",
                  "ref_copper_price", "ref_exchange_rate", "ref_valid_until", "ref_note", "price_basis", "lock_version",
                  "total_net", "total_tax", "total_gross", "total_upper", "snapshot_sha256", "template_key", "template_version", "pdf_sha256", "pdf_size", "last_error", "generated_by_name", "generated_at", "created_at", "lines"]
        read_only_fields = ["quote", "version", "status", "price_basis", "lock_version", "total_net", "total_tax", "total_gross", "snapshot_sha256", "template_key", "template_version", "pdf_sha256", "pdf_size", "last_error", "generated_at"]

    def get_lines(self, obj):
        return QuoteLineSerializer(QuoteLine.objects.filter(revision_id=obj.pk).order_by("sort", "id"), many=True).data

    def get_total_upper(self, obj):
        return rmb_upper(obj.total_gross)

    def validate_quote_date(self, v):
        import datetime

        if not (datetime.date(2000, 1, 1) <= v <= datetime.date(2100, 12, 31)):
            raise serializers.ValidationError("报价日期超出范围")
        return v

    def validate_ref_valid_until(self, v):
        import datetime

        if v and not (datetime.date(2000, 1, 1) <= v <= datetime.date(2100, 12, 31)):
            raise serializers.ValidationError("日期超出范围")
        return v

    def validate_valid_days(self, v):
        if not (1 <= v <= 365):
            raise serializers.ValidationError("有效期应在 1～365 天")
        return v


class QuoteSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    project_code = serializers.CharField(source="project.code", read_only=True)
    recipient_name = serializers.CharField(source="recipient.name", read_only=True)
    legal_entity_name = serializers.CharField(source="legal_entity.short_name", read_only=True)
    owner_name = serializers.CharField(source="owner.name", read_only=True, default="")
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    latest = serializers.SerializerMethodField()
    revisions = serializers.SerializerMethodField()

    class Meta:
        model = Quote
        fields = ["id", "code", "project", "project_name", "project_code", "recipient", "recipient_name", "recipient_contact", "legal_entity", "legal_entity_name", "title", "status", "status_label", "owner_name", "created_at", "updated_at", "latest", "revisions"]
        read_only_fields = ["code", "status"]

    def get_latest(self, obj):
        r = obj.revisions.order_by("-version").first()
        if not r:
            return None
        valid_until = r.quote_date + timezone.timedelta(days=r.valid_days)
        return {"id": r.id, "version": r.version, "status": r.status, "total_gross": str(r.total_gross), "quote_date": r.quote_date, "valid_until": valid_until}

    def get_revisions(self, obj):
        return [{"id": r.id, "version": r.version, "status": r.status, "total_gross": str(r.total_gross), "pdf_sha256": r.pdf_sha256, "generated_at": r.generated_at} for r in obj.revisions.order_by("version")]

    def validate(self, data):
        rc = data.get("recipient_contact")
        rec = data.get("recipient") or (self.instance and self.instance.recipient)
        if rc and rec and rc.account_id != rec.id:
            raise serializers.ValidationError({"recipient_contact": "联系人不属于接收客户"})
        ent = data.get("legal_entity")
        if ent and not ent.active:
            raise serializers.ValidationError({"legal_entity": "出具主体已停用"})
        return data


class QuoteViewSet(viewsets.ModelViewSet):
    """报价中心：仅 LEADER/ASSISTANT（D2）。销售、财务、管理员请求一律 403（不靠前端隐藏）。"""

    serializer_class = QuoteSerializer
    permission_classes = [HasCap.of("quote.access")]
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        qs = Quote.objects.select_related("project", "recipient", "legal_entity", "owner").prefetch_related("revisions")
        p = self.request.query_params
        if q := p.get("q"):
            q = str_param(q, "q", 100)
            qs = qs.filter(Q(code__icontains=q) | Q(project__name__icontains=q) | Q(recipient__name__icontains=q) | Q(revisions__lines__model__icontains=q) | Q(revisions__lines__name__icontains=q)).distinct()
        for f in ("project", "recipient", "legal_entity"):
            if (v := int_param(p.get(f), f)) is not None:
                qs = qs.filter(**{f: v})
        if (st := p.get("status")) and st in STATUS_FLOW:
            qs = qs.filter(status=st)
        for f, lookup in (("date_from", "created_at__date__gte"), ("date_to", "created_at__date__lte")):
            if d := p.get(f):
                try:
                    qs = qs.filter(**{lookup: timezone.datetime.fromisoformat(d).date()})
                except ValueError:
                    raise serializers.ValidationError({f: "日期格式应为 YYYY-MM-DD"})
        return qs

    def perform_create(self, s):
        from apps.common.retry import create_with_code

        def _create(code):
            q = s.save(code=code, owner=self.request.user)
            QuoteRevision.objects.create(quote=q, version=1, quote_date=timezone.localdate(), terms=q.legal_entity.default_terms, price_basis=q.legal_entity.price_basis, created_by=self.request.user)
            return q

        q = create_with_code(services.next_quote_code, _create, serializer=s)
        audit(self.request, "create", "quote", q.id, f"新建报价 {q.code}（{q.legal_entity.short_name} → {q.recipient.name}）")

    def update(self, request, *a, **kw):
        partial = kw.pop("partial", False)
        self.get_object()
        q = Quote.objects.select_for_update().get(pk=kw.get("pk") or self.kwargs["pk"])
        s = self.get_serializer(q, data=request.data, partial=partial)
        s.is_valid(raise_exception=True)
        frozen = QuoteRevision.objects.filter(quote=q).exclude(status=QuoteRevision.DRAFT).exists()
        if frozen and any(k in s.validated_data and s.validated_data[k] != getattr(q, k) for k in ("project", "recipient", "legal_entity")):
            raise BusinessError("报价已有冻结版本，不能更换项目/接收客户/出具公司；请新建报价。", code="revision_frozen")
        q = s.save()
        if "legal_entity" in s.validated_data:
            QuoteRevision.objects.filter(quote=q, status=QuoteRevision.DRAFT).update(price_basis=q.legal_entity.price_basis)
        audit(request, "update", "quote", q.id, f"修改报价 {q.code}")
        return Response(self.get_serializer(q).data)

    @action(detail=True, methods=["post"])
    def status(self, request, pk=None):
        self.get_object()
        q = Quote.objects.select_for_update().get(pk=pk)
        new = request.data.get("status")
        if not isinstance(new, str) or new not in STATUS_FLOW.get(q.status, set()):
            raise BusinessError(f"不能从「{q.get_status_display()}」变更为「{STATUS_LABELS.get(new, new) if isinstance(new, str) else '无效状态'}」", code="bad_transition")
        if q.status == "ACCEPTED" and q.orders.filter(status="CONFIRMED").exists():
            raise BusinessError("该报价已有有效的合同/订单确认，不能改回其他状态；请先撤销确认。", code="has_order")
        old = q.get_status_display()
        q.status = new
        q.save(update_fields=["status", "updated_at"])
        audit(request, "status", "quote", q.id, f"报价 {q.code}：{old} → {q.get_status_display()}（报价状态不等于业绩）")
        return Response(QuoteSerializer(q).data)

    @action(detail=True, methods=["post"])
    def revisions(self, request, pk=None):
        q = self.get_object()
        rev = services.create_next_revision(q, request.user)
        audit(request, "new_revision", "quote", q.id, f"创建新版本 {rev.code}")
        return Response(RevisionSerializer(rev).data, status=201)


def _fresh(rev_id):
    return QuoteRevision.objects.select_related("quote").get(pk=rev_id)


class RevisionViewSet(viewsets.GenericViewSet):
    queryset = QuoteRevision.objects.select_related("quote__legal_entity", "quote__recipient", "quote__project")
    serializer_class = RevisionSerializer
    permission_classes = [HasCap.of("quote.access")]

    def _expected(self, request):
        v = request.data.get("expected_version", request.headers.get("If-Match"))
        return int_param(v, "expected_version")

    def retrieve(self, request, pk=None):
        return Response(RevisionSerializer(self.get_object()).data)

    def partial_update(self, request, pk=None):
        self.get_object()
        rev = services.lock_revision_for_edit(int(pk), self._expected(request))
        s = RevisionSerializer(rev, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        services.bump(rev)
        return Response(RevisionSerializer(_fresh(rev.pk)).data)

    @action(detail=True, methods=["post"], url_path="lines")
    def add_line(self, request, pk=None):
        self.get_object()
        rev = services.lock_revision_for_edit(int(pk), self._expected(request))
        data = {k: v for k, v in request.data.items() if k not in ("expected_version", "product")}
        pid = int_param(request.data.get("product"), "product")
        if pid is not None:
            prod = Product.objects.filter(pk=pid).first()
            if not prod:
                raise BusinessError("产品不存在", code="not_found", http_status=404)
            if not prod.active:
                raise BusinessError("产品已停用，不能加入新报价", code="inactive_product")
            # 从产品库复制，之后与产品库解耦；临时改价不回写产品库
            defaults = {"model": prod.model, "name": prod.name, "brand": prod.brand, "description": prod.technical_desc, "params": prod.params,
                        "visible_param_keys": [p["key"] for p in prod.params], "unit": prod.unit, "unit_price": str(prod.base_price), "tax_rate": str(prod.tax_rate)}
            for k, v in defaults.items():
                data.setdefault(k, v)
        else:
            if not str(data.get("name", "")).strip():
                raise serializers.ValidationError({"name": "非标项需填写名称"})
            data.setdefault("model", "—")
            data.setdefault("unit_price", "0")
        data.setdefault("qty", "1")
        if "visible_param_keys" not in data and isinstance(data.get("params"), list):
            # 手工行：默认显示全部填写的参数（与从产品库加入的行一致）
            data["visible_param_keys"] = [str(p.get("key") or f"p{i + 1}")[:40] for i, p in enumerate(data["params"]) if isinstance(p, dict)]
        if QuoteLine.objects.filter(revision=rev).count() >= 300:
            raise BusinessError("单份报价最多 300 行", code="too_many_lines")
        data.setdefault("sort", QuoteLine.objects.filter(revision=rev).count() + 1)
        s = QuoteLineSerializer(data=data)
        s.is_valid(raise_exception=True)
        ln = s.save(revision=rev, product_id=pid)
        services.recalc_revision(rev)
        services.bump(rev)
        return Response(RevisionSerializer(_fresh(rev.pk)).data, status=201)

    @action(detail=True, methods=["patch", "delete"], url_path=r"lines/(?P<line_id>\d+)")
    def edit_line(self, request, pk=None, line_id=None):
        self.get_object()
        rev = services.lock_revision_for_edit(int(pk), self._expected(request))
        ln = QuoteLine.objects.select_for_update().filter(revision=rev, pk=int(line_id)).first()
        if not ln:
            raise BusinessError("报价行不存在", code="not_found", http_status=404)
        if request.method == "DELETE":
            ln.delete()
        else:
            s = QuoteLineSerializer(ln, data={k: v for k, v in request.data.items() if k != "expected_version"}, partial=True)
            s.is_valid(raise_exception=True)
            s.save()
        services.recalc_revision(rev)
        services.bump(rev)
        return Response(RevisionSerializer(_fresh(rev.pk)).data)

    @action(detail=True, methods=["post"], url_path="save-product")
    def save_line_as_product(self, request, pk=None):
        """把报价中的手工新增行存入产品库（商务/负责人）。不修改任何已有报价。"""
        if not can(request.user, "product.write"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied()
        rev = self.get_object()
        ln = QuoteLine.objects.filter(revision=rev, pk=int_param(request.data.get("line"), "line", required=True)).first()
        if not ln:
            raise BusinessError("报价行不存在", code="not_found", http_status=404)
        sku = str_param(request.data.get("sku"), "sku", 60, required=True)
        cat = int_param(request.data.get("category"), "category", required=True)
        from apps.products.models import ProductCategory

        if not ProductCategory.objects.filter(pk=cat).exists():
            raise serializers.ValidationError({"category": "类别不存在"})
        if Product.objects.filter(sku__iexact=sku).exists():
            raise BusinessError(f"型号编码 {sku} 已存在", code="duplicate_sku")
        p = Product.objects.create(sku=sku, model=ln.model, name=ln.name, brand=ln.brand, category_id=cat, unit=ln.unit, tax_rate=ln.tax_rate,
                                   base_price=ln.unit_price, technical_desc=ln.description, params=ln.params, created_by=request.user)
        audit(request, "create", "product", p.id, f"由报价行新增产品 {p.sku}")
        return Response({"id": p.id, "sku": p.sku}, status=201)

    @action(detail=True, methods=["post"])
    def render(self, request, pk=None):
        self.get_object()
        try:
            rev = services.generate_revision(int(pk), request.user)
        except services.PdfFailed as e:
            audit(request, "generate_pdf_failed", "quote_revision", pk, f"报价 PDF 生成失败：{e.message[:120]}")
            resp = Response({"code": "pdf_failed", "message": f"PDF 生成失败，草稿已保留，可修正后重试：{e.message}", "field_errors": {}, "request_id": getattr(request, "request_id", "")}, status=500)
            resp._ish_commit = True  # 提交 last_error 与审计记录（草稿内容未改变）
            return resp
        audit(request, "generate_pdf", "quote_revision", rev.id, f"生成报价 PDF {rev.code}", {"pdf_sha256": rev.pdf_sha256, "snapshot_sha256": rev.snapshot_sha256})
        return Response(RevisionSerializer(_fresh(rev.pk)).data)

    @action(detail=True, methods=["get"])
    def preview(self, request, pk=None):
        """草稿预览：直接渲染（不存档）。已冻结版本返回存档原字节。"""
        from .pdf import render_quote_pdf

        rev = self.get_object()
        if rev.status == QuoteRevision.GENERATED:
            pdf = services.read_revision_pdf(rev)
        else:
            services.recalc_revision(rev, save=False)
            pdf = render_quote_pdf(services.build_snapshot(rev))
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="preview-{rev.id}.pdf"'
        return resp

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        rev = self.get_object()
        if rev.status != QuoteRevision.GENERATED:
            raise BusinessError("该版本尚未生成 PDF", code="not_generated", http_status=404)
        data = services.read_revision_pdf(rev)  # 正式下载永远返回存档原字节（不重渲染）
        audit(request, "download_pdf", "quote_revision", rev.id, f"下载报价 PDF {rev.code}")
        resp = HttpResponse(data, content_type="application/pdf")
        disp = "inline" if request.query_params.get("inline") == "1" else "attachment"
        fname = f"{rev.code}.pdf"
        resp["Content-Disposition"] = f"{disp}; filename=\"{fname}\"; filename*=UTF-8''{urlquote(fname)}"
        resp["X-Content-SHA256"] = rev.pdf_sha256
        return resp


def quote_summary_for(user, project):
    """项目详情中的报价摘要：仅报价权限角色（D2：销售/财务/管理员一律空列表）。"""
    if not can(user, "quote.access"):
        return []
    qs = project.quotes.select_related("recipient", "legal_entity").prefetch_related("revisions")
    out = []
    for q in qs:
        r = q.revisions.order_by("-version").first()
        out.append({"id": q.id, "code": q.code, "recipient_name": q.recipient.name, "legal_entity_name": q.legal_entity.short_name, "status": q.status, "status_label": q.get_status_display(),
                    "latest_version": r.version if r else None, "total_gross": str(r.total_gross) if r else None,
                    "frozen_revisions": [{"id": x.id, "version": x.version} for x in q.revisions.order_by("version") if x.status == QuoteRevision.GENERATED], "updated_at": q.updated_at})
    return out
