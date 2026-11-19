import re

from django.conf import settings
from django.db import models

PARTY_KINDS = ["主机厂", "业主", "投资方", "总包单位", "设计院", "代理商", "贸易商", "其他"]


def normalize_name(name: str) -> str:
    """名称标准化：去空白、全半角括号统一、去常见后缀，用于重复提示。"""
    s = (name or "").strip().lower()
    s = s.replace("（", "(").replace("）", ")")
    s = re.sub(r"\s+", "", s)
    for suffix in ["股份有限公司", "有限责任公司", "有限公司", "集团公司", "公司"]:
        if s.endswith(suffix) and len(s) > len(suffix) + 1:
            s = s[: -len(suffix)]
            break
    return s


class LegalEntity(models.Model):
    """三家报价出具主体。数据共享，仅模板与抬头区分；报价必须显式绑定主体。"""

    code = models.CharField("代码", max_length=8, unique=True)
    name = models.CharField("公司全称", max_length=120)
    short_name = models.CharField("简称", max_length=40)
    template_key = models.CharField("报价模板", max_length=20, choices=[("A", "模板A·蓝色抬头"), ("B", "模板B·绿色抬头"), ("C", "模板C·灰色简洁")])
    address = models.CharField("地址", max_length=200, blank=True)
    phone = models.CharField("电话", max_length=60, blank=True)
    fax = models.CharField("传真", max_length=60, blank=True)
    email = models.CharField("邮箱", max_length=100, blank=True)
    tax_no = models.CharField("税号", max_length=40, blank=True)
    bank_name = models.CharField("开户行", max_length=120, blank=True)
    bank_account = models.CharField("账号", max_length=60, blank=True)
    default_terms = models.TextField("默认商务条款", blank=True)
    footer_note = models.CharField("页脚说明", max_length=200, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.short_name


class Account(models.Model):
    """客户组织。一个组织可有多个业务身份（主机厂/业主/投资方……）。"""

    name = models.CharField("名称", max_length=150)
    normalized_name = models.CharField(max_length=150, db_index=True, editable=False)
    kinds = models.JSONField("业务身份", default=list, blank=True)
    industry = models.CharField("行业", max_length=40, blank=True)
    region = models.CharField("地区", max_length=60, blank=True)
    address = models.CharField("地址", max_length=200, blank=True)
    remark = models.TextField("备注", blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def save(self, *a, **kw):
        self.normalized_name = normalize_name(self.name)
        super().save(*a, **kw)

    def __str__(self):
        return self.name


class Contact(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField("姓名", max_length=50)
    department = models.CharField("部门", max_length=60, blank=True)
    position = models.CharField("职务", max_length=60, blank=True)
    mobile = models.CharField("手机", max_length=30, blank=True)
    phone = models.CharField("座机", max_length=30, blank=True)
    email = models.CharField("邮箱", max_length=100, blank=True)
    remark = models.CharField("备注", max_length=200, blank=True)

    class Meta:
        ordering = ["department", "id"]
