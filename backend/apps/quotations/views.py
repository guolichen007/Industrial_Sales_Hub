from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.service import audit
from apps.common.errors import BusinessError
from apps.common.money import rmb_upper
from apps.common.roles import SALES, HasCap, can, role_of
from apps.organizations.models import Contact
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


class QuoteLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuoteLine
        fields = ["id", "sort", "product", "model", "name", "brand", "description", "params", "visible_param_keys", "unit", "qty", "unit_price", "tax_rate", "discount_rate", "line_net", "line_tax", "line_gross", "remark"]
        read_only_fields = ["line_net", "line_tax", "line_gross"]

    def validate(self, data):
        for f, msg in [("qty", "数量"), ("unit_price", "单价")]:
            if f in data and data[f] < 0:
                raise serializers.ValidationError({f: f"{msg}不能为负"})
        if "tax_rate" in data and not (0 <= data["tax_rate"] < 1):
            raise serializers.ValidationError({"tax_rate": "税率应在 0～1 之间"})
        if "discount_rate" in data and not (0 <= data["discount_rate"] < 1):
            raise serializers.ValidationError({"discount_rate": "折扣率应在 0～1 之间"})
        if "params" in data and not isinstance(data["params"], list):
            raise serializers.ValidationError({"params": "参数格式错误"})
        return data


class RevisionSerializer(serializers.ModelSerializer):
    lines = QuoteLineSerializer(many=True, read_only=True)
    code = serializers.CharField(read_only=True)
    total_upper = serializers.SerializerMethodField()
    generated_by_name = serializers.CharField(source="generated_by.name", read_only=True, default="")

    class Meta:
        model = QuoteRevision
        fields = ["id", "quote", "version", "code", "status", "quote_date", "valid_days", "delivery_terms", "payment_terms", "terms", "remark", "show_upper_amount", "total_net", "total_tax", "total_gross", "total_upper", "snapshot_sha256", "template_key", "template_version", "pdf_sha256", "pdf_size", "last_error", "generated_by_name", "generated_at", "created_at", "lines"]
        read_only_fields = ["quote", "version", "status", "total_net", "total_tax", "total_gross", "snapshot_sha256", "template_key", "template_version", "pdf_sha256", "pdf_size", "last_error", "generated_at"]

    def get_total_upper(self, obj):
        return rmb_upper(obj.total_gross)


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
        return {"id": r.id, "version": r.version, "status": r.status, "total_gross": str(r.total_gross), "quote_date": r.quote_date} if r else None

    def get_revisions(self, obj):
        return [{"id": r.id, "version": r.version, "status": r.status, "total_gross": str(r.total_gross), "pdf_sha256": r.pdf_sha256, "generated_at": r.generated_at} for r in obj.revisions.order_by("version")]

    def validate(self, data):
        rc = data.get("recipient_contact")
        rec = data.get("recipient") or (self.instance and self.instance.recipient)
        if rc and rec and rc.account_id != rec.id:
            raise serializers.ValidationError({"recipient_contact": "联系人不属于接收客户"})
        return data


class QuoteViewSet(viewsets.ModelViewSet):
    """报价中心：仅 ADMIN/LEADER/ASSISTANT。销售请求一律 403（不靠前端隐藏）。"""

    serializer_class = QuoteSerializer
    permission_classes = [HasCap.of("quote.access")]
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        qs = Quote.objects.select_related("project", "recipient", "legal_entity", "owner").prefetch_related("revisions")
        p = self.request.query_params
        if q := p.get("q"):
            qs = qs.filter(Q(code__icontains=q) | Q(project__name__icontains=q) | Q(recipient__name__icontains=q) | Q(revisions__lines__model__icontains=q) | Q(revisions__lines__name__icontains=q)).distinct()
        for f in ("project", "recipient", "legal_entity", "status"):
            if v := p.get(f):
                qs = qs.filter(**{f: v})
        if d := p.get("date_from"):
            qs = qs.filter(created_at__date__gte=d)
        if d := p.get("date_to"):
            qs = qs.filter(created_at__date__lte=d)
        return qs

    def perform_create(self, s):
        from apps.common.retry import create_with_code

        def _create(code):
            q = s.save(code=code, owner=self.request.user)
            QuoteRevision.objects.create(quote=q, version=1, quote_date=timezone.localdate(), terms=q.legal_entity.default_terms, created_by=self.request.user)
            return q

        q = create_with_code(services.next_quote_code, _create, serializer=s)
        audit(self.request, "create", "quote", q.id, f"新建报价 {q.code}（{q.legal_entity.short_name} → {q.recipient.name}）")

    def perform_update(self, s):
        q = s.instance
        latest = q.revisions.order_by("-version").first()
        if latest and latest.status != QuoteRevision.DRAFT and any(k in s.validated_data for k in ("project", "recipient", "legal_entity")):
            raise BusinessError("已生成的报价不能更换项目/接收客户/出具公司；请新建报价。", code="revision_frozen")
        q = s.save()
        audit(self.request, "update", "quote", q.id, f"修改报价 {q.code}")

    @action(detail=True, methods=["post"])
    def status(self, request, pk=None):
        q = self.get_object()
        new = request.data.get("status")
        if new not in STATUS_FLOW.get(q.status, set()):
            raise BusinessError(f"不能从「{q.get_status_display()}」变更为「{dict(Quote._meta.get_field('status').choices).get(new, new)}」", code="bad_transition")
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
    return QuoteRevision.objects.select_related("quote").prefetch_related("lines").get(pk=rev_id)


class RevisionViewSet(viewsets.GenericViewSet):
    queryset = QuoteRevision.objects.select_related("quote__legal_entity", "quote__recipient", "quote__project").prefetch_related("lines")
    serializer_class = RevisionSerializer
    permission_classes = [HasCap.of("quote.access")]

    def retrieve(self, request, pk=None):
        return Response(RevisionSerializer(self.get_object()).data)

    def partial_update(self, request, pk=None):
        rev = self.get_object()
        services.ensure_editable(rev)
        s = RevisionSerializer(rev, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        return Response(RevisionSerializer(_fresh(rev.pk)).data)

    @action(detail=True, methods=["post"], url_path="lines")
    def add_line(self, request, pk=None):
        rev = self.get_object()
        services.ensure_editable(rev)
        data = dict(request.data)
        prod = None
        if pid := data.get("product"):
            prod = Product.objects.filter(pk=pid).first()
            if not prod:
                raise BusinessError("产品不存在", code="not_found", http_status=404)
            if not prod.active:
                raise BusinessError("产品已停用，不能加入新报价", code="inactive_product")
            # 从产品库复制，之后与产品库解耦
            defaults = {"model": prod.model, "name": prod.name, "brand": prod.brand, "description": prod.technical_desc, "params": prod.params,
                        "visible_param_keys": [p["key"] for p in prod.params], "unit": prod.unit, "unit_price": str(prod.base_price), "tax_rate": str(prod.tax_rate)}
            for k, v in defaults.items():
                data.setdefault(k, v)
        data.setdefault("qty", "1")
        data.setdefault("sort", QuoteLine.objects.filter(revision=rev).count() + 1)
        s = QuoteLineSerializer(data=data)
        s.is_valid(raise_exception=True)
        ln = s.save(revision=rev)
        services.recalc_line(ln)
        ln.save()
        services.recalc_revision(rev)
        return Response(RevisionSerializer(_fresh(rev.pk)).data, status=201)

    @action(detail=True, methods=["patch", "delete"], url_path=r"lines/(?P<line_id>\d+)")
    def edit_line(self, request, pk=None, line_id=None):
        rev = self.get_object()
        services.ensure_editable(rev)
        ln = rev.lines.filter(pk=line_id).first()
        if not ln:
            raise BusinessError("报价行不存在", code="not_found", http_status=404)
        if request.method == "DELETE":
            ln.delete()
        else:
            s = QuoteLineSerializer(ln, data=request.data, partial=True)
            s.is_valid(raise_exception=True)
            ln = s.save()
            services.recalc_line(ln)
            ln.save()
        services.recalc_revision(rev)
        return Response(RevisionSerializer(_fresh(rev.pk)).data)

    @action(detail=True, methods=["post"])
    def render(self, request, pk=None):
        rev = services.generate_revision(int(pk), request.user)
        audit(request, "generate_pdf", "quote_revision", rev.id, f"生成报价 PDF {rev.code}", {"pdf_sha256": rev.pdf_sha256, "snapshot_sha256": rev.snapshot_sha256})
        return Response(RevisionSerializer(_fresh(rev.pk)).data)

    @action(detail=True, methods=["get"])
    def preview(self, request, pk=None):
        """草稿预览：直接渲染（不存档），用于确认版式。"""
        from .pdf import render_quote_pdf

        rev = self.get_object()
        services.recalc_revision(rev, save=False)
        snap = rev.snapshot if rev.status == QuoteRevision.GENERATED else services.build_snapshot(rev)
        pdf = render_quote_pdf(snap)
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="preview-{rev.id}.pdf"'
        return resp

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        rev = self.get_object()
        if rev.status != QuoteRevision.GENERATED:
            raise BusinessError("该版本尚未生成 PDF", code="not_generated", http_status=404)
        data = services.read_revision_pdf(rev)
        audit(request, "download_pdf", "quote_revision", rev.id, f"下载报价 PDF {rev.code}")
        resp = HttpResponse(data, content_type="application/pdf")
        disp = "inline" if request.query_params.get("inline") == "1" else "attachment"
        from urllib.parse import quote as urlquote

        fname = f"{rev.code}.pdf"
        resp["Content-Disposition"] = f"{disp}; filename=\"{fname}\"; filename*=UTF-8''{urlquote(fname)}"
        resp["X-Content-SHA256"] = rev.pdf_sha256
        return resp


def quote_summary_for(user, project):
    """项目详情中的报价摘要。
    - 商务/负责人/管理员：含金额；
    - 销售：默认不返回（D2 默认值）；若开启 SALES_QUOTE_SUMMARY，只返回明确允许的无金额字段；
    - 财务：不返回。
    """
    qs = project.quotes.select_related("recipient", "legal_entity").prefetch_related("revisions")
    if can(user, "quote.access"):
        return [{"id": q.id, "code": q.code, "recipient_name": q.recipient.name, "legal_entity_name": q.legal_entity.short_name, "status": q.status, "status_label": q.get_status_display(),
                 "latest_version": (r := q.revisions.order_by("-version").first()) and r.version, "total_gross": str(r.total_gross) if r else None, "updated_at": q.updated_at} for q in qs]
    if role_of(user) == SALES and settings.SALES_QUOTE_SUMMARY:
        return [{"id": q.id, "code": q.code, "recipient_name": q.recipient.name, "legal_entity_name": q.legal_entity.short_name, "status_label": q.get_status_display(), "updated_at": q.updated_at} for q in qs]
    return []
