from django.conf import settings
from django.db import models


class Attachment(models.Model):
    """私有附件：不可通过猜 URL 读取，下载时按所属业务对象重新鉴权。"""

    OWNER_TYPES = [("project", "项目"), ("order", "合同/订单"), ("performance", "业绩"), ("finance", "财务"), ("quote", "报价")]
    # RC2 附件分型：默认 INTERNAL（仅项目编辑范围内的人员）；SALES_VISIBLE 只能由商务/负责人对“交付/进度”附件显式标记，
    # 才向可查看该项目的销售提供列表与下载；PRESALE（销售售前资料）按项目编辑范围受控。报价/合同不得走泛项目附件。
    CLASSES = [("INTERNAL", "内部"), ("PRESALE", "售前资料"), ("DELIVERY", "交付/进度")]
    klass = models.CharField("附件类别", max_length=12, choices=CLASSES, default="INTERNAL")
    sales_visible = models.BooleanField("销售可见（仅交付/进度附件，需商务或负责人授权）", default=False)
    sales_visible_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    sales_visible_at = models.DateTimeField(null=True, blank=True)
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
