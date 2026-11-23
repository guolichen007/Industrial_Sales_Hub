from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.roles import can, role_of
from apps.projects.models import ExecutionMilestone, ProjectUpdate
from apps.projects.scope import editable_projects, performances_for, visible_projects


class DashboardView(APIView):
    """按角色裁剪的工作台。销售视图不含任何金额；财务（RC3 D-2）含报价提醒，不含合同/业绩金额。"""

    def get(self, request):
        u = request.user
        if role_of(u) == "ADMIN":
            return Response({"role": "ADMIN", "detail": "管理员请使用系统状态接口 /api/system/status/"})
        projects = visible_projects(u)
        mine = editable_projects(u) if role_of(u) == "SALES" else projects
        today = timezone.localdate()
        data = {
            "role": role_of(u),
            "active_projects": projects.exclude(presale_phase__in=["失标", "成交"]).count(),
            "executing_projects": projects.filter(execution_phase="执行中").count(),
            # 5753b5f 审查 R76-P1-01：已中标/已签约且两条状态线未全部完结（含中标早于合同、尚未建执行节点的项目）
            "tracking_projects": _tracking_count(projects),
            "recent_updates": [
                {"id": x.id, "project_id": x.project_id, "project_name": x.project.name, "stream": x.stream, "title": x.title, "by": x.created_by.name if x.created_by else "", "at": x.happened_at}
                for x in ProjectUpdate.objects.filter(project__in=projects).select_related("project", "created_by")[:8]
            ],
            "todos": [
                {"project_id": x.project_id, "project_name": x.project.name, "next_action": x.next_action, "date": x.next_action_date}
                for x in ProjectUpdate.objects.filter(project__in=mine, next_action_date__isnull=False, next_action_date__lte=today + timedelta(days=14)).exclude(next_action="").select_related("project").order_by("next_action_date")[:8]
            ],
            "milestone_alerts": [
                {"project_id": m.project_id, "project_name": m.project.name, "stage": m.stage, "state": m.get_state_display(), "planned_date": m.planned_date}
                for m in ExecutionMilestone.objects.filter(project__in=projects).filter(Q(state="DELAYED") | Q(planned_date__lt=today, state__in=["PENDING", "IN_PROGRESS"])).select_related("project")[:8]
            ],
        }
        if can(u, "quote.access"):
            from apps.quotations.models import Quote

            from apps.quotations.models import QuoteRevision

            data["quote_count"] = Quote.objects.count()
            data["quotes_open"] = Quote.objects.filter(status__in=["GENERATED", "SENT"]).count()
            data["quotes_draft"] = Quote.objects.filter(revisions__status="DRAFT").distinct().count()
            soon, overdue = [], []
            latest = {}
            for r in QuoteRevision.objects.filter(quote__status__in=["GENERATED", "SENT"]).order_by("quote_id", "version").select_related("quote__project"):
                latest[r.quote_id] = r  # 每份报价只看最新版本
            for r in latest.values():
                if r.status != "GENERATED":
                    continue
                until = r.quote_date + timedelta(days=r.valid_days)
                item = {"quote_id": r.quote_id, "code": r.code, "project_name": r.quote.project.name, "valid_until": until, "trial": r.is_trial}
                if today <= until <= today + timedelta(days=7):
                    soon.append(item)
                elif until < today and r.quote.status == "SENT":
                    overdue.append(item)  # RC2：已过有效期但仍为“已发送”，提示跟进或更新状态
            data["quotes_expiring"] = sorted(soon, key=lambda x: x["valid_until"])[:8]
            data["quotes_overdue"] = sorted(overdue, key=lambda x: x["valid_until"])[:8]
        from apps.performances.models import Performance

        if role_of(u) == "SALES":
            data["my_projects"] = mine.exclude(presale_phase__in=["失标"]).count()
        if can(u, "order.access"):
            from apps.contracts.models import OrderConfirmation

            data["orders_pending"] = OrderConfirmation.objects.filter(status="DRAFT", project__in=projects).count()

        data["performance_confirmed"] = performances_for(u).filter(status="CONFIRMED").count()
        data["performance_pending"] = Performance.objects.filter(status="PENDING").count() if can(u, "performance.approve") else None
        return Response(data)


def _tracking_count(projects):
    from apps.projects.views import list_status_map

    cand = list(projects.filter(Q(sales_locked_at__isnull=False) | Q(orders__status="CONFIRMED")).distinct())
    return sum(1 for v in list_status_map(cand).values() if v["tracking"])
