from django.conf import settings
from django.db import models


class Attachment(models.Model):
    """私有附件：不可通过猜 URL 读取，下载时按所属业务对象重新鉴权。"""

    OWNER_TYPES = [("project", "项目"), ("order", "合同/订单"), ("performance", "业绩"), ("finance", "财务")]
    owner_type = models.CharField(max_length=20, choices=OWNER_TYPES)
    owner_id = models.PositiveBigIntegerField()
    filename = models.CharField(max_length=200)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    storage_key = models.CharField(max_length=200, unique=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["owner_type", "owner_id"])]
