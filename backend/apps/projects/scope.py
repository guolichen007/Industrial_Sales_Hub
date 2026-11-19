"""项目与业绩的行级范围。三者相互独立（D7）：

- visible_projects：可以查看项目公共信息与执行进度的范围；
- editable_projects：可以修改项目、关联参与方、写售前跟进、上传项目附件的范围；
- performances_for：可以查看的业绩范围（不随项目可见范围扩大）。
"""
from django.db.models import Q

from apps.common.roles import ADMIN, SALES, can, role_of, sales_project_scope_own  # noqa: F401

from .models import Project


def _own_q(user, prefix=""):
    return Q(**{f"{prefix}sales_owner": user}) | Q(**{f"{prefix}members": user}) | Q(**{f"{prefix}created_by": user})


def visible_projects(user):
    qs = Project.objects.all()
    role = role_of(user)
    if role is None or role == ADMIN:
        return qs.none()
    if role == SALES and sales_project_scope_own():
        qs = qs.filter(_own_q(user)).distinct()
    return qs


def editable_projects(user):
    qs = Project.objects.all()
    if can(user, "project.edit_any"):
        return qs
    if role_of(user) == SALES:
        return qs.filter(_own_q(user)).distinct()
    return qs.none()


def is_project_editable(user, project):
    if can(user, "project.edit_any"):
        return True
    if role_of(user) == SALES:
        return project.sales_owner_id == user.id or project.created_by_id == user.id or project.members.filter(pk=user.id).exists()
    return False


def performances_for(user):
    from apps.performances.models import Performance

    qs = Performance.objects.all()
    if can(user, "performance.see_all"):
        return qs
    if role_of(user) == SALES:
        return qs.filter(Q(sales_owner=user) | Q(project__sales_owner=user) | Q(project__members=user)).distinct()
    return qs.none()


# 兼容旧调用：scoped_projects == visible_projects
scoped_projects = visible_projects
__all__ = ["visible_projects", "editable_projects", "is_project_editable", "performances_for", "scoped_projects", "ADMIN"]
