from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.roles import can, role_of
from apps.projects.models import ExecutionMilestone, ProjectUpdate
from apps.projects.scope import scoped_projects


class DashboardView(APIView):
    """按角色裁剪的工作台。销售/财务视图不包含报价金额。"""

    def get(self, request):
        u = request.user
        projects = scoped_projects(u)
        today = timezone.localdate()
        data = {
            "role": role_of(u),
            "active_projects": projects.exclude(presale_phase__in=["失标", "成交"]).count(),
            "executing_projects": projects.filter(execution_phase="执行中").count(),
            "recent_updates": [
                {"id": x.id, "project_id": x.project_id, "project_name": x.project.name, "stream": x.stream, "title": x.title, "by": x.created_by.name if x.created_by else "", "at": x.happened_at}
                for x in ProjectUpdate.objects.filter(project__in=projects).select_related("project", "created_by")[:8]
            ],
            "todos": [
                {"project_id": x.project_id, "project_name": x.project.name, "next_action": x.next_action, "date": x.next_action_date}
                for x in ProjectUpdate.objects.filter(project__in=projects, next_action_date__isnull=False, next_action_date__lte=today + timedelta(days=14)).exclude(next_action="").select_related("project").order_by("next_action_date")[:8]
            ],
            "milestone_alerts": [
                {"project_id": m.project_id, "project_name": m.project.name, "stage": m.stage, "state": m.get_state_display(), "planned_date": m.planned_date}
                for m in ExecutionMilestone.objects.filter(project__in=projects).filter(Q(state="DELAYED") | Q(planned_date__lt=today, state__in=["PENDING", "IN_PROGRESS"])).select_related("project")[:8]
            ],
        }
        if can(u, "quote.access"):
            from apps.quotations.models import Quote

            data["quote_count"] = Quote.objects.count()
            data["quotes_open"] = Quote.objects.filter(status__in=["GENERATED", "SENT"]).count()
        from apps.performances.models import Performance

        perf = Performance.objects.filter(status="CONFIRMED")
        if role_of(u) == "SALES":
            perf = perf.filter(Q(sales_owner=u) | Q(project__in=projects)).distinct()
        data["performance_confirmed"] = perf.count()
        data["performance_pending"] = Performance.objects.filter(status="PENDING").count() if can(u, "performance.approve") else None
        return Response(data)
