from django.conf import settings
from django.db import models


class Performance(models.Model):
    """内部业绩台账。internal_amount 为敏感字段，仅授权序列化器输出。"""

    SOURCES = [("ORDER", "订单确认"), ("MANUAL", "手工补录"), ("IMPORT", "Excel 导入")]
    STATUS = [("PENDING", "待核实"), ("CONFIRMED", "已确认"), ("REVOKED", "已撤销")]

    project = models.ForeignKey("projects.Project", null=True, blank=True, on_delete=models.PROTECT, related_name="performances")
    order_confirmation = models.OneToOneField("contracts.OrderConfirmation", null=True, blank=True, on_delete=models.PROTECT, related_name="performance")
    source = models.CharField(max_length=10, choices=SOURCES)
    status = models.CharField(max_length=10, choices=STATUS, default="PENDING")
    title = models.CharField("业绩名称", max_length=150)
    year = models.PositiveIntegerField("年份")
    region = models.CharField("地区", max_length=60, blank=True)
    industry = models.CharField("行业", max_length=40, blank=True)
    product_summary = models.CharField("供货产品", max_length=200, blank=True)
    machine_type = models.CharField("主机类型", max_length=40, blank=True)
    host_maker = models.CharField("主机厂", max_length=120, blank=True)
    owner_org = models.CharField("业主", max_length=120, blank=True)
    investor = models.CharField("投资方", max_length=120, blank=True)
    sales_owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    internal_amount = models.DecimalField("内部金额", max_digits=16, decimal_places=2, null=True, blank=True)
    public_summary = models.TextField("公开简介", blank=True)
    publishable = models.BooleanField("允许对外展示", default=False)
    public_name_authorized = models.BooleanField("业主名称已获授权公开", default=False)
    remark = models.TextField("内部备注", blank=True)
    import_key = models.CharField(max_length=80, unique=True, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "-id"]


class CasePhoto(models.Model):
    """业绩现场照片（RC2）：上传时用 Pillow 重新编码为 JPEG（去除 EXIF/GPS 等元数据）；
    只有部门负责人勾选“已获公开授权”的照片才会出现在公开案例预览与案例册 PDF 中。"""

    performance = models.ForeignKey(Performance, on_delete=models.CASCADE, related_name="photos")
    caption = models.CharField("图片说明（对外）", max_length=100, blank=True)
    storage_key = models.CharField(max_length=200, unique=True)
    sha256 = models.CharField(max_length=64)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    authorized = models.BooleanField("已获公开授权", default=False)
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    authorized_at = models.DateTimeField(null=True, blank=True)
    authorization_note = models.CharField("授权依据（如业主书面同意）", max_length=200, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class CaseRelease(models.Model):
    """对外案例册。与内部业绩隔离：只存白名单字段快照。"""

    title = models.CharField(max_length=120)
    subtitle = models.CharField(max_length=200, blank=True)
    legal_entity = models.ForeignKey("organizations.LegalEntity", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    performance_ids = models.JSONField(default=list)
    public_snapshot = models.JSONField(default=dict)
    snapshot_sha256 = models.CharField(max_length=64, blank=True)
    pdf_storage_key = models.CharField(max_length=200, blank=True)
    pdf_sha256 = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    withdrawn = models.BooleanField("已撤回", default=False)
    withdrawn_reason = models.CharField(max_length=200, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
