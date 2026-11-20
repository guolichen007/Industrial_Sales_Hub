"""私有附件：扩展名白名单 + 文件签名校验 + 大小上限 + 下载时按所属对象鉴权。"""
import hashlib
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.service import audit
from apps.common.errors import BusinessError
from apps.common.pdfkit import read_private, store_private
from apps.common.params import int_param
from apps.common.roles import can

from .models import Attachment

ALLOWED = {
    ".pdf": (b"%PDF",),
    ".png": (b"\x89PNG",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".xlsx": (b"PK\x03\x04",),
    ".docx": (b"PK\x03\x04",),
    ".zip": (b"PK\x03\x04",),
}
MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".zip": "application/zip"}


def _owner_exists(owner_type, owner_id):
    from apps.contracts.models import OrderConfirmation
    from apps.performances.models import Performance
    from apps.projects.models import Project
    from apps.quotations.models import Quote

    model = {"project": Project, "order": OrderConfirmation, "performance": Performance, "quote": Quote}.get(owner_type)
    return bool(model and model.objects.filter(pk=owner_id).exists())


def _owner_access(user, owner_type, owner_id, write=False):
    """附件权限跟随所属业务对象的“编辑范围/敏感级别”，而不是项目的公共可见范围（D7）。

    - project：仅可编辑该项目的人（负责人/商务，或销售本人负责/参与的项目）可读写；
      其他人能看到项目公共进度，但看不到项目附件（附件可能含报价、图纸等）。
    - order：合同/订单证据。读写需 order.access / order.confirm，且项目在可见范围内。
    - performance：业绩证据。读写需 performance.write（负责人/商务）。
    - finance：独立财务台账本期关闭（D5），一律拒绝。
    """
    from apps.projects.models import Project
    from apps.projects.scope import is_project_editable, visible_projects

    if not _owner_exists(owner_type, owner_id):
        return False
    if owner_type == "project":
        p = Project.objects.get(pk=owner_id)
        return can(user, "project.edit") and is_project_editable(user, p)
    if owner_type == "order":
        from apps.contracts.models import OrderConfirmation

        o = OrderConfirmation.objects.get(pk=owner_id)
        if not visible_projects(user).filter(pk=o.project_id).exists():
            return False
        # 合同/订单扫描件可能含金额（D5）：财务可上传确认依据，但查看/下载限负责人与商务
        return can(user, "order.confirm") if write else can(user, "order.amount")
    if owner_type == "performance":
        return can(user, "performance.write")
    if owner_type == "quote":
        # 报价附件（技术方案、客户询价单等）：只与报价权限一致（D2：销售/财务/管理员一律无）
        return can(user, "quote.access")
    return False


def _sales_visible_ok(user, att):
    """销售对“非本人项目”只能看到显式授权为销售可见的交付/进度附件，且项目在其可见范围内。"""
    from apps.projects.scope import visible_projects

    return (att.owner_type == "project" and att.klass == "DELIVERY" and att.sales_visible and can(user, "project.read")
            and visible_projects(user).filter(pk=att.owner_id).exists())


def can_read_attachment(user, att):
    return _owner_access(user, att.owner_type, att.owner_id) or _sales_visible_ok(user, att)


class AttachmentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.CharField(source="uploaded_by.name", read_only=True, default="")

    class Meta:
        model = Attachment
        fields = ["id", "owner_type", "owner_id", "filename", "content_type", "size", "sha256", "klass", "sales_visible", "uploaded_by_name", "uploaded_at"]


class AttachmentListView(APIView):
    def get(self, request):
        ot = request.query_params.get("owner_type")
        oid = int_param(request.query_params.get("owner_id"), "owner_id", required=True)
        qs = Attachment.objects.filter(owner_type=ot, owner_id=oid)
        if ot and _owner_access(request.user, ot, oid):
            return Response(AttachmentSerializer(qs, many=True).data)
        # 无编辑权的销售：只列出该项目中显式授权“销售可见”的交付/进度附件
        if ot == "project" and can(request.user, "project.read"):
            from apps.projects.scope import visible_projects

            if visible_projects(request.user).filter(pk=oid).exists():
                return Response(AttachmentSerializer(qs.filter(klass="DELIVERY", sales_visible=True), many=True).data)
        raise PermissionDenied("无权查看该附件列表")

    def post(self, request):
        ot = str(request.data.get("owner_type", ""))
        oid = str(int_param(request.data.get("owner_id"), "owner_id", required=True))
        f = request.FILES.get("file")
        if not f:
            raise BusinessError("请选择文件", code="no_file", http_status=400)
        if not (ot and oid.isdigit()) or not _owner_access(request.user, ot, int(oid), write=True):
            raise PermissionDenied("无权上传到该对象")
        ext = Path(f.name).suffix.lower()
        if ext not in ALLOWED:
            raise BusinessError(f"不支持的文件类型 {ext}；允许：{'、'.join(ALLOWED)}", code="bad_type", http_status=400)
        if f.size > settings.ATTACHMENT_MAX_BYTES:
            raise BusinessError(f"文件超过 {settings.ATTACHMENT_MAX_BYTES // 1024 // 1024} MB 上限", code="too_large", http_status=400)
        data = f.read()
        if not any(data.startswith(sig) for sig in ALLOWED[ext]):
            raise BusinessError("文件内容与扩展名不符", code="bad_signature", http_status=400)
        name = Path(f.name).name.replace("\\", "_")[:200]
        klass = str(request.data.get("klass") or ("PRESALE" if request.user.role == "SALES" else "INTERNAL"))
        if klass not in dict(Attachment.CLASSES) or ot != "project" and klass != "INTERNAL":
            raise BusinessError("附件类别无效（只有项目附件可选择售前资料/交付进度）", code="bad_class", http_status=400)
        if request.user.role == "SALES" and klass != "PRESALE":
            klass = "PRESALE"  # 销售上传的一律为受控的售前资料
        att = Attachment.objects.create(owner_type=ot, owner_id=int(oid), filename=name, content_type=MIME[ext], size=len(data), klass=klass,
                                        sha256=hashlib.sha256(data).hexdigest(), storage_key=store_private("attachments", data, ext), uploaded_by=request.user)
        audit(request, "upload", "attachment", att.id, f"上传附件 {name} → {ot}#{oid}")
        return Response(AttachmentSerializer(att).data, status=201)


class AttachmentDownloadView(APIView):
    def get(self, request, pk):
        att = Attachment.objects.filter(pk=pk).first()
        if not att or not can_read_attachment(request.user, att):
            raise PermissionDenied("无权下载该附件")
        data = read_private(att.storage_key)
        audit(request, "download", "attachment", att.id, f"下载附件 {att.filename}")
        from urllib.parse import quote as urlquote

        resp = HttpResponse(data, content_type=att.content_type)
        resp["Content-Disposition"] = f"attachment; filename=\"file{Path(att.filename).suffix}\"; filename*=UTF-8''{urlquote(att.filename)}"
        return resp


class AttachmentDetailView(APIView):
    """PATCH：商务/负责人设置附件类别与“销售可见”（仅交付/进度类可授权，可撤回）；DELETE：上传人或负责人删除（写审计）。"""

    def patch(self, request, pk):
        from django.utils import timezone

        att = Attachment.objects.filter(pk=pk).first()
        if not att or not _owner_access(request.user, att.owner_type, att.owner_id, write=True):
            raise PermissionDenied("无权修改该附件")
        klass = request.data.get("klass", att.klass)
        sv = request.data.get("sales_visible", att.sales_visible)
        sv = sv in (True, "true", "1", 1)
        if klass not in dict(Attachment.CLASSES) or (att.owner_type != "project" and klass != "INTERNAL"):
            raise BusinessError("附件类别无效", code="bad_class", http_status=400)
        if (sv or klass != att.klass) and not can(request.user, "project.edit_any"):
            raise PermissionDenied("只有商务助理或部门负责人可以设置附件类别与销售可见")
        if sv and klass != "DELIVERY":
            raise BusinessError("只有“交付/进度”类附件可以授权销售可见；合同、报价、内部资料不得对销售开放", code="not_delivery")
        changed = sv != att.sales_visible
        att.klass, att.sales_visible = klass, sv
        if changed:
            att.sales_visible_by, att.sales_visible_at = (request.user, timezone.now()) if sv else (None, None)
        att.save()
        audit(request, "classify", "attachment", att.id, f"附件 {att.filename}：类别 {att.get_klass_display()}，销售可见={'是' if sv else '否'}")
        return Response(AttachmentSerializer(att).data)

    def delete(self, request, pk):
        att = Attachment.objects.filter(pk=pk).first()
        if not att or not _owner_access(request.user, att.owner_type, att.owner_id, write=True):
            raise PermissionDenied("无权删除该附件")
        if att.uploaded_by_id != request.user.id and request.user.role != "LEADER":
            raise PermissionDenied("只能删除自己上传的附件（负责人可删除任意附件）")
        audit(request, "delete", "attachment", att.id, f"删除附件 {att.filename}", {"sha256": att.sha256, "owner": f"{att.owner_type}#{att.owner_id}"})
        att.delete()  # 文件本体保留在私有目录（备份/审计可追溯），不再可通过系统访问
        return Response(status=204)
