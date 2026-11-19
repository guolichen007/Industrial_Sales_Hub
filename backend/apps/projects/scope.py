from django.db.models import Q

from apps.common.roles import sales_scoped

from .models import Project


def scoped_projects(user):
    """行级范围：销售默认仅能看到自己负责或作为成员参与的项目（D-可配置）。"""
    qs = Project.objects.all()
    if sales_scoped(user):
        qs = qs.filter(Q(sales_owner=user) | Q(members=user) | Q(created_by=user)).distinct()
    return qs
