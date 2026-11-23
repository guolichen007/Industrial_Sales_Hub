from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.service import audit
from apps.common.errors import BusinessError
from apps.common.params import bool_param, int_param, str_param
from apps.common.roles import SALES, HasCap, can, role_of

from .models import EXECUTION_PHASES, MILESTONE_STAGES, PRESALE_PHASES, ExecutionMilestone, Project, ProjectParty, ProjectUpdate, StageDef, TrackEvent
from .scope import assert_sales_writable, editable_projects, is_project_editable, visible_projects

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
        fields = ["id", "project", "stream", "title", "content", "public_content", "phase", "contact_name", "next_action", "next_action_date", "happened_at", "created_by_name", "created_at"]
        read_only_fields = ["project", "created_at"]

    def validate_happened_at(self, v):
        # 业务发生时间可补录，但不能写成未来（系统录入时间 created_at 由服务器生成，客户端无法提供）
        if v and v > timezone.now() + timezone.timedelta(minutes=5):
            raise serializers.ValidationError("发生时间不能晚于当前时间")
        return v

    def to_representation(self, obj):
        data = super().to_representation(obj)
        req = self.context.get("request")
        if req is not None and role_of(req.user) == SALES and not sales_may_see_content(req.user, obj):
            # RC3-T24：商务/财务录入的内部自由文本对销售不可见，只给“销售可见说明”
            data["content"] = obj.public_content
        return data


def sales_may_see_content(user, upd):
    """销售能看到原始内容的情形：本人录入的售前跟进。其他（执行线、商务代录）只看 public_content。"""
    return upd.stream == ProjectUpdate.PRESALE and upd.created_by_id == user.id


class MilestoneSerializer(serializers.ModelSerializer):
    """执行节点安全视图：不含任何金额字段，可给销售。"""

    updated_by_name = serializers.CharField(source="updated_by.name", read_only=True, default="")

    class Meta:
        model = ExecutionMilestone
        fields = ["id", "stage", "sort", "state", "owner_name", "planned_date", "actual_date", "note", "delay_reason", "public_note", "updated_by_name", "updated_at"]
        read_only_fields = ["stage", "sort"]

    def to_representation(self, obj):
        data = super().to_representation(obj)
        req = self.context.get("request")
        if req is None or role_of(req.user) == SALES:
            # RC3-T24：内部说明与延期原因不返回给销售（未传 request 时按最严格处理）
            data.pop("note", None)
            data.pop("delay_reason", None)
        return data

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
        fields = ["id", "code", "name", "industry", "machine_type", "region", "location", "investor", "presale_phase", "execution_phase", "sales_owner", "sales_owner_name", "party_names", "milestone_done", "milestone_total", "can_edit", "won_at", "sales_locked_at", "updated_at"]

    def get_party_names(self, obj):
        return [f"{p.account.name}（{p.party_role}）" for p in obj.parties.all()]

    def get_can_edit(self, obj):
        return is_project_editable(self.context["request"].user, obj)

    def to_representation(self, obj):
        data = super().to_representation(obj)
        data["updated_by_name"] = obj.updated_by.name if obj.updated_by_id else ""
        # 5753b5f 审查 R76-P1-01 / P2-04：列表直接给出两条状态线的安全摘要（无金额、无内部/公开说明自由文本），批量计算
        status_map = self.context.get("status_map")
        if status_map is not None:
            data["status"] = status_map.get(obj.id)
        return data


LIST_STATUS_FIELDS = ("code", "label", "is_final", "happened_on", "created_at", "created_by_name", "coverage_stale")


def list_status_map(projects):
    """批量计算项目列表的状态线安全摘要（5753b5f 审查 R76-P1-01）：固定 3 次查询，与项目数无关。
    只含阶段、发生日期、记录人、系统时间与完结/失效标识；不含 private_note、public_note、金额。"""
    ids = [p.id for p in projects]
    if not ids:
        return {}
    defs = {(d.track, d.code): d for d in StageDef.objects.all()}
    orders = {}
    from apps.contracts.models import OrderConfirmation

    for pid, oid in OrderConfirmation.objects.filter(project_id__in=ids, status="CONFIRMED").values_list("project_id", "id"):
        orders.setdefault(pid, []).append(oid)
    latest = {}
    for ev in TrackEvent.objects.filter(project_id__in=ids, reverted_at__isnull=True).select_related("created_by").order_by("project_id", "track", "-happened_on", "-id"):
        latest.setdefault((ev.project_id, ev.track), ev)
    out = {}
    for p in projects:
        cur = sorted(orders.get(p.id, []))
        item = {"won": bool(p.sales_locked_at), "active_order_count": len(cur)}
        for track in ("DELIVERY", "PAYMENT"):
            ev = latest.get((p.id, track))
            if ev is None:
                item[track.lower()] = None
                continue
            d = defs.get((track, ev.code))
            fin = bool(d and d.is_final)
            item[track.lower()] = {"code": ev.code, "label": d.label if d else ev.label_snapshot, "is_final": fin, "happened_on": ev.happened_on,
                                   "created_at": ev.created_at, "created_by_name": ev.created_by.name if ev.created_by else "", "coverage_stale": coverage_stale(ev, fin, cur)}
        done = [item[k] for k in ("delivery", "payment")]
        item["all_final"] = all(x and x["is_final"] and not x["coverage_stale"] for x in done)
        item["needs_reconfirmation"] = any(x and x["coverage_stale"] for x in done)
        item["tracking"] = (item["won"] or item["active_order_count"] > 0) and not item["all_final"]
        out[p.id] = item
    return out


class ProjectSerializer(serializers.ModelSerializer):
    """项目详情：参与方、两条进度线、执行节点；**永不**包含报价金额、合同金额与财务字段。"""

    sales_owner_name = serializers.CharField(source="sales_owner.name", read_only=True, default="")
    parties = PartySerializer(many=True, read_only=True)
    milestones = serializers.SerializerMethodField()
    tracks = serializers.SerializerMethodField()
    member_names = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    won_by_name = serializers.CharField(source="won_by.name", read_only=True, default="")
    created_by_name = serializers.CharField(source="created_by.name", read_only=True, default="")
    updated_by_name = serializers.CharField(source="updated_by.name", read_only=True, default="")

    class Meta:
        model = Project
        fields = ["id", "code", "name", "industry", "machine_type", "region", "location", "investor", "description", "presale_phase", "execution_phase", "sales_owner", "sales_owner_name", "members", "member_names", "expected_amount_note", "parties", "milestones", "tracks", "delivery_due_on", "can_edit",
                  "won_at", "won_by_name", "won_note", "sales_locked_at", "created_by_name", "created_at", "updated_by_name", "updated_at"]
        read_only_fields = ["code", "execution_phase", "won_at", "won_note", "sales_locked_at"]

    def get_member_names(self, obj):
        return [u.name for u in obj.members.all()]

    def get_milestones(self, obj):
        return MilestoneSerializer(obj.milestones.all(), many=True, context=self.context).data

    def get_tracks(self, obj):
        return track_summary(obj, self.context["request"].user)

    def get_can_edit(self, obj):
        return is_project_editable(self.context["request"].user, obj)

    def validate_delivery_due_on(self, v):
        if v is not None and not can(self.context["request"].user, "delivery.write"):
            raise serializers.ValidationError("计划交付日期由商务维护")
        return v

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
        if role_of(self.context["request"].user) == SALES:
            if v in SALES_FORBIDDEN_PHASES and (not self.instance or self.instance.presale_phase != v):
                raise serializers.ValidationError("“成交”由合同/订单确认自动产生，销售不能直接设置")
            if self.instance and self.instance.presale_phase == "成交" and v != "成交":
                raise serializers.ValidationError("项目已成交（有合同/订单确认），销售不能改回其他阶段")
        return v


class ProjectViewSet(viewsets.ModelViewSet):
    permission_classes = [HasCap.of("project.edit", read_cap="project.read")]
    SUB_ACTIONS = {"updates", "milestones", "update_milestone", "related", "parties", "remove_party", "set_execution_phase", "handoff", "handoff_revert", "tracks", "revert_track", "activity"}

    def get_permissions(self):
        if self.action in self.SUB_ACTIONS:
            return [IsAuthenticated()]
        return super().get_permissions()

    def list(self, request, *a, **kw):
        """项目列表。tracking=1：已中标或已有有效合同、且两条状态线未全部完结的项目（销售“进度”默认入口，5753b5f 审查 R76-P1-01）；
        tracking=all：已中标或已有有效合同的全部项目（含已完结，不悄悄隐藏）。旧 execution_phase 过滤保留为高级条件。"""
        projects = list(self.filter_queryset(self.get_queryset()))
        smap = list_status_map(projects)
        tr = request.query_params.get("tracking")
        if tr == "1":
            projects = [p for p in projects if smap[p.id]["tracking"]]
        elif tr == "all":
            projects = [p for p in projects if smap[p.id]["won"] or smap[p.id]["active_order_count"] > 0]
        elif tr not in (None, ""):
            raise serializers.ValidationError({"tracking": "只能是 1 或 all"})
        return Response(ProjectListSerializer(projects, many=True, context={**self.get_serializer_context(), "status_map": smap}).data)

    def get_queryset(self):
        qs = visible_projects(self.request.user).select_related("sales_owner", "updated_by").prefetch_related("parties__account", "members", "milestones")
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
        """写操作：加行锁重新读取项目，并校验可编辑范围（D7）与中标转交锁定（RC3-G1，统一断言）。"""
        obj = self.get_object()
        locked = Project.objects.select_for_update().get(pk=obj.pk)
        assert_sales_writable(self.request.user, locked)
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
        obj = s.save(updated_by=request.user)
        audit(request, "update", "project", obj.id, f"修改项目 {obj.code}", {"fields": sorted(k for k in s.validated_data if k != "expected_amount_note"),
                                                                               **{k: str(v) for k, v in s.validated_data.items() if k not in ("members", "expected_amount_note")}})
        return Response(self.get_serializer(obj).data)

    # ---------- RC3-G1 中标确认 / 转交商务 ----------
    @action(detail=True, methods=["post"])
    def handoff(self, request, pk=None):
        """中标确认并转交商务：记录 won_at/won_by，锁定销售写入（可早于合同/订单）。幂等。"""
        if not can(request.user, "project.handoff"):
            raise PermissionDenied("中标确认/转交由商务或负责人执行")
        self.get_object()
        project = Project.objects.select_for_update().get(pk=pk)
        if project.sales_locked_at:
            return Response(self.get_serializer(project).data)
        now = timezone.now()
        happened = request.data.get("won_at")
        if happened:
            from django.utils.dateparse import parse_date, parse_datetime

            d = parse_datetime(str(happened)) or parse_date(str(happened))
            if d is None:
                raise serializers.ValidationError({"won_at": "日期格式无效"})
            if not hasattr(d, "hour"):
                from datetime import datetime, time

                d = datetime.combine(d, time(12))
            if timezone.is_naive(d):
                d = timezone.make_aware(d)
            if d > now:
                raise serializers.ValidationError({"won_at": "中标时间不能晚于当前时间"})
        project.won_at = happened and d or now
        project.won_by = request.user
        project.won_note = str_param(request.data.get("note"), "note", 200) or ""
        project.sales_locked_at = now
        project.presale_phase = "成交"
        project.updated_by = request.user
        project.save(update_fields=["won_at", "won_by", "won_note", "sales_locked_at", "presale_phase", "updated_by", "updated_at"])
        ProjectUpdate.objects.create(project=project, stream=ProjectUpdate.PRESALE, title="中标确认，项目转交商务", content=project.won_note, phase="成交",
                                     happened_at=project.won_at, created_by=request.user)
        audit(request, "handoff", "project", project.id, f"中标确认并转交商务 {project.code}（销售写入已锁定）")
        return Response(self.get_serializer(project).data)

    @action(detail=True, methods=["post"], url_path="handoff/revert")
    def handoff_revert(self, request, pk=None):
        """撤销中标（误操作/最终失标）：仅负责人，必须填写原因；已有有效合同/订单时不允许。"""
        if not can(request.user, "project.handoff_revert"):
            raise PermissionDenied("撤销中标仅部门负责人可执行")
        self.get_object()
        project = Project.objects.select_for_update().get(pk=pk)
        reason = str_param(request.data.get("reason"), "reason", 200, required=True)
        if not project.sales_locked_at:
            raise BusinessError("项目当前未处于中标转交状态", code="not_won")
        if project.orders.filter(status="CONFIRMED").exists():
            raise BusinessError("项目已有有效的合同/订单确认，不能撤销中标；请先撤销合同/订单确认。", code="has_order")
        target = str_param(request.data.get("presale_phase"), "presale_phase", 10) or "结果待定"
        if target not in PRESALE_PHASES or target == "成交":
            raise serializers.ValidationError({"presale_phase": "请选择撤销后的售前阶段（不能是“成交”）"})
        project.won_at = project.won_by = None
        project.won_note = ""
        project.sales_locked_at = None
        project.presale_phase = target
        project.updated_by = request.user
        project.save(update_fields=["won_at", "won_by", "won_note", "sales_locked_at", "presale_phase", "updated_by", "updated_at"])
        ProjectUpdate.objects.create(project=project, stream=ProjectUpdate.PRESALE, title=f"撤销中标 → {target}", content=reason, phase=target, happened_at=timezone.now(), created_by=request.user)
        audit(request, "handoff_revert", "project", project.id, f"撤销中标 {project.code}：{reason}")
        return Response(self.get_serializer(project).data)

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
        project.updated_by = request.user
        project.save(update_fields=["updated_by", "updated_at"])
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
            return Response(UpdateSerializer(qs, many=True, context={"request": request}).data)
        body = {k: v for k, v in request.data.items() if k != "created_at"}  # 系统录入时间不接受客户端提供
        s = UpdateSerializer(data={**body, "happened_at": body.get("happened_at") or timezone.now()}, context={"request": request})
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
            if role_of(request.user) == SALES and (phase in SALES_FORBIDDEN_PHASES or project.presale_phase == "成交"):
                raise PermissionDenied("“成交”由中标确认/合同订单确认产生；已成交项目的阶段由商务/负责人维护")
        upd = s.save(project=project, created_by=request.user)
        project.updated_by = request.user
        fields = ["updated_by", "updated_at"]
        if stream == ProjectUpdate.PRESALE and phase:
            project.presale_phase = phase
            fields.append("presale_phase")
        project.save(update_fields=fields)
        audit(request, "add_update", "project", project.id, f"[{upd.get_stream_display()}] {upd.title}")
        return Response(UpdateSerializer(upd, context={"request": request}).data, status=201)

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
        return Response(MilestoneSerializer(project.milestones.select_related("updated_by"), many=True, context={"request": request}).data)

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
        s = MilestoneSerializer(m, data=request.data, partial=True, context={"request": request})
        s.is_valid(raise_exception=True)
        m = s.save(updated_by=request.user)
        if m.state == "DONE" and not m.actual_date:
            m.actual_date = timezone.localdate()
            m.save(update_fields=["actual_date"])
        note = m.note if m.state != "DELAYED" else f"延期原因：{m.delay_reason}。{m.note}".strip()
        ProjectUpdate.objects.create(project=project, stream=ProjectUpdate.EXECUTION, title=f"{m.stage}：{old} → {m.get_state_display()}", content=note, public_content=m.public_note,
                                     phase=m.stage, happened_at=timezone.now(), created_by=request.user)
        refresh_execution_phase(project)
        audit(request, "update_milestone", "project", project.id, f"执行节点 {m.stage} → {m.get_state_display()}")
        return Response(MilestoneSerializer(m, context={"request": request}).data)

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
        if target == "执行中" and not project.orders.filter(status="CONFIRMED").exists():
            raise BusinessError("项目已无有效的合同/订单确认，不能恢复执行；请先重新确认合同/订单。", code="no_order")
        project.execution_phase = target
        project.save(update_fields=["execution_phase", "updated_at"])
        if target == "执行中":
            refresh_execution_phase(project, force=True)
        ProjectUpdate.objects.create(project=project, stream=ProjectUpdate.EXECUTION, title=f"执行阶段 → {target}", content=str_param(request.data.get("note"), "note", 300), happened_at=timezone.now(), created_by=request.user)
        audit(request, "execution_phase", "project", project.id, f"执行阶段 → {target}")
        return Response(ProjectSerializer(project, context={"request": request}).data)

    # ---------- RC3-G2 状态线 ----------
    @action(detail=True, methods=["get", "post"])
    def tracks(self, request, pk=None):
        """GET：两条状态线历史（销售无 private_note）；POST：记录一个状态事件（delivery.write / payment.write）。"""
        project = self.get_object()
        if request.method == "GET":
            qs = project.track_events.select_related("created_by", "reverted_by")
            return Response({"summary": track_summary(project, request.user), "events": TrackEventSerializer(qs, many=True, context={"request": request}).data})
        track = request.data.get("track")
        if track not in ("DELIVERY", "PAYMENT"):
            raise serializers.ValidationError({"track": "只能是 DELIVERY 或 PAYMENT"})
        if not can(request.user, "delivery.write" if track == "DELIVERY" else "payment.write"):
            raise PermissionDenied("生产/交付由商务维护，资金状态由财务/负责人维护" if track == "PAYMENT" else "生产/交付状态由商务/财务维护")
        project = Project.objects.select_for_update().get(pk=project.pk)
        if not project.sales_locked_at and not project.orders.filter(status="CONFIRMED").exists():
            raise BusinessError("项目尚未中标确认或订单确认，不能记录执行/资金状态", code="not_won")
        code = str(request.data.get("code") or "")
        d = stage_map(track).get(code)
        if d is None or not d.active:
            raise serializers.ValidationError({"code": "阶段不存在或已停用"})
        body = {k: request.data.get(k) for k in ("happened_on", "public_note", "private_note") if k in request.data}
        body.setdefault("happened_on", timezone.localdate().isoformat())
        s = TrackEventSerializer(data={**body, "track": track, "code": code}, context={"request": request})
        s.is_valid(raise_exception=True)
        if s.validated_data["happened_on"] > timezone.localdate():
            raise serializers.ValidationError({"happened_on": "发生日期不能晚于今天"})
        # RC3 收口 D-5：两条状态线是项目总体状态；多张有效合同/订单时，“已交付/完结”“已结清”等完结阶段须人工确认已覆盖全部合同/批次
        order_ids = sorted(project.orders.filter(status="CONFIRMED").values_list("id", flat=True))
        n_orders = len(order_ids)
        covers = bool_param(request.data.get("confirm_all_covered", False))
        # 6912049 审查 R75-01：该线当前完结状态的覆盖确认已失效（合同集合变化，含 1→0、1→另一张）时，重新记录完结也须确认
        prev = current_event(project, track)
        prev_d = stage_map(track).get(prev.code) if prev else None
        stale = bool(prev and coverage_stale(prev, bool(prev_d and prev_d.is_final), order_ids))
        need = d.is_final and (n_orders > 1 or stale)
        if need and not covers:
            why = (f"本项目有 {n_orders} 张有效合同/订单，状态线记录的是项目总体状态。" if n_orders > 1
                   else "原完结记录的覆盖确认已失效（合同/订单有变化，或该记录缺少覆盖确认）。")
            raise BusinessError(f"{why}记录“{d.label}”前请确认已覆盖全部合同/批次（confirm_all_covered）。", code="confirm_coverage_required", http_status=409)
        ev = s.save(project=project, created_by=request.user, label_snapshot=d.label, active_orders_at=n_orders, covers_all_orders=bool(need and covers),
                    covered_order_ids=order_ids)
        project.updated_by = request.user
        project.save(update_fields=["updated_by", "updated_at"])
        ProjectUpdate.objects.create(project=project, stream=ProjectUpdate.EXECUTION, title=f"{'生产/交付' if track == 'DELIVERY' else '资金状态'}：{d.label}",
                                     # 进展流只带公开说明：内部说明只保存在 TrackEvent，由 track_summary/TrackEventSerializer 按写权限裁剪
                                     # （否则商务可经进展流读到资金线内部说明）
                                     content=ev.public_note, public_content=ev.public_note, phase=code, happened_at=timezone.now(), created_by=request.user)
        audit(request, "track_event", "project", project.id, f"{track} → {code}（{d.label}）" + (f"；已确认覆盖全部 {n_orders} 张合同/订单" if ev.covers_all_orders else ""),
              {"event_id": ev.id, "happened_on": str(ev.happened_on), "active_orders": n_orders, "covers_all_orders": ev.covers_all_orders})
        return Response({"summary": track_summary(project, request.user), "event": TrackEventSerializer(ev, context={"request": request}).data}, status=201)

    @action(detail=True, methods=["post"], url_path=r"tracks/(?P<eid>\d+)/revert")
    def revert_track(self, request, pk=None, eid=None):
        """撤销一条状态事件（录错）：只标记，不删除；当前状态回到上一条有效事件。"""
        self.get_object()
        project = Project.objects.select_for_update().get(pk=pk)
        ev = project.track_events.select_for_update().filter(pk=int_param(eid, "eid")).first()
        if not ev:
            raise BusinessError("事件不存在", code="not_found", http_status=404)
        if not can(request.user, "delivery.write" if ev.track == "DELIVERY" else "payment.write"):
            raise PermissionDenied()
        if ev.reverted_at:
            raise BusinessError("该事件已撤销", code="already_reverted")
        ev.reverted_at, ev.reverted_by = timezone.now(), request.user
        ev.revert_reason = str_param(request.data.get("reason"), "reason", 200, required=True)
        ev.save(update_fields=["reverted_at", "reverted_by", "revert_reason"])
        # 撤销原因只保存在 TrackEvent.revert_reason（TrackEventSerializer 按该线写权限裁剪）；进展流只记固定文案，
        # 否则无 payment.write 的商务可经进展流读到资金线撤销原因（RC3 独立审计 V-01）
        note = "录错已撤销，原因见状态历史"
        ProjectUpdate.objects.create(project=project, stream=ProjectUpdate.EXECUTION, title=f"撤销状态记录：{ev.label_snapshot}", content=note, public_content=note,
                                     happened_at=timezone.now(), created_by=request.user)
        audit(request, "track_revert", "project", project.id, f"撤销状态事件 {ev.track}/{ev.code}", {"event_id": ev.id})
        return Response({"summary": track_summary(project, request.user)})

    # ---------- RC3-G4 项目操作动态（按角色裁剪） ----------
    SAFE_PROJECT_ACTIONS = {"create": "新建项目", "update": "修改项目资料", "add_party": "关联参与方", "remove_party": "移除参与方", "handoff": "中标确认/转交商务",
                            "handoff_revert": "撤销中标", "track_event": "记录交付/资金状态", "track_revert": "撤销状态记录", "init_milestones": "建立执行节点",
                            "update_milestone": "更新执行节点", "execution_phase": "调整执行阶段", "add_update": "记录进展"}

    @action(detail=True, methods=["get"])
    def activity(self, request, pk=None):
        """操作动态：操作人 + 服务器时间（AuditEvent.at）+ 动作。
        - 销售：只看到动作名称、操作人、时间（不含摘要/字段值）；不含报价、订单、附件下载、审计内部细节；
        - 负责人/商务/财务：动作 + 摘要；报价相关动作只对报价权限角色返回。"""
        from apps.audit.models import AuditEvent

        project = self.get_object()
        user = request.user
        sales = role_of(user) == SALES
        qs = AuditEvent.objects.filter(resource="project", resource_id=str(project.id), action__in=self.SAFE_PROJECT_ACTIONS.keys()).order_by("-at", "-id")[:200]
        rows = []
        for ev in qs:
            item = {"at": ev.at, "actor_name": ev.actor_name, "action": ev.action, "action_label": self.SAFE_PROJECT_ACTIONS.get(ev.action, ev.action)}
            if not sales:
                item["summary"] = ev.summary
                if isinstance(ev.detail, dict) and ev.detail.get("fields"):
                    item["fields"] = ev.detail["fields"]
            rows.append(item)
        if can(user, "quote.access"):
            qids = list(project.quotes.values_list("id", flat=True))
            rids = [str(x) for x in project.quotes.values_list("revisions__id", flat=True) if x]
            for ev in AuditEvent.objects.filter(Q(resource="quote", resource_id__in=[str(x) for x in qids]) | Q(resource="quote_revision", resource_id__in=rids)).order_by("-at")[:200]:
                rows.append({"at": ev.at, "actor_name": ev.actor_name, "action": ev.action, "action_label": "报价：" + ev.action, "summary": ev.summary,
                             **({"fields": ev.detail["fields"]} if isinstance(ev.detail, dict) and ev.detail.get("fields") else {})})
        rows.sort(key=lambda r: r["at"], reverse=True)
        return Response(rows[:300])

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


# ====================== RC3-G2 两条状态线 ======================
def stage_map(track):
    ensure_default_stages()
    return {d.code: d for d in StageDef.objects.filter(track=track)}


def ensure_default_stages():
    from .models import DEFAULT_STAGES

    if StageDef.objects.exists():
        return
    for track, rows in DEFAULT_STAGES.items():
        for i, (code, label, final) in enumerate(rows):
            StageDef.objects.get_or_create(track=track, code=code, defaults={"label": label, "sort": i * 10, "is_final": final})


def current_event(project, track):
    return project.track_events.filter(track=track, reverted_at__isnull=True).order_by("-happened_on", "-id").first()


def coverage_stale(ev, is_final, current_ids):
    """完结类状态的覆盖确认是否失效（D-5；74616ed 审查 P1-02、6912049 审查 R75-01/R75-02）：
    1) 记录后有效合同/订单集合发生任何变化（新增、撤销、替换；与 0/1/多张数量无关）→ 失效；
    2) 记录当时已有 ≥2 张有效合同却没有覆盖确认（迁移前的旧数据、或 74616ed 时期的绕过）→ 失效，不静默视为有效。
    非完结状态不涉及覆盖确认。"""
    if not is_final:
        return False
    snap = sorted(ev.covered_order_ids or [])
    if snap != list(current_ids):
        return True
    return len(snap) > 1 and not ev.covers_all_orders


def track_summary(project, user):
    """项目两条状态线的当前值。所有角色可见：阶段（当前展示名）、发生日期、记录人与系统时间、公开说明；
    仅有对应写权限的角色可见 private_note。**任何角色都不返回金额**（模型中不存在金额字段）。"""
    out = {}
    current_ids = sorted(project.orders.filter(status="CONFIRMED").values_list("id", flat=True))
    for track, cap in (("DELIVERY", "delivery.write"), ("PAYMENT", "payment.write")):
        ev = current_event(project, track)
        defs = stage_map(track)
        if ev is None:
            out[track.lower()] = None
            continue
        d = defs.get(ev.code)
        item = {"code": ev.code, "label": d.label if d else ev.label_snapshot, "is_final": bool(d and d.is_final), "happened_on": ev.happened_on,
                "created_at": ev.created_at, "created_by_name": ev.created_by.name if ev.created_by else "", "public_note": ev.public_note,
                "covers_all_orders": ev.covers_all_orders}
        item["coverage_stale"] = coverage_stale(ev, item["is_final"], current_ids)
        if can(user, cap):
            item["private_note"] = ev.private_note
        out[track.lower()] = item
    # RC3 收口 D-5：明确“项目总体状态”，多合同时提示（不是每张合同/每个批次各自的状态）
    n = project.orders.filter(status="CONFIRMED").count()
    out["scope"] = "PROJECT"
    out["active_order_count"] = n
    out["multi_order"] = n > 1
    out["needs_reconfirmation"] = any((out.get(k) or {}).get("coverage_stale") for k in ("delivery", "payment"))
    due = project.delivery_due_on
    final = out.get("delivery") and out["delivery"]["is_final"] and not out["delivery"]["coverage_stale"]
    out["delivery_overdue"] = bool(due and not final and due < timezone.localdate())
    return out


class TrackEventSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.name", read_only=True, default="")
    reverted_by_name = serializers.CharField(source="reverted_by.name", read_only=True, default="")
    label = serializers.SerializerMethodField()

    class Meta:
        model = TrackEvent
        fields = ["id", "track", "code", "label", "label_snapshot", "happened_on", "created_at", "created_by_name", "public_note", "private_note", "reverted_at", "reverted_by_name", "revert_reason",
                  "active_orders_at", "covers_all_orders", "current_label"]
        read_only_fields = ["label_snapshot", "created_at", "reverted_at", "revert_reason", "active_orders_at", "covers_all_orders"]

    current_label = serializers.SerializerMethodField()

    def get_label(self, obj):
        # 74616ed 审查 P1-03：历史事件显示记录当时的名称（label_snapshot）；当前展示名另见 current_label
        if obj.label_snapshot:
            return obj.label_snapshot
        d = StageDef.objects.filter(track=obj.track, code=obj.code).first()
        return d.label if d else obj.code

    def get_current_label(self, obj):
        d = StageDef.objects.filter(track=obj.track, code=obj.code).first()
        return d.label if d else obj.label_snapshot

    def to_representation(self, obj):
        data = super().to_representation(obj)
        req = self.context.get("request")
        cap = "delivery.write" if obj.track == "DELIVERY" else "payment.write"
        if req is None or not can(req.user, cap):
            data.pop("private_note", None)
            data.pop("revert_reason", None)
        return data


class StageDefSerializer(serializers.ModelSerializer):
    updated_by_name = serializers.CharField(source="updated_by.name", read_only=True, default="")
    used = serializers.SerializerMethodField()

    class Meta:
        model = StageDef
        fields = ["id", "track", "code", "label", "sort", "active", "is_final", "used", "updated_by_name", "updated_at"]
        read_only_fields = ["track", "code", "is_final"]  # 稳定 code；终态语义固定（74616ed 审查 P0-01：不可经阶段配置修改）

    def get_used(self, obj):
        return TrackEvent.objects.filter(track=obj.track, code=obj.code).exists()


def _norm_label(v):
    import unicodedata

    return "".join(unicodedata.normalize("NFKC", str(v or "")).split()).lower()


def _reject_duplicate_label(track, label, instance):
    """6912049 审查 R75-03：同一状态线内展示名称（规范化：全半角、空白、大小写）不得重复，
    防止新建非完结阶段冒用“已交付/完结”等正式完结阶段的名称造成“假完结”。"""
    n = _norm_label(label)
    if not n:
        return
    qs = StageDef.objects.filter(track=track)
    if instance is not None:
        qs = qs.exclude(pk=instance.pk)
    if any(_norm_label(x.label) == n for x in qs):
        raise serializers.ValidationError({"label": "同一状态线已有同名阶段（含完结阶段），请使用可区分的名称"})


def _reject_is_final_change(data, instance):
    """74616ed 审查 P0-01：“是否完结阶段”（is_final）决定多合同完结确认与项目完结判断，属于固定业务语义，
    不能经阶段配置 API 修改（否则可先改成非完结、绕过覆盖确认记录完结、再改回）。显式提交不同值 → 400；
    新增阶段一律非完结。"""
    if "is_final" not in data:
        return
    want = bool_param(data.get("is_final"))
    have = bool(instance.is_final) if instance is not None else False
    if want != have:
        raise serializers.ValidationError({"is_final": "“完结阶段”属性为固定业务语义，不能在阶段配置中修改（只能改名称、排序、启停）"})


class StageDefViewSet(viewsets.ModelViewSet):
    """阶段配置：所有业务角色可读；负责人/商务可改展示名、排序、停用，可新增阶段（新 code）；不提供删除。"""

    serializer_class = StageDefSerializer
    permission_classes = [HasCap.of("stage.config", read_cap="project.read")]
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        ensure_default_stages()
        qs = StageDef.objects.select_related("updated_by")
        if (t := self.request.query_params.get("track")) in ("DELIVERY", "PAYMENT"):
            qs = qs.filter(track=t)
        return qs

    def create(self, request, *a, **kw):
        import re as _re

        track = request.data.get("track")
        code = str(request.data.get("code") or "").strip().upper()
        if track not in ("DELIVERY", "PAYMENT") or not _re.match(r"^[A-Z][A-Z0-9_]{1,29}$", code):
            raise serializers.ValidationError({"code": "track 为 DELIVERY/PAYMENT；code 为大写字母/数字/下划线（2–30 位），创建后不可修改"})
        if StageDef.objects.filter(track=track, code=code).exists():
            raise BusinessError("该阶段代码已存在", code="duplicate_stage")
        _reject_is_final_change(request.data, None)
        _reject_duplicate_label(track, request.data.get("label"), None)
        s = StageDefSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.save(track=track, code=code, updated_by=request.user, is_final=False)
        audit(request, "create", "stage_def", d.id, f"新增阶段 {track}/{code} {d.label}")
        return Response(StageDefSerializer(d).data, status=201)

    def perform_update(self, s):
        _reject_is_final_change(self.request.data, s.instance)
        if "label" in self.request.data:
            _reject_duplicate_label(s.instance.track, self.request.data.get("label"), s.instance)
        before = {k: getattr(s.instance, k) for k in ("label", "sort", "active", "is_final")}
        d = s.save(updated_by=self.request.user)
        changed = {k: [str(before[k]), str(getattr(d, k))] for k in before if before[k] != getattr(d, k)}
        audit(self.request, "update", "stage_def", d.id, f"修改阶段 {d.track}/{d.code}", {"changed": changed})


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
