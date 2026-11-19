from django.conf import settings
from django.db import models
from django.db.models import Q


class OrderConfirmation(models.Model):
    """合同/订单确认记录：业绩归集的唯一受控入口。DRAFT → CONFIRMED → REVOKED"""

    BASIS = [("CONTRACT", "合同"), ("ORDER", "订单"), ("LEADER", "负责人确认")]
    STATUS = [("DRAFT", "待确认"), ("CONFIRMED", "已确认"), ("REVOKED", "已撤销")]

    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="orders")
    quote = models.ForeignKey("quotations.Quote", null=True, blank=True, on_delete=models.PROTECT, related_name="orders")
    legal_entity = models.ForeignKey("organizations.LegalEntity", on_delete=models.PROTECT, related_name="orders")
    customer = models.ForeignKey("organizations.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="orders")
    basis = models.CharField("确认依据", max_length=10, choices=BASIS)
    order_ref = models.CharField("合同/订单号", max_length=60, blank=True)
    signed_on = models.DateField("签订日期", null=True, blank=True)
    contract_amount = models.DecimalField("合同金额(含税)", max_digits=16, decimal_places=2, null=True, blank=True)
    product_summary = models.CharField("供货产品", max_length=200, blank=True)
    reason = models.TextField("确认说明/理由", blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="DRAFT")
    idempotency_key = models.CharField(max_length=64, unique=True, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            # 同一出具主体下，未撤销的合同/订单号不得重复
            models.UniqueConstraint(
                fields=["legal_entity", "order_ref"],
                condition=Q(~Q(status="REVOKED") & ~Q(order_ref="")),
                name="uniq_active_order_ref",
            )
        ]
