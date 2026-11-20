from decimal import Decimal

from django.conf import settings
from django.db import models

QUOTE_STATUSES = [
    ("DRAFT", "草稿"),
    ("GENERATED", "已生成"),
    ("SENT", "已发送"),
    ("ACCEPTED", "已接受"),
    ("LOST", "已失标"),
    ("CANCELLED", "已取消"),
]


class Quote(models.Model):
    """报价单（业务容器）。价格、参数、条款全部在版本中，且生成后冻结。"""

    code = models.CharField("报价编号", max_length=30, unique=True)
    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="quotes")
    recipient = models.ForeignKey("organizations.Account", on_delete=models.PROTECT, related_name="received_quotes")
    recipient_contact = models.ForeignKey("organizations.Contact", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    legal_entity = models.ForeignKey("organizations.LegalEntity", on_delete=models.PROTECT, related_name="quotes")
    title = models.CharField("报价标题", max_length=150, blank=True)
    status = models.CharField(max_length=10, choices=QUOTE_STATUSES, default="DRAFT")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]


class QuoteRevision(models.Model):
    DRAFT, GENERATED = "DRAFT", "GENERATED"
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="revisions")
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=[(DRAFT, "草稿"), (GENERATED, "已生成")], default=DRAFT)
    quote_date = models.DateField("报价日期")
    valid_days = models.PositiveIntegerField("有效期(天)", default=30)
    delivery_terms = models.CharField("交货期", max_length=200, blank=True)
    payment_terms = models.CharField("付款方式", max_length=200, blank=True)
    terms = models.TextField("商务条款", blank=True)
    remark = models.TextField("备注", blank=True)
    show_upper_amount = models.BooleanField("显示大写金额", default=True)
    # 参考价（铜价/汇率等）：仅手工录入并印在报价单上，不参与自动调价（D4）
    ref_copper_price = models.CharField("参考铜价", max_length=60, blank=True)
    ref_exchange_rate = models.CharField("参考汇率", max_length=60, blank=True)
    ref_valid_until = models.DateField("参考价有效期至", null=True, blank=True)
    ref_note = models.CharField("参考价说明", max_length=200, blank=True)
    price_basis = models.CharField(max_length=8, default="NET")  # 创建时取自出具主体，生成时冻结
    # RC2（模板 r3）：整单乘数（例如“2 台斗轮”：明细为单台/单套，合计 × 台数 = 总计）、质保期、试用标记
    host_qty = models.PositiveIntegerField("整单台数", default=1)
    host_label = models.CharField("台数说明", max_length=20, blank=True)  # 例如“台斗轮”→ 打印“2台斗轮总计”
    warranty = models.CharField("质保期", max_length=100, blank=True)
    is_trial = models.BooleanField("试用报价（模板未签收）", default=False)
    lock_version = models.PositiveIntegerField(default=0)  # 草稿乐观并发版本号
    total_net = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    total_tax = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    total_gross = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    # 冻结信息
    snapshot = models.JSONField(null=True, blank=True)
    snapshot_sha256 = models.CharField(max_length=64, blank=True)
    template_key = models.CharField(max_length=10, blank=True)
    template_version = models.CharField(max_length=20, blank=True)
    pdf_storage_key = models.CharField(max_length=200, blank=True)
    pdf_sha256 = models.CharField(max_length=64, blank=True)
    pdf_size = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    generated_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["quote_id", "version"]
        constraints = [models.UniqueConstraint(fields=["quote", "version"], name="uniq_quote_version")]

    @property
    def code(self):
        return f"{self.quote.code}-V{self.version}"


class QuoteLine(models.Model):
    """报价行。产品信息在加入时复制，之后与产品库解耦。"""

    revision = models.ForeignKey(QuoteRevision, on_delete=models.CASCADE, related_name="lines")
    sort = models.IntegerField(default=0)
    product = models.ForeignKey("products.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    model = models.CharField("型号", max_length=80)
    name = models.CharField("名称", max_length=100)
    brand = models.CharField("品牌", max_length=40, blank=True)
    description = models.TextField("技术说明", blank=True)
    params = models.JSONField(default=list, blank=True)  # [{key,label,value,unit}]
    visible_param_keys = models.JSONField(default=list, blank=True)
    unit = models.CharField(max_length=10, default="台")
    qty = models.DecimalField(max_digits=14, decimal_places=3)
    multiplier = models.DecimalField("乘数", max_digits=12, decimal_places=3, default=Decimal("1"))  # 原样中明确的倍数（如每台米数）
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.13"))
    discount_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0"))
    line_net = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    line_tax = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    line_gross = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal("0"))
    remark = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["sort", "id"]
