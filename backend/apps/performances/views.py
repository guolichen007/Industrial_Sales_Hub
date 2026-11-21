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
from apps.common.params import int_list, int_param, str_param
from apps.common.roles import SALES, HasCap, can, role_of
from apps.projects.scope import performances_for
from apps.organizations.models import LegalEntity

from .models import CasePhoto, CaseRelease, Performance
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
    """安全视图（销售、财务）：不含内部金额、内部备注、合同编号（D2/D5）。"""

    class Meta(PerformanceSerializer.Meta):
        fields = [f for f in PerformanceSerializer.Meta.fields if f not in ("internal_amount", "remark", "order_ref", "order_confirmation")]


def performance_payload(p, user):
    return (PerformanceSerializer if can(user, "performance.see_amount") else PerformanceSafeSerializer)(p).data


class PerformanceViewSet(viewsets.ModelViewSet):
    permission_classes = [HasCap.of("performance.write", read_cap="performance.read")]
    http_method_names = ["get", "post", "patch"]

    def get_serializer_class(self):
        return PerformanceSerializer if can(self.request.user, "performance.see_amount") else PerformanceSafeSerializer

    def get_queryset(self):
        qs = performances_for(self.request.user).select_related("project", "sales_owner", "order_confirmation")
        p = self.request.query_params
        if q := p.get("q"):
            q = str_param(q, "q", 100)
            qs = qs.filter(Q(title__icontains=q) | Q(product_summary__icontains=q) | Q(region__icontains=q) | Q(host_maker__icontains=q) | Q(owner_org__icontains=q) | Q(investor__icontains=q))
        if (y := int_param(p.get("year"), "year")) is not None:
            qs = qs.filter(year=y)
        if (y := int_param(p.get("year_from"), "year_from")) is not None:
            qs = qs.filter(year__gte=y)
        if (y := int_param(p.get("year_to"), "year_to")) is not None:
            qs = qs.filter(year__lte=y)
        for f in ("status", "source", "industry", "machine_type"):
            if v := p.get(f):
                qs = qs.filter(**{f: str_param(v, f, 40)})
        if v := p.get("region"):
            qs = qs.filter(region__icontains=str_param(v, "region", 60))
        if (v := int_param(p.get("sales_owner"), "sales_owner")) is not None:
            qs = qs.filter(sales_owner_id=v)
        if (v := int_param(p.get("project"), "project")) is not None:
            qs = qs.filter(project_id=v)
        if p.get("publishable") == "1":
            qs = qs.filter(publishable=True, status="CONFIRMED")
        return qs

    @action(detail=False, methods=["get"])
    def facets(self, request):
        """筛选下拉的候选值（仅来自本人可见范围）。"""
        qs = self.get_queryset()
        out = {}
        for f in ("industry", "machine_type", "region"):
            out[f] = sorted({v for v in qs.values_list(f, flat=True) if v})[:200]
        out["years"] = sorted({v for v in qs.values_list("year", flat=True)}, reverse=True)
        return Response(out)

    @action(detail=False, methods=["get"], url_path="export-pdf")
    def export_pdf(self, request):
        """RC3-G3 内部销售安全版 PDF：与列表相同的可见范围与筛选（服务端查询决定记录，不接受客户端传入的 ID 列表），
        固定白名单字段（无金额/合同号/内部备注），水印“公司内部资料”；写审计（筛选条件、条数、文件 SHA256）。"""
        from django.utils import timezone as tz

        from apps.common.pdfkit import sha256_bytes

        from .pdf import internal_row, render_internal_performance_pdf

        if not can(request.user, "performance.export"):
            raise PermissionDenied()
        if "ids" in request.query_params or "performance_ids" in request.query_params:
            raise BusinessError("内部业绩 PDF 只按筛选条件导出，不接受指定记录 ID", code="ids_not_allowed", http_status=400)
        qs = self.get_queryset().select_related("sales_owner").order_by("-year", "-id")
        n = qs.count()
        if n > 1000:
            raise BusinessError(f"筛选结果 {n} 条，超过单次导出上限 1000 条，请缩小筛选范围", code="too_many", http_status=400)
        labels = {"q": "关键字", "year": "年份", "year_from": "起始年", "year_to": "截止年", "status": "状态", "source": "来源", "industry": "行业", "machine_type": "主机类型", "region": "地区",
                  "sales_owner": "销售", "project": "项目"}
        filters = {k: str(v)[:40] for k, v in request.query_params.items() if k in labels and v}
        ftext = "；".join(f"{labels[k]}={v}" for k, v in filters.items())
        now = tz.localtime().strftime("%Y-%m-%d %H:%M")
        pdf = render_internal_performance_pdf([internal_row(p) for p in qs], ftext, request.user.name, now)
        digest = sha256_bytes(pdf)
        audit(request, "export_pdf", "performance", "", f"导出内部业绩 PDF {n} 条", {"filters": filters, "rows": n, "pdf_sha256": digest})
        from urllib.parse import quote as urlquote

        resp = HttpResponse(pdf, content_type="application/pdf")
        fname = f"内部业绩-{tz.localdate().isoformat()}.pdf"
        resp["Content-Disposition"] = f"attachment; filename=\"internal-performances.pdf\"; filename*=UTF-8''{urlquote(fname)}"
        resp["X-Content-SHA256"] = digest
        return resp

    @action(detail=False, methods=["get"])
    def export(self, request):
        """内部 Excel 导出（RC2）：与列表相同的筛选与可见范围；列按角色裁剪（只有 see_amount 角色导出金额与内部备注）。写审计。"""
        import io

        from openpyxl import Workbook

        if not can(request.user, "performance.export"):
            raise PermissionDenied()
        qs = self.get_queryset().order_by("-year", "-id")[:5000]
        see_amount = can(request.user, "performance.see_amount")
        cols = [("title", "业绩名称"), ("year", "年份"), ("status_label", "状态"), ("source_label", "来源"), ("region", "地区"), ("industry", "行业"), ("product_summary", "供货产品"),
                ("machine_type", "主机类型"), ("host_maker", "主机厂"), ("owner_org", "业主"), ("investor", "投资方"), ("sales_owner_name", "销售负责人"), ("project_name", "项目"),
                ("publishable", "允许对外展示")]
        if see_amount:
            cols += [("order_ref", "合同/订单号"), ("internal_amount", "内部金额"), ("remark", "内部备注")]
        wb = Workbook()
        ws = wb.active
        ws.title = "业绩台账"
        ws.append([c[1] for c in cols])
        ser = PerformanceSerializer if see_amount else PerformanceSafeSerializer
        n = 0
        for row in ser(qs, many=True).data:
            vals = []
            for k, _ in cols:
                v = row.get(k)
                if k == "internal_amount" and v not in (None, ""):
                    from decimal import Decimal

                    v = Decimal(v)
                elif k == "publishable":
                    v = "是" if v else "否"
                elif isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
                    v = "'" + v  # 防 CSV/公式注入
                vals.append(v)
            ws.append(vals)
            n += 1
        ws.freeze_panes = "A2"
        buf = io.BytesIO()
        wb.save(buf)
        audit(request, "export", "performance", "", f"导出业绩 Excel {n} 条（{'含' if see_amount else '不含'}金额列）", {"filters": {k: v for k, v in request.query_params.items()}, "rows": n})
        from urllib.parse import quote as urlquote

        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        fname = f"业绩台账-{timezone.localdate().isoformat()}.xlsx"
        resp["Content-Disposition"] = f"attachment; filename=\"performances.xlsx\"; filename*=UTF-8''{urlquote(fname)}"
        return resp

    def _guard_fields(self, s):
        """RC3-G0 字段级授权：无金额权限者不得写入金额/内部备注；无公开审批权者不得修改对外展示与业主名称授权。"""
        u = self.request.user
        if not can(u, "performance.see_amount"):
            for f in ("internal_amount", "remark"):
                if f in self.request.data:
                    raise PermissionDenied(f"当前角色不能写入“{f}”（金额/内部备注仅负责人与商务）")
        if not can(u, "performance.publish"):
            for f in ("publishable", "public_name_authorized"):
                if f in self.request.data:
                    raise PermissionDenied("对外展示与业主名称授权属于公开审批，由负责人/商务设置")

    def perform_create(self, s):
        self._guard_fields(s)
        # 手工补录：独立来源 + 待核实，不能绕过订单验证直接成为已确认
        o = s.save(source="MANUAL", status="PENDING", created_by=self.request.user)
        audit(self.request, "create", "performance", o.id, f"手工补录业绩 {o.title}（待核实）")

    def perform_update(self, s):
        self._guard_fields(s)
        inst = Performance.objects.select_for_update().get(pk=s.instance.pk)
        if inst.status == "REVOKED":
            raise BusinessError("已撤销业绩不可修改", code="revoked")
        if inst.source == "ORDER" and "internal_amount" in s.validated_data and s.validated_data["internal_amount"] != inst.internal_amount:
            raise BusinessError("订单生成的业绩金额以合同/订单为准，不能在业绩台账单独修改。", code="order_amount_locked")
        s.instance = inst
        o = s.save()
        audit(self.request, "update", "performance", o.id, f"修改业绩 {o.title}")

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        if not can(request.user, "performance.approve"):
            raise PermissionDenied("仅部门负责人可确认业绩")
        self.get_object()
        o = Performance.objects.select_for_update().get(pk=pk)
        if o.status != "PENDING":
            return Response(self.get_serializer(o).data)
        o.status, o.confirmed_by, o.confirmed_at = "CONFIRMED", request.user, timezone.now()
        o.save()
        audit(request, "approve", "performance", o.id, f"确认业绩 {o.title}")
        return Response(self.get_serializer(o).data)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        if not can(request.user, "performance.approve"):
            raise PermissionDenied("仅部门负责人可撤销业绩")
        self.get_object()
        o = Performance.objects.select_for_update().get(pk=pk)
        reason = str_param(request.data.get("reason"), "reason", 200)
        if not reason:
            raise BusinessError("请填写撤销原因", code="reason_required", http_status=400)
        if o.source == "ORDER" and o.order_confirmation and o.order_confirmation.status == "CONFIRMED":
            raise BusinessError("该业绩来自合同/订单确认，请在“合同/订单确认”中撤销，以保持订单与业绩一致。", code="revoke_via_order")
        if o.status == "REVOKED":
            return Response(self.get_serializer(o).data)
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
        fields = ["id", "title", "subtitle", "legal_entity", "performance_ids", "public_snapshot", "snapshot_sha256", "pdf_sha256", "created_by_name", "created_at", "withdrawn", "withdrawn_reason", "withdrawn_at"]
        read_only_fields = ["public_snapshot", "snapshot_sha256", "pdf_sha256"]


def build_case_snapshot(perfs, title, subtitle, entity):
    return {"schema": 1, "title": title, "subtitle": subtitle, "issuer": entity.name if entity else "", "cases": [public_case(p) for p in perfs]}


def _publishable(ids):
    perfs = {p.id: p for p in Performance.objects.filter(pk__in=ids)}
    bad = [i for i in ids if i not in perfs or perfs[i].status != "CONFIRMED" or not perfs[i].publishable]
    if bad:
        raise BusinessError(f"以下业绩未确认或未标记“允许对外展示”，不能发布：{bad}", code="not_publishable", http_status=400)
    return [perfs[i] for i in ids]


class CasePreviewView(APIView):
    """案例预览：只返回白名单字段；仅“已确认 + 允许对外展示”的业绩（共享资料）。"""

    permission_classes = [HasCap.of("case.generate", read_cap="case.read")]

    def post(self, request):
        ids = int_list(request.data.get("performance_ids") or [], "performance_ids")[:200]
        perfs = {p.id: p for p in Performance.objects.filter(pk__in=ids, status="CONFIRMED", publishable=True)}
        return Response({"cases": [public_case(perfs[i]) for i in ids if i in perfs]})


class PublicCaseListView(APIView):
    """可公开案例候选（白名单字段）：销售也可浏览与检索，不暴露内部业绩字段。"""

    permission_classes = [HasCap.of("case.generate", read_cap="case.read")]

    def get(self, request):
        qs = Performance.objects.filter(status="CONFIRMED", publishable=True).order_by("-year", "-id")
        if q := request.query_params.get("q"):
            q = str_param(q, "q", 100)
            qs = qs.filter(Q(title__icontains=q) | Q(product_summary__icontains=q) | Q(region__icontains=q) | Q(industry__icontains=q) | Q(machine_type__icontains=q))
        if (y := int_param(request.query_params.get("year"), "year")) is not None:
            qs = qs.filter(year=y)
        for f in ("industry", "machine_type", "region"):
            if v := request.query_params.get(f):
                qs = qs.filter(**{f: str_param(v, f, 60)})
        if v := request.query_params.get("product"):
            qs = qs.filter(product_summary__icontains=str_param(v, "product", 60))
        if request.query_params.get("facets") == "1":
            # 常用筛选的候选值：只来自“已确认 + 可公开”的业绩（白名单字段）
            base = Performance.objects.filter(status="CONFIRMED", publishable=True)
            return Response({f: sorted({x for x in base.values_list(f, flat=True) if x})[:200] for f in ("industry", "machine_type", "region")}
                            | {"years": sorted({x for x in base.values_list("year", flat=True)}, reverse=True)})
        return Response([{"id": p.id, **public_case(p)} for p in qs[:500]])


class CaseReleaseViewSet(viewsets.ModelViewSet):
    serializer_class = CaseReleaseSerializer
    permission_classes = [HasCap.of("case.generate", read_cap="case.read")]
    http_method_names = ["get", "post"]

    def get_queryset(self):
        qs = CaseRelease.objects.select_related("created_by")
        if role_of(self.request.user) == SALES:
            qs = qs.filter(withdrawn=False)
        return qs

    def create(self, request, *a, **kw):
        ids = int_list(request.data.get("performance_ids") or [], "performance_ids")
        if not ids:
            raise BusinessError("请至少选择一条业绩", code="empty_case", http_status=400)
        if len(ids) > 200:
            raise BusinessError("单册最多 200 条案例", code="too_many", http_status=400)
        perfs = _publishable(ids)
        title = str_param(request.data.get("title"), "title", 120) or "项目业绩案例"
        subtitle = str_param(request.data.get("subtitle"), "subtitle", 200)
        eid = int_param(request.data.get("legal_entity"), "legal_entity")
        entity = LegalEntity.objects.filter(pk=eid).first() if eid else None
        if eid and not entity:
            raise BusinessError("署名公司不存在", code="not_found", http_status=400)
        snap = build_case_snapshot(perfs, title, subtitle, entity)
        pdf = render_case_pdf(snap)
        cr = CaseRelease.objects.create(title=title, subtitle=subtitle, legal_entity=entity, performance_ids=ids, public_snapshot=snap,
                                        snapshot_sha256=sha256_bytes(canonical_json(snap)), pdf_storage_key=store_private("cases", pdf), pdf_sha256=sha256_bytes(pdf), created_by=request.user)
        audit(request, "publish", "case_release", cr.id, f"生成案例册《{title}》（{len(ids)} 条）", {"pdf_sha256": cr.pdf_sha256})
        return Response(CaseReleaseSerializer(cr).data, status=201)

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        """撤回案例册：之后不可再下载；已经发出的文件无法追回（前端明示）。"""
        if not can(request.user, "case.withdraw"):
            raise PermissionDenied("仅部门负责人可撤回案例册")
        self.get_object()
        cr = CaseRelease.objects.select_for_update().get(pk=pk)
        reason = str_param(request.data.get("reason"), "reason", 200, required=True)
        cr.withdrawn, cr.withdrawn_reason, cr.withdrawn_at = True, reason, timezone.now()
        cr.save(update_fields=["withdrawn", "withdrawn_reason", "withdrawn_at"])
        audit(request, "withdraw", "case_release", cr.id, f"撤回案例册：{reason}")
        return Response(CaseReleaseSerializer(cr).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        cr = self.get_object()
        if cr.withdrawn:
            raise BusinessError("该案例册已撤回，不能再下载。", code="withdrawn", http_status=410)
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


class CasePhotoView(APIView):
    """业绩照片：GET 列表（内部，performance.write）/ POST 上传（performance.write）/ PATCH 授权（仅 LEADER）/ DELETE。"""

    def get(self, request):
        if not can(request.user, "performance.write"):
            raise PermissionDenied()
        pid = int_param(request.query_params.get("performance"), "performance", required=True)
        return Response([_photo_row(x) for x in CasePhoto.objects.filter(performance_id=pid).select_related("authorized_by")])

    def post(self, request):
        import io

        from PIL import Image, ImageOps

        if not can(request.user, "performance.write"):
            raise PermissionDenied()
        perf = Performance.objects.filter(pk=int_param(request.data.get("performance"), "performance", required=True)).first()
        f = request.FILES.get("file")
        if not perf or not f or f.size > 10 * 1024 * 1024:
            raise BusinessError("请选择不超过 10 MB 的 JPG/PNG 照片", code="bad_photo", http_status=400)
        if perf.photos.count() >= 12:
            raise BusinessError("每条业绩最多 12 张照片", code="too_many", http_status=400)
        try:
            im = Image.open(f)
            if im.format not in ("JPEG", "PNG"):
                raise ValueError(im.format)
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((1600, 1600))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=85)  # 重新编码：不写入任何 EXIF/GPS
            data = out.getvalue()
        except Exception:  # noqa: BLE001
            raise BusinessError("图片无法识别，请使用 JPG 或 PNG", code="bad_photo", http_status=400)
        ph = CasePhoto.objects.create(performance=perf, caption=str_param(request.data.get("caption"), "caption", 100) or "", storage_key=store_private("case-photos", data, ".jpg"),
                                      sha256=sha256_bytes(data), width=im.width, height=im.height, uploaded_by=request.user)
        audit(request, "upload", "case_photo", ph.id, f"上传业绩照片（{perf.title}）")
        return Response(_photo_row(ph), status=201)


class CasePhotoDetailView(APIView):
    def patch(self, request, pk):
        ph = CasePhoto.objects.filter(pk=pk).first()
        if not ph:
            raise PermissionDenied()
        if "authorized" in request.data:
            if not can(request.user, "case.withdraw"):  # 部门负责人
                raise PermissionDenied("只有部门负责人可以确认照片已获公开授权")
            auth = request.data.get("authorized") in (True, "true", "1", 1)
            if auth:
                note = str_param(request.data.get("authorization_note"), "authorization_note", 200, required=True)
                ph.authorized, ph.authorized_by, ph.authorized_at, ph.authorization_note = True, request.user, timezone.now(), note
            else:
                ph.authorized, ph.authorized_by, ph.authorized_at = False, None, None
        elif not can(request.user, "performance.write"):
            raise PermissionDenied()
        if "caption" in request.data:
            ph.caption = str_param(request.data.get("caption"), "caption", 100) or ""
        ph.save()
        audit(request, "authorize" if "authorized" in request.data else "update", "case_photo", ph.id, f"照片公开授权={'是' if ph.authorized else '否'}")
        return Response(_photo_row(ph))

    def delete(self, request, pk):
        ph = CasePhoto.objects.filter(pk=pk).first()
        if not ph or not can(request.user, "performance.write"):
            raise PermissionDenied()
        audit(request, "delete", "case_photo", ph.id, "删除业绩照片")
        ph.delete()
        return Response(status=204)


class CasePhotoImageView(APIView):
    """图片内容：内部（performance.write）可看全部；case.read 角色只能看“已授权 + 已确认 + 可公开”业绩的照片。"""

    def get(self, request, pk):
        ph = CasePhoto.objects.select_related("performance").filter(pk=pk).first()
        ok = ph and (can(request.user, "performance.write") or (can(request.user, "case.read") and ph.authorized and ph.performance.status == "CONFIRMED" and ph.performance.publishable))
        if not ok:
            raise PermissionDenied("无权查看该照片")
        data = read_private(ph.storage_key)
        if sha256_bytes(data) != ph.sha256:
            raise BusinessError("文件校验失败", code="integrity", http_status=500)
        return HttpResponse(data, content_type="image/jpeg")


def _photo_row(ph):
    return {"id": ph.id, "performance": ph.performance_id, "caption": ph.caption, "width": ph.width, "height": ph.height, "authorized": ph.authorized,
            "authorized_by_name": ph.authorized_by.name if ph.authorized_by else "", "authorized_at": ph.authorized_at, "authorization_note": ph.authorization_note, "uploaded_at": ph.uploaded_at}
