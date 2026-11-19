from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.service import audit
from apps.common.errors import BusinessError
from apps.common.roles import SALES, HasCap, can, role_of

from .models import MILESTONE_STAGES, ExecutionMilestone, FinanceEntry, Project, ProjectParty, ProjectUpdate
from .scope import scoped_projects


def next_project_code():
    year = timezone.localdate().strftime("%y")
    prefix = f"PRJ-{year}"
    last = Project.objects.filter(code__startswith=prefix).order_by("-code").values_list("code", flat=True).first()
    n = int(last[len(prefix):]) + 1 if last and last[len(prefix):].isdigit() else 1
    return f"{prefix}{n:03d}"


class PartySerializer(serializers.ModelSerializer):
    account_name = serializers.CharField(source="account.name", read_only=True)

    class Meta:
        model = ProjectParty
        fields = ["id", "account", "account_name", "party_role", "is_primary", "note"]


class UpdateSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.name", read_only=True, default="")

    class Meta:
        model = ProjectUpdate
        fields = ["id", "project", "stream", "title", "content", "phase", "contact_name", "next_action", "next_action_date", "happened_at", "created_by_name", "created_at"]
        read_only_fields = ["project"]


class MilestoneSerializer(serializers.ModelSerializer):
    """执行节点安全视图：不含任何金额字段，可给销售。"""

    updated_by_name = serializers.CharField(source="updated_by.name", read_only=True, default="")

    class Meta:
        model = ExecutionMilestone
        fields = ["id", "stage", "sort", "state", "owner_name", "planned_date", "actual_date", "note", "updated_by_name", "updated_at"]
        read_only_fields = ["stage", "sort"]


class FinanceEntrySerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.name", read_only=True, default="")

    class Meta:
        model = FinanceEntry
        fields = ["id", "kind", "amount", "happened_on", "note", "created_by_name", "created_at"]

    def validate_amount(self, v):
        if v < 0:
            raise serializers.ValidationError("金额不能为负")
        return v


class ProjectListSerializer(serializers.ModelSerializer):
    sales_owner_name = serializers.CharField(source="sales_owner.name", read_only=True, default="")
    party_names = serializers.SerializerMethodField()
    quote_count = serializers.IntegerField(read_only=True)
    milestone_done = serializers.IntegerField(read_only=True)
    milestone_total = serializers.IntegerField(read_only=True)

    class Meta:
        model = Project
        fields = ["id", "code", "name", "industry", "machine_type", "region", "location", "investor", "presale_phase", "execution_phase", "sales_owner", "sales_owner_name", "party_names", "quote_count", "milestone_done", "milestone_total", "updated_at"]

    def get_party_names(self, obj):
        return [f"{p.account.name}（{p.party_role}）" for p in obj.parties.all()]


class ProjectSerializer(serializers.ModelSerializer):
    """项目详情：包含参与方、两条进度线、执行节点；**永不**包含报价金额、合同金额与财务字段。"""

    sales_owner_name = serializers.CharField(source="sales_owner.name", read_only=True, default="")
    parties = PartySerializer(many=True, read_only=True)
    milestones = MilestoneSerializer(many=True, read_only=True)
    member_names = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = ["id", "code", "name", "industry", "machine_type", "region", "location", "investor", "description", "presale_phase", "execution_phase", "sales_owner", "sales_owner_name", "members", "member_names", "expected_amount_note", "parties", "milestones", "created_at", "updated_at"]
        read_only_fields = ["code", "execution_phase"]

    def get_member_names(self, obj):
        return [u.name for u in obj.members.all()]


class ProjectViewSet(viewsets.ModelViewSet):
    permission_classes = [HasCap.of("project.edit", read_cap="*")]
    # 这些子资源在方法内部按“售前/执行/财务”分别授权
    SUB_ACTIONS = {"updates", "milestones", "update_milestone", "finance", "related", "parties"}

    def get_permissions(self):
        if self.action in self.SUB_ACTIONS:
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_object(self):
        obj = super().get_object()
        u = self.request.user
        # 销售即便被配置为“可查看全部项目”，写操作仍只限自己负责/参与的项目
        if role_of(u) == SALES and self.request.method not in ("GET", "HEAD", "OPTIONS"):
            if not (obj.sales_owner_id == u.id or obj.created_by_id == u.id or obj.members.filter(pk=u.id).exists()):
                raise PermissionDenied("只能修改自己负责或参与的项目")
        return obj

    def get_queryset(self):
        qs = scoped_projects(self.request.user).select_related("sales_owner").prefetch_related("parties__account", "members", "milestones")
        p = self.request.query_params
        if q := p.get("q"):
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(parties__account__name__icontains=q) | Q(machine_type__icontains=q) | Q(region__icontains=q)).distinct()
        if ph := p.get("presale_phase"):
            qs = qs.filter(presale_phase=ph)
        if ex := p.get("execution_phase"):
            qs = qs.filter(execution_phase=ex)
        if p.get("mine") == "1":
            u = self.request.user
            qs = qs.filter(Q(sales_owner=u) | Q(members=u)).distinct()
        if self.action == "list":
            qs = qs.annotate(
                quote_count=Count("quotes", distinct=True),
                milestone_done=Count("milestones", filter=Q(milestones__state__in=["DONE", "SKIPPED"]), distinct=True),
                milestone_total=Count("milestones", distinct=True),
            )
        return qs

    def get_serializer_class(self):
        return ProjectListSerializer if self.action == "list" else ProjectSerializer

    def perform_create(self, s):
        user = self.request.user
        owner = s.validated_data.get("sales_owner") or (user if role_of(user) == SALES else None)
        from apps.common.retry import create_with_code

        obj = create_with_code(next_project_code, lambda code: s.save(code=code, created_by=user, sales_owner=owner), serializer=s)
        audit(self.request, "create", "project", obj.id, f"新建项目 {obj.code} {obj.name}")

    def perform_update(self, s):
        obj = s.instance
        if role_of(self.request.user) == SALES and "sales_owner" in s.validated_data and s.validated_data["sales_owner"] != obj.sales_owner:
            raise PermissionDenied("销售不能变更项目负责人")
        obj = s.save()
        audit(self.request, "update", "project", obj.id, f"修改项目 {obj.code}", {k: str(v) for k, v in s.validated_data.items() if k != "members"})

    def destroy(self, request, *a, **kw):
        if not can(request.user, "project.delete"):
            raise PermissionDenied("仅负责人/管理员可删除项目")
        obj = self.get_object()
        if obj.quotes.exists() or obj.orders.exists():
            raise BusinessError("项目已有报价或订单，不能删除。", code="protected")
        audit(request, "delete", "project", obj.id, f"删除项目 {obj.code}")
        return super().destroy(request, *a, **kw)

    # ---------- 参与方 ----------
    @action(detail=True, methods=["get", "post"])
    def parties(self, request, pk=None):
        project = self.get_object()
        if request.method == "GET":
            return Response(PartySerializer(project.parties.select_related("account"), many=True).data)
        if not can(request.user, "project.edit"):
            raise PermissionDenied()
        s = PartySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if ProjectParty.objects.filter(project=project, account=s.validated_data["account"], party_role=s.validated_data["party_role"]).exists():
            raise BusinessError("该组织已以相同身份参与本项目。", code="duplicate_party")
        party = s.save(project=project)
        project.save(update_fields=["updated_at"])
        audit(request, "add_party", "project", project.id, f"关联参与方 {party.account.name}（{party.party_role}）")
        return Response(PartySerializer(party).data, status=201)

    @action(detail=True, methods=["delete"], url_path=r"parties/(?P<party_id>\d+)")
    def remove_party(self, request, pk=None, party_id=None):
        project = self.get_object()
        if not can(request.user, "project.edit"):
            raise PermissionDenied()
        party = project.parties.filter(pk=party_id).first()
        if not party:
            raise BusinessError("参与方不存在", code="not_found", http_status=404)
        audit(request, "remove_party", "project", project.id, f"移除参与方 {party.account.name}（{party.party_role}）")
        party.delete()
        return Response(status=204)

    # ---------- 进展日志 ----------
    @action(detail=True, methods=["get", "post"])
    def updates(self, request, pk=None):
        project = self.get_object()
        if request.method == "GET":
            qs = project.updates.select_related("created_by")
            if st := request.query_params.get("stream"):
                qs = qs.filter(stream=st)
            return Response(UpdateSerializer(qs, many=True).data)
        s = UpdateSerializer(data={**request.data, "happened_at": request.data.get("happened_at") or timezone.now()})
        s.is_valid(raise_exception=True)
        stream = s.validated_data["stream"]
        cap = "update.presale.write" if stream == ProjectUpdate.PRESALE else "update.execution.write"
        if not can(request.user, cap):
            raise PermissionDenied("售前跟进由销售/商务录入；合同执行进度由商务/财务更新")
        upd = s.save(project=project, created_by=request.user)
        if stream == ProjectUpdate.PRESALE and s.validated_data.get("phase") in dict(Project._meta.get_field("presale_phase").choices):
            project.presale_phase = s.validated_data["phase"]
        project.save()
        audit(request, "add_update", "project", project.id, f"[{upd.get_stream_display()}] {upd.title}")
        return Response(UpdateSerializer(upd).data, status=201)

    # ---------- 执行节点 ----------
    @action(detail=True, methods=["get", "post"])
    def milestones(self, request, pk=None):
        project = self.get_object()
        if request.method == "POST":
            if not can(request.user, "update.execution.write"):
                raise PermissionDenied("执行节点由商务/财务维护")
            init_milestones(project, request.user)
            audit(request, "init_milestones", "project", project.id, "初始化执行节点")
        return Response(MilestoneSerializer(project.milestones.select_related("updated_by"), many=True).data)

    @action(detail=True, methods=["patch"], url_path=r"milestones/(?P<mid>\d+)")
    def update_milestone(self, request, pk=None, mid=None):
        project = self.get_object()
        if not can(request.user, "update.execution.write"):
            raise PermissionDenied("执行节点由商务/财务维护，销售仅可查看")
        m = project.milestones.filter(pk=mid).first()
        if not m:
            raise BusinessError("节点不存在", code="not_found", http_status=404)
        old = m.get_state_display()
        s = MilestoneSerializer(m, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        m = s.save(updated_by=request.user)
        if m.state == "DONE" and not m.actual_date:
            m.actual_date = timezone.localdate()
            m.save(update_fields=["actual_date"])
        ProjectUpdate.objects.create(project=project, stream=ProjectUpdate.EXECUTION, title=f"{m.stage}：{old} → {m.get_state_display()}", content=m.note, phase=m.stage, happened_at=timezone.now(), created_by=request.user)
        refresh_execution_phase(project)
        audit(request, "update_milestone", "project", project.id, f"执行节点 {m.stage} → {m.get_state_display()}")
        return Response(MilestoneSerializer(m).data)

    # ---------- 财务（独立授权视图） ----------
    @action(detail=True, methods=["get", "post"])
    def finance(self, request, pk=None):
        project = self.get_object()
        if not can(request.user, "project_finance.access"):
            raise PermissionDenied("项目财务信息仅负责人/财务可见")
        if request.method == "POST":
            s = FinanceEntrySerializer(data=request.data)
            s.is_valid(raise_exception=True)
            e = s.save(project=project, created_by=request.user)
            audit(request, "add_finance", "project", project.id, f"登记{e.get_kind_display()}")
        entries = project.finance_entries.select_related("created_by")
        sums = {k: str(sum((e.amount for e in entries if e.kind == k), 0)) for k, _ in FinanceEntry.KINDS}
        return Response({"entries": FinanceEntrySerializer(entries, many=True).data, "sums": sums})

    # ---------- 360° 关联 ----------
    @action(detail=True, methods=["get"])
    def related(self, request, pk=None):
        """项目关联的报价/订单/业绩：按角色裁剪字段，销售不返回任何金额。"""
        from apps.contracts.views import order_payload
        from apps.performances.views import performance_payload
        from apps.quotations.views import quote_summary_for

        project = self.get_object()
        user = request.user
        return Response({
            "quotes": quote_summary_for(user, project),
            "orders": [order_payload(o, user) for o in project.orders.select_related("legal_entity", "customer")] if can(user, "order.access") else [],
            "performances": [performance_payload(p, user) for p in project.performances.all()],
        })


def init_milestones(project, user=None):
    for i, st in enumerate(MILESTONE_STAGES):
        ExecutionMilestone.objects.get_or_create(project=project, stage=st, defaults={"sort": i, "updated_by": user})
    m = project.milestones.filter(stage="订单已确认").first()
    if m and m.state == "PENDING":
        m.state, m.actual_date = "DONE", timezone.localdate()
        m.save()
    refresh_execution_phase(project)


def refresh_execution_phase(project):
    ms = list(project.milestones.all())
    if not ms:
        return
    if project.execution_phase in ("暂停",):
        return
    if all(m.state in ("DONE", "SKIPPED") for m in ms):
        project.execution_phase = "已关闭"
    elif any(m.stage == "验收" and m.state == "DONE" for m in ms):
        project.execution_phase = "已验收"
    else:
        project.execution_phase = "执行中"
    project.save(update_fields=["execution_phase", "updated_at"])
