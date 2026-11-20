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
}


def role_of(user):
    return getattr(user, "role", None) if user and user.is_authenticated else None


def can(user, cap):
    return role_of(user) in CAP[cap]


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
