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


def _owner_access(user, owner_type, owner_id, write=False):
    """附件权限跟随所属业务对象。"""
    from apps.projects.scope import scoped_projects

    if owner_type == "project":
        if not scoped_projects(user).filter(pk=owner_id).exists():
            return False
        return can(user, "project.edit") if write else True
    if owner_type == "order":
        return can(user, "order.confirm" if write else "order.access")
    if owner_type == "performance":
        return can(user, "performance.write") if write else can(user, "performance.see_amount")
    if owner_type == "finance":
        return can(user, "project_finance.access")
    return False


class AttachmentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.CharField(source="uploaded_by.name", read_only=True, default="")

    class Meta:
        model = Attachment
        fields = ["id", "owner_type", "owner_id", "filename", "content_type", "size", "sha256", "uploaded_by_name", "uploaded_at"]


class AttachmentListView(APIView):
    def get(self, request):
        ot, oid = request.query_params.get("owner_type"), request.query_params.get("owner_id")
        if not (ot and oid and oid.isdigit()) or not _owner_access(request.user, ot, int(oid)):
            raise PermissionDenied("无权查看该附件列表")
        return Response(AttachmentSerializer(Attachment.objects.filter(owner_type=ot, owner_id=oid), many=True).data)

    def post(self, request):
        ot, oid = request.data.get("owner_type"), str(request.data.get("owner_id", ""))
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
        name = Path(f.name).name[:200]
        att = Attachment.objects.create(owner_type=ot, owner_id=int(oid), filename=name, content_type=MIME[ext], size=len(data),
                                        sha256=hashlib.sha256(data).hexdigest(), storage_key=store_private("attachments", data, ext), uploaded_by=request.user)
        audit(request, "upload", "attachment", att.id, f"上传附件 {name} → {ot}#{oid}")
        return Response(AttachmentSerializer(att).data, status=201)


class AttachmentDownloadView(APIView):
    def get(self, request, pk):
        att = Attachment.objects.filter(pk=pk).first()
        if not att or not _owner_access(request.user, att.owner_type, att.owner_id):
            raise PermissionDenied("无权下载该附件")
        data = read_private(att.storage_key)
        audit(request, "download", "attachment", att.id, f"下载附件 {att.filename}")
        from urllib.parse import quote as urlquote

        resp = HttpResponse(data, content_type=att.content_type)
        resp["Content-Disposition"] = f"attachment; filename=\"file{Path(att.filename).suffix}\"; filename*=UTF-8''{urlquote(att.filename)}"
        return resp
