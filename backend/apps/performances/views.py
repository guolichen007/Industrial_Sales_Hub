from django.db.models import Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.service import audit
from apps.common.errors import BusinessError
from apps.common.pdfkit import canonical_json, read_private, sha256_bytes, store_private
from apps.common.roles import SALES, HasCap, can, role_of, sales_scoped
from apps.organizations.models import LegalEntity

from .models import CaseRelease, Performance
from .pdf import render_case_pdf
from .public import public_case


class PerformanceSerializer(serializers.ModelSerializer):
    """内部完整视图（含金额），仅授权角色。"""

    project_name = serializers.CharField(source="project.name", read_only=True, default="")
    sales_owner_name = serializers.CharField(source="sales_owner.name", read_only=True, default="")
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    source_label = serializers.CharField(source="get_source_display", read_only=True)
    order_ref = serializers.CharField(source="order_confirmation.order_ref", read_only=True, default="")

    class Meta:
        model = Performance
        fields = ["id", "project", "project_name", "order_confirmation", "order_ref", "source", "source_label", "status", "status_label", "title", "year", "region", "industry", "product_summary", "machine_type", "host_maker", "owner_org", "investor", "sales_owner", "sales_owner_name", "internal_amount", "public_summary", "publishable", "public_name_authorized", "remark", "created_at", "confirmed_at"]
        read_only_fields = ["order_confirmation", "source", "status", "confirmed_at"]


class PerformanceSafeSerializer(PerformanceSerializer):
    """销售视图：不含 internal_amount / remark。"""

    class Meta(PerformanceSerializer.Meta):
        fields = [f for f in PerformanceSerializer.Meta.fields if f not in ("internal_amount", "remark")]


def performance_payload(p, user):
    return (PerformanceSerializer if can(user, "performance.see_amount") else PerformanceSafeSerializer)(p).data


class PerformanceViewSet(viewsets.ModelViewSet):
    permission_classes = [HasCap.of("performance.write", read_cap="*")]
    http_method_names = ["get", "post", "patch"]

    def get_serializer_class(self):
        return PerformanceSerializer if can(self.request.user, "performance.see_amount") else PerformanceSafeSerializer

    def get_queryset(self):
        u = self.request.user
        qs = Performance.objects.select_related("project", "sales_owner", "order_confirmation")
        if sales_scoped(u):
            qs = qs.filter(Q(sales_owner=u) | Q(project__sales_owner=u) | Q(project__members=u)).distinct()
        p = self.request.query_params
        if q := p.get("q"):
            qs = qs.filter(Q(title__icontains=q) | Q(product_summary__icontains=q) | Q(region__icontains=q) | Q(host_maker__icontains=q) | Q(owner_org__icontains=q) | Q(investor__icontains=q))
        for f in ("status", "year", "source", "industry", "machine_type"):
            if v := p.get(f):
                qs = qs.filter(**{f: v})
        if p.get("publishable") == "1":
            qs = qs.filter(publishable=True, status="CONFIRMED")
        return qs

    def perform_create(self, s):
        # 手工补录：独立来源 + 待核实，不能绕过订单验证直接成为已确认
        o = s.save(source="MANUAL", status="PENDING", created_by=self.request.user)
        audit(self.request, "create", "performance", o.id, f"手工补录业绩 {o.title}（待核实）")

    def perform_update(self, s):
        if s.instance.status == "REVOKED":
            raise BusinessError("已撤销业绩不可修改", code="revoked")
        o = s.save()
        audit(self.request, "update", "performance", o.id, f"修改业绩 {o.title}")

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        if not can(request.user, "performance.approve"):
            raise PermissionDenied("仅部门负责人/管理员可确认业绩")
        o = self.get_object()
        if o.status != "PENDING":
            return Response(self.get_serializer(o).data)
        o.status, o.confirmed_by, o.confirmed_at = "CONFIRMED", request.user, timezone.now()
        o.save()
        audit(request, "approve", "performance", o.id, f"确认业绩 {o.title}")
        return Response(self.get_serializer(o).data)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        if not can(request.user, "performance.approve"):
            raise PermissionDenied("仅部门负责人/管理员可撤销业绩")
        o = self.get_object()
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            raise BusinessError("请填写撤销原因", code="reason_required", http_status=400)
        o.status = "REVOKED"
        o.remark = (o.remark + f"\n[撤销 {timezone.localdate()}] {reason}").strip()
        o.save()
        audit(request, "revoke", "performance", o.id, f"撤销业绩：{reason}")
        return Response(self.get_serializer(o).data)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        qs = self.get_queryset().filter(status="CONFIRMED")
        by_year = {}
        for p in qs:
            by_year.setdefault(p.year, {"count": 0})
            by_year[p.year]["count"] += 1
        data = {"confirmed": qs.count(), "pending": self.get_queryset().filter(status="PENDING").count(), "by_year": dict(sorted(by_year.items()))}
        if can(request.user, "performance.see_amount"):
            data["amount_total"] = str(qs.aggregate(s=Sum("internal_amount"))["s"] or 0)
            for y, v in data["by_year"].items():
                v["amount"] = str(qs.filter(year=y).aggregate(s=Sum("internal_amount"))["s"] or 0)
        return Response(data)


class CaseReleaseSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.name", read_only=True, default="")

    class Meta:
        model = CaseRelease
        fields = ["id", "title", "subtitle", "legal_entity", "performance_ids", "public_snapshot", "snapshot_sha256", "pdf_sha256", "created_by_name", "created_at"]
        read_only_fields = ["public_snapshot", "snapshot_sha256", "pdf_sha256"]


def build_case_snapshot(perfs, title, subtitle, entity):
    return {"schema": 1, "title": title, "subtitle": subtitle, "issuer": entity.name if entity else "", "cases": [public_case(p) for p in perfs]}


class CasePreviewView(APIView):
    """案例预览：只返回白名单字段。"""

    permission_classes = [HasCap.of("case.write", read_cap="case.read")]

    def post(self, request):
        ids = request.data.get("performance_ids") or []
        perfs = list(Performance.objects.filter(pk__in=ids, status="CONFIRMED", publishable=True))
        return Response({"cases": [public_case(p) for p in sorted(perfs, key=lambda x: ids.index(x.id))]})



class CaseReleaseViewSet(viewsets.ModelViewSet):
    serializer_class = CaseReleaseSerializer
    permission_classes = [HasCap.of("case.write", read_cap="case.read")]
    http_method_names = ["get", "post"]
    queryset = CaseRelease.objects.select_related("created_by")

    def create(self, request, *a, **kw):
        ids = [int(x) for x in (request.data.get("performance_ids") or [])]
        if not ids:
            raise BusinessError("请至少选择一条业绩", code="empty_case", http_status=400)
        perfs = {p.id: p for p in Performance.objects.filter(pk__in=ids)}
        bad = [i for i in ids if i not in perfs or perfs[i].status != "CONFIRMED" or not perfs[i].publishable]
        if bad:
            raise BusinessError(f"以下业绩未确认或未标记“允许对外展示”，不能发布：{bad}", code="not_publishable", http_status=400)
        title = str(request.data.get("title") or "项目业绩案例").strip()[:120]
        subtitle = str(request.data.get("subtitle") or "").strip()[:200]
        entity = LegalEntity.objects.filter(pk=request.data.get("legal_entity")).first() if request.data.get("legal_entity") else None
        snap = build_case_snapshot([perfs[i] for i in ids], title, subtitle, entity)
        pdf = render_case_pdf(snap)
        cr = CaseRelease.objects.create(title=title, subtitle=subtitle, legal_entity=entity, performance_ids=ids, public_snapshot=snap,
                                        snapshot_sha256=sha256_bytes(canonical_json(snap)), pdf_storage_key=store_private("cases", pdf), pdf_sha256=sha256_bytes(pdf), created_by=request.user)
        audit(request, "publish", "case_release", cr.id, f"生成案例册《{title}》（{len(ids)} 条）", {"pdf_sha256": cr.pdf_sha256})
        return Response(CaseReleaseSerializer(cr).data, status=201)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        cr = self.get_object()
        data = read_private(cr.pdf_storage_key)
        if sha256_bytes(data) != cr.pdf_sha256:
            raise BusinessError("文件校验失败", code="pdf_integrity", http_status=500)
        audit(request, "download_pdf", "case_release", cr.id, f"下载案例册 {cr.title}")
        from urllib.parse import quote as urlquote

        resp = HttpResponse(data, content_type="application/pdf")
        fname = f"案例册-{cr.id}.pdf"
        disp = "inline" if request.query_params.get("inline") == "1" else "attachment"
        resp["Content-Disposition"] = f"{disp}; filename=\"case-{cr.id}.pdf\"; filename*=UTF-8''{urlquote(fname)}"
        return resp
