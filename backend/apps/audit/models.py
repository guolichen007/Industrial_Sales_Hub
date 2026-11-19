from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """创建/修改/导出/授权/生成 PDF 等操作留痕。只追加，不提供修改接口。"""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    actor_name = models.CharField(max_length=80, blank=True)
    action = models.CharField(max_length=60)
    resource = models.CharField(max_length=60)
    resource_id = models.CharField(max_length=40, blank=True)
    summary = models.CharField(max_length=300, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    ip = models.CharField(max_length=64, blank=True)
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-at", "-id"]
        indexes = [models.Index(fields=["resource", "resource_id"])]
