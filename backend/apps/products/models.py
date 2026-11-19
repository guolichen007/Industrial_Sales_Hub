from decimal import Decimal

from django.conf import settings
from django.db import models


class ProductCategory(models.Model):
    name = models.CharField("类别", max_length=40, unique=True)
    sort = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort", "id"]


class ParameterDefinition(models.Model):
    """按产品类别定义的参数字典。"""

    category = models.ForeignKey(ProductCategory, on_delete=models.CASCADE, related_name="parameters")
    key = models.SlugField(max_length=40)
    label = models.CharField(max_length=40)
    unit = models.CharField(max_length=20, blank=True)
    sort = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort", "id"]
        constraints = [models.UniqueConstraint(fields=["category", "key"], name="uniq_param_key")]


class Product(models.Model):
    sku = models.CharField("型号编码", max_length=60, unique=True)
    model = models.CharField("型号", max_length=80)
    name = models.CharField("名称", max_length=100)
    category = models.ForeignKey(ProductCategory, on_delete=models.PROTECT, related_name="products")
    brand = models.CharField("品牌", max_length=40, blank=True)
    drawing_no = models.CharField("图号", max_length=60, blank=True)
    unit = models.CharField("单位", max_length=10, default="台")
    tax_rate = models.DecimalField("税率", max_digits=5, decimal_places=4, default=Decimal("0.13"))
    base_price = models.DecimalField("参考未税单价", max_digits=14, decimal_places=2, default=Decimal("0"))
    technical_desc = models.TextField("技术说明", blank=True)
    # [{"key": "pressure", "label": "工作压力", "value": "25", "unit": "MPa"}]
    params = models.JSONField("参数", default=list, blank=True)
    active = models.BooleanField("启用", default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category__sort", "sku"]
