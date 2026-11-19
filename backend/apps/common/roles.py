"""五类角色与授权策略的唯一来源。前端菜单隐藏仅是体验，所有授权在此执行。"""
from django.conf import settings
from rest_framework.permissions import BasePermission, SAFE_METHODS

ADMIN, LEADER, ASSISTANT, SALES, FINANCE = "ADMIN", "LEADER", "ASSISTANT", "SALES", "FINANCE"
ROLE_CHOICES = [
    (ADMIN, "管理员"),
    (LEADER, "部门负责人"),
    (ASSISTANT, "商务助理"),
    (SALES, "销售人员"),
    (FINANCE, "财务人员"),
]
ALL = {ADMIN, LEADER, ASSISTANT, SALES, FINANCE}

# 能力 → 角色（与 PRD 第 3 节权限矩阵一致）
CAP = {
    "user.manage": {ADMIN},
    "legal_entity.write": {ADMIN, LEADER, ASSISTANT},
    "product.write": {ADMIN, LEADER, ASSISTANT},
    "product.see_price": {ADMIN, LEADER, ASSISTANT},
    "account.write": {ADMIN, LEADER, ASSISTANT, SALES},
    "account.delete": {ADMIN, LEADER},
    "project.create": {ADMIN, LEADER, ASSISTANT, SALES},
    "project.edit": {ADMIN, LEADER, ASSISTANT, SALES},  # SALES 受行级范围限制
    "project.delete": {ADMIN, LEADER},
    "update.presale.write": {ADMIN, LEADER, ASSISTANT, SALES},
    "update.execution.write": {ADMIN, LEADER, ASSISTANT, FINANCE},
    "project_finance.access": {ADMIN, LEADER, FINANCE},
    "quote.access": {ADMIN, LEADER, ASSISTANT},
    "order.access": {ADMIN, LEADER, ASSISTANT, FINANCE},
    "order.confirm": {ADMIN, LEADER, ASSISTANT, FINANCE},
    "order.revoke": {ADMIN, LEADER},
    "performance.see_amount": {ADMIN, LEADER, ASSISTANT, FINANCE},
    "performance.write": {ADMIN, LEADER, ASSISTANT},
    "performance.approve": {ADMIN, LEADER},
    "case.read": {ADMIN, LEADER, ASSISTANT, SALES},
    "case.write": {ADMIN, LEADER, ASSISTANT},
    "import.run": {ADMIN, LEADER, ASSISTANT},
    "audit.read": {ADMIN, LEADER},
}


def role_of(user):
    return getattr(user, "role", None) if user and user.is_authenticated else None


def can(user, cap):
    return role_of(user) in CAP[cap]


def sales_scoped(user):
    """销售是否受“自己/参与项目”行级范围限制。"""
    return role_of(user) == SALES and not settings.SALES_SEE_ALL_PROJECTS


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
