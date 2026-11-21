from django.conf import settings
from django.db import models

PRESALE_PHASES = ["线索发现", "初步沟通", "技术交流", "报价中", "投标中", "结果待定", "失标", "成交"]
EXECUTION_PHASES = ["未启动", "执行中", "暂停", "已验收", "已关闭"]
PARTY_ROLES = ["主机厂", "业主", "投资方", "总包单位", "设计院", "代理商", "贸易商", "其他"]
MILESTONE_STAGES = ["订单已确认", "技术资料确认", "生产/备货", "发货", "到货", "安装/调试", "验收", "完成"]
MILESTONE_STATES = [("PENDING", "未开始"), ("IN_PROGRESS", "进行中"), ("DONE", "已完成"), ("DELAYED", "延期"), ("SKIPPED", "不适用")]


class Project(models.Model):
    """项目独立建档，不依附于客户。"""

    code = models.CharField("项目编号", max_length=30, unique=True)
    name = models.CharField("项目名称", max_length=150)
    industry = models.CharField("行业", max_length=40, blank=True)
    machine_type = models.CharField("主机类型", max_length=40, blank=True)
    region = models.CharField("区域", max_length=60, blank=True)
    location = models.CharField("项目地点", max_length=120, blank=True)
    investor = models.CharField("投资方(文字)", max_length=120, blank=True)
    description = models.TextField("项目说明", blank=True)
    presale_phase = models.CharField("售前阶段", max_length=10, choices=[(p, p) for p in PRESALE_PHASES], default="线索发现")
    execution_phase = models.CharField("执行阶段", max_length=10, choices=[(p, p) for p in EXECUTION_PHASES], default="未启动")
    sales_owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_projects")
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="member_projects")
    expected_amount_note = models.CharField("预计规模(文字)", max_length=60, blank=True)
    # RC3-G1 中标交接：独立的业务受控事件（可早于合同/订单签订）。sales_locked_at 非空 → 销售对该项目的一切售前写入口关闭
    won_at = models.DateTimeField("中标时间", null=True, blank=True)
    won_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    won_note = models.CharField("中标说明", max_length=200, blank=True)
    sales_locked_at = models.DateTimeField("销售转交（锁定）时间", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def sales_locked(self):
        return self.sales_locked_at is not None


class ProjectParty(models.Model):
    """项目参与方（M:N）。同项目同组织同角色去重；同一组织可有多个身份。"""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="parties")
    account = models.ForeignKey("organizations.Account", on_delete=models.PROTECT, related_name="project_parties")
    party_role = models.CharField("参与身份", max_length=10, choices=[(p, p) for p in PARTY_ROLES])
    is_primary = models.BooleanField("主要参与方", default=False)
    note = models.CharField("说明", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["project", "account", "party_role"], name="uniq_project_party_role")]


class ProjectUpdate(models.Model):
    """项目进展日志，分为售前跟进与合同执行两条线，分别授权。"""

    PRESALE, EXECUTION = "PRESALE", "EXECUTION"
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="updates")
    stream = models.CharField(max_length=10, choices=[(PRESALE, "售前跟进"), (EXECUTION, "合同执行")])
    title = models.CharField("标题", max_length=120)
    content = models.TextField("内容", blank=True)
    phase = models.CharField("阶段", max_length=20, blank=True)
    contact_name = models.CharField("联系对象", max_length=60, blank=True)
    next_action = models.CharField("下一步", max_length=200, blank=True)
    next_action_date = models.DateField("下次行动日期", null=True, blank=True)
    happened_at = models.DateTimeField("发生时间")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-happened_at", "-id"]


class ExecutionMilestone(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="milestones")
    stage = models.CharField("节点", max_length=20)
    sort = models.IntegerField(default=0)
    state = models.CharField("状态", max_length=12, choices=MILESTONE_STATES, default="PENDING")
    owner_name = models.CharField("责任人", max_length=40, blank=True)
    planned_date = models.DateField("预计完成", null=True, blank=True)
    actual_date = models.DateField("实际完成", null=True, blank=True)
    note = models.CharField("说明", max_length=300, blank=True)
    delay_reason = models.CharField("延期原因", max_length=200, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort", "id"]
        constraints = [models.UniqueConstraint(fields=["project", "stage"], name="uniq_milestone_stage")]


class FinanceEntry(models.Model):
    """项目财务信息（合同金额、开票、回款）。独立授权视图：销售的项目/进度 API 永不返回。"""

    KINDS = [("CONTRACT", "合同金额"), ("INVOICE", "开票"), ("RECEIPT", "回款")]
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="finance_entries")
    kind = models.CharField(max_length=10, choices=KINDS)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    happened_on = models.DateField()
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-happened_on", "-id"]
