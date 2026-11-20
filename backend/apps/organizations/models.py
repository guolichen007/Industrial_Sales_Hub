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
    # RC2（模板 2026.11-r3）：三套版式依据三份真实样张的布局设计（公共仓库只含版式代码，不含真实主体资料）
    template_key = models.CharField("报价模板", max_length=20, choices=[("A", "模板A·双语规格表（型号/描述/米数、铜价参考、英文条款）"), ("B", "模板B·中英抬头＋参数子行＋大写"), ("C", "模板C·双语抬头＋部位/点数＋多台总计")])
    # V2.1：每家公司独立的计价口径与模板文字（依据真实样张配置；未签收前为工程默认值）
    price_basis = models.CharField("单价口径", max_length=8, choices=[("NET", "未税单价，逐行计税"), ("GROSS", "含税单价，倒算税额")], default="NET")
    quote_title = models.CharField("报价单标题", max_length=40, default="报　价　单")
    intro_text = models.CharField("开头语", max_length=200, default="感谢贵司的信任与支持，现将相关产品报价如下：")
    show_tax_columns = models.BooleanField("显示税率/税额列", default=True)
    template_verified = models.BooleanField("（已废弃，RC1 勾选）RC2 起以 TemplateSignoff 签收记录为准", default=False)  # 保留列：不做破坏性迁移
    name_en = models.CharField("英文全称", max_length=150, blank=True)
    address_en = models.CharField("英文地址", max_length=250, blank=True)
    website = models.CharField("网址", max_length=100, blank=True)
    currency_label = models.CharField("币种说明", max_length=40, default="人民币 CNY")
    logo_key = models.CharField(max_length=200, blank=True)  # 私有存储中的 Logo（PNG/JPG）；更换时写新文件，旧文件保留供历史核对
    logo_sha256 = models.CharField(max_length=64, blank=True)
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

    # 签收绑定的配置：这些字段任一变化，原签收自动失效，需负责人重新签收
    SIGNOFF_FIELDS = ("template_key", "price_basis", "show_tax_columns", "name", "name_en", "quote_title", "logo_sha256")

    def signoff_config_sha256(self):
        import hashlib
        import json

        return hashlib.sha256(json.dumps({k: getattr(self, k) for k in self.SIGNOFF_FIELDS}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def current_signoff(self, template_version=None):
        """当前模板版本 + 当前配置下有效的签收记录；没有则 None。"""
        if template_version is None:
            from apps.quotations.pdf import TEMPLATE_VERSION as template_version
        return self.signoffs.filter(template_key=self.template_key, template_version=template_version, config_sha256=self.signoff_config_sha256(),
                                    revoked_at__isnull=True).select_related("signed_by").order_by("-signed_at").first()


class TemplateSignoff(models.Model):
    """模板签收（RC2）：仅部门负责人；按“模板 + 模板版本 + 主体关键配置哈希”签收，记录责任人与时间；可撤销，不可修改。"""

    entity = models.ForeignKey(LegalEntity, on_delete=models.CASCADE, related_name="signoffs")
    template_key = models.CharField(max_length=20)
    template_version = models.CharField(max_length=20)
    config_sha256 = models.CharField(max_length=64)
    config = models.JSONField(default=dict)  # 签收时的关键配置（便于追溯“签收的是什么”）
    note = models.CharField("签收说明（核对的样张/黄金用例）", max_length=300)
    signed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    signed_at = models.DateTimeField(auto_now_add=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-signed_at", "-id"]


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
