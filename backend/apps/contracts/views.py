from django.db import IntegrityError, transaction
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.audit.service import audit
from apps.common.errors import BusinessError
from apps.common.params import bool_param, int_param, str_param
from apps.common.roles import HasCap, can
from apps.projects.scope import visible_projects

from . import services
from .models import OrderConfirmation, normalize_order_ref


class OrderSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    legal_entity_name = serializers.CharField(source="legal_entity.short_name", read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True, default="")
    quote_code = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    basis_label = serializers.CharField(source="get_basis_display", read_only=True)
    confirmed_by_name = serializers.CharField(source="confirmed_by.name", read_only=True, default="")
    performance_id = serializers.SerializerMethodField()

    class Meta:
        model = OrderConfirmation
        fields = ["id", "project", "project_name", "quote", "quote_revision", "quote_code", "legal_entity", "legal_entity_name", "customer", "customer_name", "basis", "basis_label", "order_ref", "signed_on", "contract_amount", "product_summary", "reason", "status", "status_label", "confirmed_by_name", "confirmed_at", "revoke_reason", "revoked_at", "created_at", "performance_id"]
        read_only_fields = ["status", "confirmed_at", "revoke_reason", "revoked_at"]
        extra_kwargs = {"legal_entity": {"required": False}}
        validators = []

    def get_quote_code(self, obj):
        if not obj.quote_id:
            return ""
        return f"{obj.quote.code}-V{obj.quote_revision.version}" if obj.quote_revision_id else obj.quote.code

    def get_performance_id(self, obj):
        p = getattr(obj, "performance", None)
        return p.id if p else None

    def validate(self, data):
        q = data.get("quote")
        rev = data.get("quote_revision")
        if rev and not q:
            q = data["quote"] = rev.quote
        if q:
            if q.project_id != data["project"].id:
                raise serializers.ValidationError({"quote": "报价不属于该项目"})
            if q.status == "CANCELLED":
                raise serializers.ValidationError({"quote": "报价已取消"})
            # 绑定冻结版本：未指定时取最新已生成版本；没有冻结版本的报价不能作为订单依据
            from apps.quotations.models import QuoteRevision

            if rev is None:
                rev = QuoteRevision.objects.filter(quote=q, status=QuoteRevision.GENERATED).order_by("-version").first()
                if rev is None:
                    raise serializers.ValidationError({"quote": "该报价尚无已生成（冻结）的版本"})
                data["quote_revision"] = rev
            elif rev.quote_id != q.id or rev.status != QuoteRevision.GENERATED:
                raise serializers.ValidationError({"quote_revision": "必须选择该报价已生成（冻结）的版本"})
            if data["quote_revision"].is_trial:
                raise serializers.ValidationError({"quote_revision": "试用报价（模板未签收）不能作为合同/订单依据"})
            if data.get("legal_entity") and data["legal_entity"].id != q.legal_entity_id:
                raise serializers.ValidationError({"legal_entity": "出具主体必须与报价一致"})
            if data.get("customer") and data["customer"].id != q.recipient_id:
                raise serializers.ValidationError({"customer": "客户必须与报价接收客户一致"})
            data["legal_entity"] = q.legal_entity
            data["customer"] = q.recipient
        if not data.get("legal_entity"):
            raise serializers.ValidationError({"legal_entity": "请选择出具主体"})
        if data.get("contract_amount") is not None and data["contract_amount"] < 0:
            raise serializers.ValidationError({"contract_amount": "金额不能为负"})
        data["order_ref"] = (data.get("order_ref") or "").strip()
        if data.get("basis") in ("CONTRACT", "ORDER") and not normalize_order_ref(data["order_ref"]):
            raise serializers.ValidationError({"order_ref": "以合同/订单为依据时必须填写合同/订单号"})
        return data


class OrderSafeSerializer(OrderSerializer):
    """财务视图（D5）：无合同金额；也不暴露报价编号。"""

    class Meta(OrderSerializer.Meta):
        fields = [f for f in OrderSerializer.Meta.fields if f not in ("contract_amount", "quote", "quote_revision", "quote_code")]


class OrderQuoteSerializer(OrderSerializer):
    """有报价权限但无合同金额权限（RC3 收口 D-2：财务）：可见关联报价编号，不见合同金额。"""

    class Meta(OrderSerializer.Meta):
        fields = [f for f in OrderSerializer.Meta.fields if f != "contract_amount"]


def order_serializer_for(user):
    if can(user, "order.amount"):
        return OrderSerializer
    return OrderQuoteSerializer if can(user, "quote.access") else OrderSafeSerializer


def order_payload(o, user):
    return order_serializer_for(user)(o).data


class OrderViewSet(viewsets.ModelViewSet):
    permission_classes = [HasCap.of("order.confirm", read_cap="order.access")]
    http_method_names = ["get", "post"]

    def get_serializer_class(self):
        return order_serializer_for(self.request.user)

    def get_queryset(self):
        qs = OrderConfirmation.objects.select_related("project", "legal_entity", "customer", "quote", "quote_revision", "confirmed_by").filter(project__in=visible_projects(self.request.user))
        if (p := int_param(self.request.query_params.get("project"), "project")) is not None:
            qs = qs.filter(project_id=p)
        if s := self.request.query_params.get("status"):
            qs = qs.filter(status=s)
        return qs

    def create(self, request, *a, **kw):
        user = request.user
        data = dict(request.data)
        key = str_param(data.pop("idempotency_key", None) or request.headers.get("Idempotency-Key"), "idempotency_key", 64) or None
        confirm_now = bool_param(data.pop("confirm_now", False))
        if not can(user, "order.amount") and data.get("contract_amount") not in (None, ""):
            raise PermissionDenied("当前角色不能录入合同金额（由负责人或商务维护）")
        if not can(user, "quote.access") and (data.get("quote") not in (None, "") or data.get("quote_revision") not in (None, "")):
            raise PermissionDenied("当前角色不能关联报价（无报价权限）")
        s = OrderSerializer(data=data)
        s.is_valid(raise_exception=True)
        fp = services.fingerprint({**s.validated_data, "confirm_now": confirm_now})
        if key:
            existing = OrderConfirmation.objects.filter(idempotency_key=key).first()
            if existing:
                if existing.idempotency_fingerprint != fp:
                    raise BusinessError("同一幂等键已用于内容不同的请求，请刷新页面后重新提交。", code="idempotency_conflict")
                return Response(self.get_serializer(existing).data, status=200)
        norm = normalize_order_ref(s.validated_data["order_ref"])
        if norm and OrderConfirmation.objects.filter(legal_entity=s.validated_data["legal_entity"], order_ref_norm=norm).exclude(status="REVOKED").exists():
            raise BusinessError(f"合同/订单号 {s.validated_data['order_ref']} 在该出具主体下已登记，禁止重复。", code="duplicate_order")
        try:
            with transaction.atomic():
                o = s.save(created_by=user, idempotency_key=key, idempotency_fingerprint=fp)
        except IntegrityError:
            # 并发：另一请求已用同一幂等键或同一订单号插入
            if key and (existing := OrderConfirmation.objects.filter(idempotency_key=key).first()):
                if existing.idempotency_fingerprint == fp:
                    return Response(self.get_serializer(existing).data, status=200)
                raise BusinessError("同一幂等键已用于内容不同的请求。", code="idempotency_conflict")
            raise BusinessError(f"合同/订单号 {s.validated_data['order_ref']} 已被同时登记，禁止重复。", code="duplicate_order")
        audit(request, "create", "order", o.id, f"登记确认依据 {o.get_basis_display()} {o.order_ref}")
        if confirm_now:
            o = services.confirm_order(o.id, user)  # 失败则整个请求回滚（WriteTransactionMiddleware），不会留下草稿
            audit(request, "confirm", "order", o.id, "确认订单并生成业绩")
        return Response(self.get_serializer(o).data, status=201)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        self.get_object()
        o = services.confirm_order(int(pk), request.user)
        audit(request, "confirm", "order", o.id, f"确认订单 {o.order_ref or '(负责人确认)'} 并生成业绩")
        return Response(self.get_serializer(o).data)

    @action(detail=True, methods=["post"], url_path="amount")
    def set_amount(self, request, pk=None):
        """负责人/商务补录或更正合同金额（财务确认时不录金额，D5）；同步订单生成的业绩金额。"""
        from apps.common.params import decimal_param
        from apps.performances.models import Performance

        if not can(request.user, "order.amount"):
            raise PermissionDenied("仅负责人或商务可维护合同金额")
        self.get_object()
        o = OrderConfirmation.objects.select_for_update().get(pk=pk)
        if o.status == "REVOKED":
            raise BusinessError("已撤销的确认记录不能修改金额", code="revoked")
        o.contract_amount = decimal_param(request.data.get("contract_amount"), "contract_amount", 16, 2, required=True)
        o.save(update_fields=["contract_amount"])
        Performance.objects.filter(order_confirmation=o, source="ORDER").exclude(status="REVOKED").update(internal_amount=o.contract_amount)
        audit(request, "set_amount", "order", o.id, "更新合同金额")
        return Response(self.get_serializer(o).data)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        if not can(request.user, "order.revoke"):
            raise PermissionDenied("仅部门负责人可撤销确认")
        self.get_object()
        o = services.revoke_order(int(pk), request.user, str_param(request.data.get("reason"), "reason", 300))
        audit(request, "revoke", "order", o.id, f"撤销确认：{o.revoke_reason}")
        return Response(self.get_serializer(o).data)
