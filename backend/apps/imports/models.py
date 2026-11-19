from django.conf import settings
from django.db import models


class ImportBatch(models.Model):
    KINDS = [("products", "产品"), ("accounts", "客户"), ("performances", "历史业绩")]
    kind = models.CharField(max_length=20, choices=KINDS)
    filename = models.CharField(max_length=200)
    file_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=12, choices=[("PREVIEWED", "已预检"), ("COMMITTED", "已提交")], default="PREVIEWED")
    rows = models.JSONField(default=list)
    summary = models.JSONField(default=dict)
    result = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
