from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.service import audit
from apps.common.errors import BusinessError
from apps.common.params import int_param, str_param
from apps.common.roles import SALES, HasCap, can, role_of

from .models import EXECUTION_PHASES, MILESTONE_STAGES, PRESALE_PHASES, ExecutionMilestone, Project, ProjectParty, ProjectUpdate
from .scope import editable_projects, is_project_editable, visible_projects

# 销售不能直接设置的售前阶段：“成交”只能由合同/订单确认产生
SALES_FORBIDDEN_PHASES = {"成交"}


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
        fields = ["id", "stage", "sort", "state", "owner_name", "planned_date", "actual_date", "note", "delay_reason", "updated_by_name", "updated_at"]
        read_only_fields = ["stage", "sort"]

    def validate(self, data):
        state = data.get("state", self.instance.state if self.instance else "PENDING")
        planned = data.get("planned_date", self.instance.planned_date if self.instance else None)
        actual = data.get("actual_date", self.instance.actual_date if self.instance else None)
        reason = data.get("delay_reason", self.instance.delay_reason if self.instance else "")
        if state == "DELAYED" and not (reason or "").strip():
            raise serializers.ValidationError({"delay_reason": "标记延期时必须填写延期原因"})
        if actual and state in ("PENDING", "IN_PROGRESS"):
            raise serializers.ValidationError({"actual_date": "未完成的节点不能填写实际完成日"})
        if actual and actual > timezone.localdate():
            raise serializers.ValidationError({"actual_date": "实际完成日不能晚于今天"})
        if planned and actual and planned.year < 2000:
            raise serializers.ValidationError({"planned_date": "日期无效"})
        return data


class ProjectListSerializer(serializers.ModelSerializer):
    """列表：所有角色相同字段，不含报价数量/金额/预计规模（D2）。"""

    sales_owner_name = serializers.CharField(source="sales_owner.name", read_only=True, default="")
    party_names = serializers.SerializerMethodField()
    milestone_done = serializers.IntegerField(read_only=True)
    milestone_total = serializers.IntegerField(read_only=True)
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = ["id", "code", "name", "industry", "machine_type", "region", "location", "investor", "presale_phase", "execution_phase", "sales_owner", "sales_owner_name", "party_names", "milestone_done", "milestone_total", "can_edit", "updated_at"]

    def get_party_names(self, obj):
        return [f"{p.account.name}（{p.party_role}）" for p in obj.parties.all()]

    def get_can_edit(self, obj):
        return is_project_editable(self.context["request"].user, obj)


class ProjectSerializer(serializers.ModelSerializer):
    """项目详情：参与方、两条进度线、执行节点；**永不**包含报价金额、合同金额与财务字段。"""

    sales_owner_name = serializers.CharField(source="sales_owner.name", read_only=True, default="")
    parties = PartySerializer(many=True, read_only=True)
    milestones = MilestoneSerializer(many=True, read_only=True)
    member_names = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = ["id", "code", "name", "industry", "machine_type", "region", "location", "investor", "description", "presale_phase", "execution_phase", "sales_owner", "sales_owner_name", "members", "member_names", "expected_amount_note", "parties", "milestones", "can_edit", "created_at", "updated_at"]
        read_only_fields = ["code", "execution_phase"]

    def get_member_names(self, obj):
        return [u.name for u in obj.members.all()]

    def get_can_edit(self, obj):
        return is_project_editable(self.context["request"].user, obj)

    def to_representation(self, obj):
        data = super().to_representation(obj)
        if not can(self.context["request"].user, "project.see_scale_note"):
            data.pop("expected_amount_note", None)  # 自由文本可能含金额，仅商务/负责人可见
        return data

    def validate(self, data):
        # 无权查看“预计规模”的角色也不能写入（防止盲写覆盖）
        if not can(self.context["request"].user, "project.see_scale_note"):
            data.pop("expected_amount_note", None)
        return data

    def validate_presale_phase(self, v):
        if role_of(self.context["request"].user) == SALES and v in SALES_FORBIDDEN_PHASES and (not self.instance or self.instance.presale_phase != v):
            raise serializers.ValidationError("“成交”由合同/订单确认自动产生，销售不能直接设置")
        return v


class ProjectViewSet(viewsets.ModelViewSet):
    permission_classes = [HasCap.of("project.edit", read_cap="project.read")]
    SUB_ACTIONS = {"updates", "milestones", "update_milestone", "related", "parties", "remove_party"}

    def get_permissions(self):
        if self.action in self.SUB_ACTIONS:
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        qs = visible_projects(self.request.user).select_related("sales_owner").prefetch_related("parties__account", "members", "milestones")
        p = self.request.query_params
        if q := p.get("q"):
            q = str_param(q, "q", 100)
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(parties__account__name__icontains=q) | Q(machine_type__icontains=q) | Q(region__icontains=q)).distinct()
        if ph := p.get("presale_phase"):
            qs = qs.filter(presale_phase=ph)
        if ex := p.get("execution_phase"):
            qs = qs.filter(execution_phase=ex)
        if p.get("mine") == "1":
            u = self.request.user
            qs = qs.filter(Q(sales_owner=u) | Q(members=u) | Q(created_by=u)).distinct()
        if self.action == "list":
            qs = qs.annotate(
                milestone_done=Count("milestones", filter=Q(milestones__state__in=["DONE", "SKIPPED"]), distinct=True),
                milestone_total=Count("milestones", distinct=True),
            )
        return qs

    def get_serializer_class(self):
        return ProjectListSerializer if self.action == "list" else ProjectSerializer

    def _locked(self, pk_value=None):
        """写操作：加行锁重新读取项目，并校验可编辑范围（D7）。"""
        obj = self.get_object()
        locked = Project.objects.select_for_update().get(pk=obj.pk)
        if not is_project_editable(self.request.user, locked):
            raise PermissionDenied("只能修改自己负责或参与的项目")
        return locked

    def perform_create(self, s):
        user = self.request.user
        if role_of(user) == SALES:
            # 销售新建：负责人固定为本人，不能顺带授权他人
            if s.validated_data.get("sales_owner") not in (None, user):
                raise PermissionDenied("销售只能创建自己负责的项目")
            if s.validated_data.get("members"):
                raise PermissionDenied("销售不能指定项目成员，请联系商务或负责人")
            owner = user
        else:
            owner = s.validated_data.get("sales_owner")
        if role_of(user) == SALES and s.validated_data.get("presale_phase") in SALES_FORBIDDEN_PHASES:
            raise PermissionDenied("“成交”由合同/订单确认自动产生")
        from apps.common.retry import create_with_code

        obj = create_with_code(next_project_code, lambda code: s.save(code=code, created_by=user, sales_owner=owner), serializer=s)
        audit(self.request, "create", "project", obj.id, f"新建项目 {obj.code} {obj.name}")

    def update(self, request, *a, **kw):
        partial = kw.pop("partial", False)
        obj = self._locked()
        s = self.get_serializer(obj, data=request.data, partial=partial)
        s.is_valid(raise_exception=True)
        if not can(request.user, "project.edit_any"):
            if "sales_owner" in s.validated_data and s.validated_data["sales_owner"] != obj.sales_owner:
                raise PermissionDenied("销售不能变更项目负责人")
            if "members" in s.validated_data and set(s.validated_data["members"]) != set(obj.members.all()):
                raise PermissionDenied("销售不能变更项目成员")
        obj = s.save()
        audit(request, "update", "project", obj.id, f"修改项目 {obj.code}", {k: str(v) for k, v in s.validated_data.items() if k not in ("members", "expected_amount_note")})
        return Response(self.get_serializer(obj).data)

    def destroy(self, request, *a, **kw):
        if not can(request.user, "project.delete"):
            raise PermissionDenied("仅部门负责人可删除项目")
        obj = self._locked()
        if obj.quotes.exists() or obj.orders.exists() or obj.performances.exists():
            raise BusinessError("项目已有报价、订单或业绩，不能删除。", code="protected")
        audit(request, "delete", "project", obj.id, f"删除项目 {obj.code}")
        obj.delete()
        return Response(status=204)

    # ---------- 参与方 ----------
    @action(detail=True, methods=["get", "post"])
    def parties(self, request, pk=None):
        if request.method == "GET":
            project = self.get_object()
            return Response(PartySerializer(project.parties.select_related("account"), many=True).data)
        if not can(request.user, "project.edit"):
            raise PermissionDenied()
        project = self._locked()
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
        if not can(request.user, "project.edit"):
            raise PermissionDenied()
        project = self._locked()
        party = project.parties.filter(pk=party_id).first()
        if not party:
            raise BusinessError("参与方不存在", code="not_found", http_status=404)
        if project.quotes.filter(recipient=party.account).exists():
            raise BusinessError("该组织已是本项目报价的接收客户，不能移除。", code="protected")
        audit(request, "remove_party", "project", project.id, f"移除参与方 {party.account.name}（{party.party_role}）")
        party.delete()
        return Response(status=204)

    # ---------- 进展日志 ----------
    @action(detail=True, methods=["get", "post"])
    def updates(self, request, pk=None):
        if request.method == "GET":
            project = self.get_object()
            qs = project.updates.select_related("created_by")
            if st := request.query_params.get("stream"):
                qs = qs.filter(stream=st)
            return Response(UpdateSerializer(qs, many=True).data)
        s = UpdateSerializer(data={**request.data, "happened_at": request.data.get("happened_at") or timezone.now()})
        s.is_valid(raise_exception=True)
        stream = s.validated_data["stream"]
        if stream == ProjectUpdate.PRESALE:
            if not can(request.user, "update.presale.write"):
                raise PermissionDenied("售前跟进由销售/商务录入")
            project = self._locked()  # 售前跟进需要项目编辑范围
        else:
            if not can(request.user, "update.execution.write"):
                raise PermissionDenied("合同执行进度由商务/财务更新")
            self.get_object()
            project = Project.objects.select_for_update().get(pk=pk)
        phase = s.validated_data.get("phase")
        if stream == ProjectUpdate.PRESALE and phase:
            if phase not in PRESALE_PHASES:
                raise serializers.ValidationError({"phase": "无效的售前阶段"})
            if role_of(request.user) == SALES and phase in SALES_FORBIDDEN_PHASES:
                raise PermissionDenied("“成交”由合同/订单确认自动产生")
        upd = s.save(project=project, created_by=request.user)
        fields = ["updated_at"]
        if stream == ProjectUpdate.PRESALE and phase:
            project.presale_phase = phase
            fields.append("presale_phase")
        project.save(update_fields=fields)
        audit(request, "add_update", "project", project.id, f"[{upd.get_stream_display()}] {upd.title}")
        return Response(UpdateSerializer(upd).data, status=201)

    # ---------- 执行节点 ----------
    @action(detail=True, methods=["get", "post"])
    def milestones(self, request, pk=None):
        project = self.get_object()
        if request.method == "POST":
            # V2.1：执行节点只在订单确认时自动建立；手工初始化仅限商务/负责人补救历史项目
            if not can(request.user, "milestone.init"):
                raise PermissionDenied("执行节点在合同/订单确认后自动建立")
            if not project.orders.filter(status="CONFIRMED").exists():
                raise BusinessError("项目尚无已确认的合同/订单，不能建立执行节点。", code="no_order")
            project = Project.objects.select_for_update().get(pk=project.pk)
            init_milestones(project, request.user)
            audit(request, "init_milestones", "project", project.id, "初始化执行节点")
        return Response(MilestoneSerializer(project.milestones.select_related("updated_by"), many=True).data)

    @action(detail=True, methods=["patch"], url_path=r"milestones/(?P<mid>\d+)")
    def update_milestone(self, request, pk=None, mid=None):
        if not can(request.user, "update.execution.write"):
            raise PermissionDenied("执行节点由商务/财务维护，销售仅可查看")
        self.get_object()
        project = Project.objects.select_for_update().get(pk=pk)
        m = ExecutionMilestone.objects.select_for_update().filter(project=project, pk=int_param(mid, "mid")).first()
        if not m:
            raise BusinessError("节点不存在", code="not_found", http_status=404)
        old = m.get_state_display()
        s = MilestoneSerializer(m, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        m = s.save(updated_by=request.user)
        if m.state == "DONE" and not m.actual_date:
            m.actual_date = timezone.localdate()
            m.save(update_fields=["actual_date"])
        note = m.note if m.state != "DELAYED" else f"延期原因：{m.delay_reason}。{m.note}".strip()
        ProjectUpdate.objects.create(project=project, stream=ProjectUpdate.EXECUTION, title=f"{m.stage}：{old} → {m.get_state_display()}", content=note, phase=m.stage, happened_at=timezone.now(), created_by=request.user)
        refresh_execution_phase(project)
        audit(request, "update_milestone", "project", project.id, f"执行节点 {m.stage} → {m.get_state_display()}")
        return Response(MilestoneSerializer(m).data)

    @action(detail=True, methods=["post"], url_path="execution-phase")
    def set_execution_phase(self, request, pk=None):
        """暂停/恢复执行（不可绕过节点直接设置为已验收/已关闭）。"""
        if not can(request.user, "update.execution.write"):
            raise PermissionDenied()
        self.get_object()
        project = Project.objects.select_for_update().get(pk=pk)
        target = str_param(request.data.get("phase"), "phase", 10, required=True)
        if target not in ("暂停", "执行中"):
            raise serializers.ValidationError({"phase": "只能设置为“暂停”或“执行中”"})
        if not project.milestones.exists():
            raise BusinessError("项目尚未进入执行阶段", code="no_execution")
        project.execution_phase = target
        project.save(update_fields=["execution_phase", "updated_at"])
        if target == "执行中":
            refresh_execution_phase(project, force=True)
        ProjectUpdate.objects.create(project=project, stream=ProjectUpdate.EXECUTION, title=f"执行阶段 → {target}", content=str_param(request.data.get("note"), "note", 300), happened_at=timezone.now(), created_by=request.user)
        audit(request, "execution_phase", "project", project.id, f"执行阶段 → {target}")
        return Response(ProjectSerializer(project, context={"request": request}).data)

    # ---------- 360° 关联 ----------
    @action(detail=True, methods=["get"])
    def related(self, request, pk=None):
        """项目关联的报价/订单/业绩：按角色裁剪字段；销售无报价与订单；业绩按业绩范围而非项目范围（D7）。"""
        from apps.contracts.views import order_payload
        from apps.performances.views import performance_payload
        from apps.projects.scope import performances_for
        from apps.quotations.views import quote_summary_for

        project = self.get_object()
        user = request.user
        return Response({
            "quotes": quote_summary_for(user, project),
            "orders": [order_payload(o, user) for o in project.orders.select_related("legal_entity", "customer", "quote", "quote_revision", "confirmed_by")] if can(user, "order.access") else [],
            "performances": [performance_payload(p, user) for p in performances_for(user).filter(project=project)],
        })


def init_milestones(project, user=None):
    for i, st in enumerate(MILESTONE_STAGES):
        ExecutionMilestone.objects.get_or_create(project=project, stage=st, defaults={"sort": i, "updated_by": user})
    m = project.milestones.filter(stage="订单已确认").first()
    if m and m.state == "PENDING":
        m.state, m.actual_date = "DONE", timezone.localdate()
        m.save()
    refresh_execution_phase(project)


def refresh_execution_phase(project, force=False):
    ms = list(project.milestones.all())
    if not ms:
        if project.execution_phase != "未启动":
            project.execution_phase = "未启动"
            project.save(update_fields=["execution_phase", "updated_at"])
        return
    if project.execution_phase == "暂停" and not force:
        return
    if all(m.state in ("DONE", "SKIPPED") for m in ms):
        phase = "已关闭"
    elif any(m.stage == "验收" and m.state == "DONE" for m in ms):
        phase = "已验收"
    else:
        phase = "执行中"
    assert phase in EXECUTION_PHASES
    project.execution_phase = phase
    project.save(update_fields=["execution_phase", "updated_at"])
