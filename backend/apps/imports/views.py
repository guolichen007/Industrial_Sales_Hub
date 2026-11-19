import hashlib

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.service import audit
from apps.common.errors import BusinessError
from apps.common.roles import HasCap

from . import service
from .models import ImportBatch


class TemplateView(APIView):
    permission_classes = [HasCap.of("import.run", read_cap="import.run")]

    def get(self, request, kind):
        if kind not in service.TEMPLATES:
            raise BusinessError("未知模板", code="not_found", http_status=404)
        resp = HttpResponse(service.template_xlsx(kind), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="import-{kind}.xlsx"'
        return resp


class PreviewView(APIView):
    permission_classes = [HasCap.of("import.run")]

    def post(self, request):
        kind = request.data.get("kind")
        f = request.FILES.get("file")
        if kind not in service.TEMPLATES or not f:
            raise BusinessError("请选择导入类型和 .xlsx 文件", code="bad_request", http_status=400)
        if not f.name.lower().endswith(".xlsx"):
            raise BusinessError("仅支持 .xlsx（不接受带宏的 .xlsm / 旧版 .xls）", code="bad_type", http_status=400)
        data = f.read()
        if not data.startswith(b"PK\x03\x04"):
            raise BusinessError("文件内容不是有效的 xlsx", code="bad_signature", http_status=400)
        try:
            rows, summary = service.validate(kind, service.read_rows(kind, data))
        except ValueError as e:
            raise BusinessError(str(e), code="bad_template", http_status=400)
        b = ImportBatch.objects.create(kind=kind, filename=f.name[:200], file_sha256=hashlib.sha256(data).hexdigest(), rows=rows, summary=summary, created_by=request.user)
        audit(request, "import_preview", "import_batch", b.id, f"预检 {kind} {f.name}：{summary}")
        return Response({"id": b.id, "kind": kind, "summary": summary, "rows": rows[:500], "template": service.TEMPLATES[kind]})


class CommitView(APIView):
    permission_classes = [HasCap.of("import.run")]

    def post(self, request, pk):
        with transaction.atomic():
            b = ImportBatch.objects.select_for_update().filter(pk=pk).first()
            if not b:
                raise BusinessError("批次不存在", code="not_found", http_status=404)
            if b.status == "COMMITTED":
                return Response({"id": b.id, "result": b.result, "already": True})
            rows, summary = service.validate(b.kind, [{"row": r["row"], "values": r["values"]} for r in b.rows])  # 提交前重新校验
            b.result = service.commit(b.kind, rows, request.user)
            b.status, b.committed_at = "COMMITTED", timezone.now()
            b.save()
        audit(request, "import_commit", "import_batch", b.id, f"导入 {b.kind}：{b.result}")
        return Response({"id": b.id, "result": b.result})
