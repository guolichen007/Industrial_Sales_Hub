"""五类角色与授权策略的唯一来源（V2.1-LAN）。前端菜单隐藏只是体验，所有授权在此执行。

V2.1 变更（见 docs/decisions/V2.1-LAN-决策记录.md）：
- D2：销售无任何报价/价格/合同金额能力。
- D5：财务只维护合同确认状态与执行进度，无金额读写；独立财务台账关闭。
- D7：项目可见范围（visible）与可编辑范围（editable）、业绩范围相互独立。
- 管理员为技术角色：账号、基础配置、诊断；不默认拥有报价、金额、业绩等业务敏感能力。
"""
from django.conf import settings
from rest_framework.permissions import SAFE_METHODS, BasePermission

ADMIN, LEADER, ASSISTANT, SALES, FINANCE = "ADMIN", "LEADER", "ASSISTANT", "SALES", "FINANCE"
ROLE_CHOICES = [
    (ADMIN, "管理员"),
    (LEADER, "部门负责人"),
    (ASSISTANT, "商务助理"),
    (SALES, "销售人员"),
    (FINANCE, "财务人员"),
]
ALL = {ADMIN, LEADER, ASSISTANT, SALES, FINANCE}
BUSINESS = {LEADER, ASSISTANT, SALES, FINANCE}

CAP = {
    # ---- 技术管理 ----
    "user.manage": {ADMIN},
    "system.status": {ADMIN, LEADER},
    "audit.read": {ADMIN, LEADER},
    "legal_entity.write": {ADMIN, LEADER, ASSISTANT},
    "template.signoff": {LEADER},  # RC2：报价模板签收仅部门负责人
    # ---- 主数据 ----
    "product.write": {LEADER, ASSISTANT},
    "product.see_price": {LEADER, ASSISTANT},
    "account.read": BUSINESS,
    "account.write": {LEADER, ASSISTANT, SALES},
    "account.delete": {LEADER},
    "account.category": {LEADER, ASSISTANT},  # RC2：客户自定义类别字典
    "performance.export": {LEADER, ASSISTANT, FINANCE, SALES},  # RC2：按各自可见范围与字段白名单导出内部 Excel
    # ---- 项目 ----
    "project.read": BUSINESS,  # 管理员（技术角色）默认不浏览业务项目
    "project.create": {LEADER, ASSISTANT, SALES},
    "project.edit": {LEADER, ASSISTANT, SALES},  # SALES 仅限 editable_projects
    "project.edit_any": {LEADER, ASSISTANT},  # 可编辑全部项目、指派负责人与成员、设置“成交”
    "project.delete": {LEADER},
    "project.see_scale_note": {LEADER, ASSISTANT},  # 自由文本“预计规模”可能含金额
    "update.presale.write": {LEADER, ASSISTANT, SALES},
    "update.execution.write": {LEADER, ASSISTANT, FINANCE},
    "milestone.init": {LEADER, ASSISTANT},
    "project.handoff": {LEADER, ASSISTANT},  # RC3-G1：中标确认/转交商务（锁定销售写入）
    "project.handoff_revert": {LEADER},  # 撤销中标（误操作或失标）仅负责人，必须填原因
    # ---- 报价（D2：销售/财务/管理员均无） ----
    "quote.access": {LEADER, ASSISTANT},
    # ---- 合同/订单（D5：财务可确认，不可见/写金额） ----
    "order.access": {LEADER, ASSISTANT, FINANCE},
    "order.confirm": {LEADER, ASSISTANT, FINANCE},
    "order.amount": {LEADER, ASSISTANT},
    "order.revoke": {LEADER},
    # ---- 业绩与案例 ----
    "performance.read": BUSINESS,
    "performance.see_all": {LEADER, ASSISTANT, FINANCE},
    "performance.see_amount": {LEADER, ASSISTANT},
    "performance.write": {LEADER, ASSISTANT},
    "performance.approve": {LEADER},
    "case.read": {LEADER, ASSISTANT, SALES},
    "case.generate": {LEADER, ASSISTANT, SALES},  # 服务端白名单，销售不获得内部编辑权
    "case.withdraw": {LEADER},
    # ---- 导入 ----
    "import.run": {LEADER, ASSISTANT},
    # ---- 已关闭能力（D5）：独立财务台账。保留键以便代码明确拒绝 ----
    "project_finance.access": set(),
    # ---- RC3：询价登记（客户来询，不含价格）与业务能力拆分 ----
    "inquiry.read": {LEADER, ASSISTANT},
    "inquiry.write": {LEADER, ASSISTANT},
    "performance.publish": {LEADER, ASSISTANT},  # 修改“允许对外展示/业主名称授权”（公开审批相关），财务不可
    "delivery.write": {LEADER, ASSISTANT},  # 生产/交付状态线
    "payment.write": {LEADER, FINANCE},  # 资金状态线（仅状态枚举，不含金额）
    "stage.config": {LEADER, ASSISTANT},  # 阶段展示名/排序/停用
    "attachment.classify": {LEADER, ASSISTANT},  # 附件类别与“销售可见”授权（财务不获得，即使 BUSINESS 口径）
}

# RC3-G0 业务口径（docs/RC3-业务角色与字段矩阵.md）：财务纳入商务板块。
#   ISH_FINANCE_SCOPE=BUSINESS（默认）：财务可建/改项目资料、登记询价、录入/更新业绩（不含金额与公开授权）、维护交付与资金状态；
#                    RC2：恢复 V2.1 D5（财务只做合同确认与执行节点）。
#   ISH_FINANCE_QUOTE=1：财务可进入报价中心（含单价/金额）。默认 0——“财务是否可见报价价格”需甲方书面确认后再开。
#   任何口径下：销售无报价/价格/合同金额；财务不能审批业绩、不能签收模板、不能看“预计规模”自由文本、不能撤销中标。
FINANCE_BUSINESS_CAPS = {"project.create", "project.edit", "project.edit_any", "inquiry.read", "inquiry.write", "performance.write", "delivery.write", "milestone.init"}
FINANCE_QUOTE_CAPS = {"quote.access", "order.amount", "performance.see_amount"}


def effective_roles(cap):
    roles = set(CAP[cap])
    if getattr(settings, "FINANCE_SCOPE", "BUSINESS") == "BUSINESS" and cap in FINANCE_BUSINESS_CAPS:
        roles.add(FINANCE)
    if getattr(settings, "FINANCE_QUOTE", False) and cap in FINANCE_QUOTE_CAPS:
        roles.add(FINANCE)
    return roles


def effective_caps(role):
    return sorted(k for k in CAP if role in effective_roles(k))


def role_of(user):
    return getattr(user, "role", None) if user and user.is_authenticated else None


def can(user, cap):
    return role_of(user) in effective_roles(cap)


def sales_project_scope_own():
    """D7：销售项目可见范围是否仅本人（ISH_PROJECT_VISIBILITY=OWN）。"""
    return settings.PROJECT_VISIBILITY == "OWN"


class HasCap(BasePermission):
    """用法：permission_classes = [HasCap.of("quote.access")]；可分别指定读/写能力。"""

    read_cap = None
    write_cap = None

    @classmethod
    def of(cls, write_cap, read_cap=None):
        return type("HasCap_" + write_cap.replace(".", "_"), (cls,), {"write_cap": write_cap, "read_cap": read_cap})

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and request.user.is_active):
            return False
        if request.method in SAFE_METHODS:
            cap = self.read_cap or self.write_cap
            return cap == "*" or can(request.user, cap)
        return can(request.user, self.write_cap)
