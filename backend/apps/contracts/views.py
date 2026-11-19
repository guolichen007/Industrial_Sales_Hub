from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.audit.service import audit
from apps.common.errors import BusinessError
from apps.common.roles import HasCap, can
from apps.projects.scope import scoped_projects

from . import services
from .models import OrderConfirmation


class OrderSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    legal_entity_name = serializers.CharField(source="legal_entity.short_name", read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True, default="")
    quote_code = serializers.CharField(source="quote.code", read_only=True, default="")
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    basis_label = serializers.CharField(source="get_basis_display", read_only=True)
    confirmed_by_name = serializers.CharField(source="confirmed_by.name", read_only=True, default="")
    performance_id = serializers.SerializerMethodField()

    class Meta:
        model = OrderConfirmation
        fields = ["id", "project", "project_name", "quote", "quote_code", "legal_entity", "legal_entity_name", "customer", "customer_name", "basis", "basis_label", "order_ref", "signed_on", "contract_amount", "product_summary", "reason", "status", "status_label", "idempotency_key", "confirmed_by_name", "confirmed_at", "revoke_reason", "revoked_at", "created_at", "performance_id"]
        read_only_fields = ["status", "confirmed_at", "revoke_reason", "revoked_at"]
        extra_kwargs = {"legal_entity": {"required": False}, "idempotency_key": {"validators": []}}
        validators = []  # 重复规则由业务层给出明确提示（duplicate_order）

    def get_performance_id(self, obj):
        p = getattr(obj, "performance", None)
        return p.id if p else None

    def validate(self, data):
        q = data.get("quote")
        if q and q.project_id != data["project"].id:
            raise serializers.ValidationError({"quote": "报价不属于该项目"})
        if q:
            data.setdefault("legal_entity", q.legal_entity)
            data.setdefault("customer", q.recipient)
        if not data.get("legal_entity"):
            raise serializers.ValidationError({"legal_entity": "请选择出具主体"})
        if data.get("contract_amount") is not None and data["contract_amount"] < 0:
            raise serializers.ValidationError({"contract_amount": "金额不能为负"})
        data["order_ref"] = (data.get("order_ref") or "").strip()
        return data


class OrderSafeSerializer(OrderSerializer):
    class Meta(OrderSerializer.Meta):
        fields = [f for f in OrderSerializer.Meta.fields if f != "contract_amount"]


def order_payload(o, user):
    return (OrderSerializer if can(user, "performance.see_amount") else OrderSafeSerializer)(o).data


class OrderViewSet(viewsets.ModelViewSet):
    permission_classes = [HasCap.of("order.confirm", read_cap="order.access")]
    http_method_names = ["get", "post"]

    def get_serializer_class(self):
        return OrderSerializer if can(self.request.user, "performance.see_amount") else OrderSafeSerializer

    def get_queryset(self):
        qs = OrderConfirmation.objects.select_related("project", "legal_entity", "customer", "quote", "confirmed_by").filter(project__in=scoped_projects(self.request.user))
        if p := self.request.query_params.get("project"):
            qs = qs.filter(project_id=p)
        if s := self.request.query_params.get("status"):
            qs = qs.filter(status=s)
        return qs

    def create(self, request, *a, **kw):
        key = (request.data.get("idempotency_key") or request.headers.get("Idempotency-Key") or "").strip()[:64] or None
        if key and (existing := OrderConfirmation.objects.filter(idempotency_key=key).first()):
            return Response(self.get_serializer(existing).data, status=200)
        s = self.get_serializer(data={**request.data, "idempotency_key": key})
        s.is_valid(raise_exception=True)
        if s.validated_data["order_ref"] and OrderConfirmation.objects.filter(legal_entity=s.validated_data["legal_entity"], order_ref=s.validated_data["order_ref"]).exclude(status="REVOKED").exists():
            raise BusinessError(f"合同/订单号 {s.validated_data['order_ref']} 已登记，禁止重复。", code="duplicate_order")
        o = s.save(created_by=request.user)
        audit(request, "create", "order", o.id, f"登记确认依据 {o.get_basis_display()} {o.order_ref}")
        if request.data.get("confirm_now"):
            o = services.confirm_order(o.id, request.user)
            audit(request, "confirm", "order", o.id, "确认订单并生成业绩")
        return Response(self.get_serializer(o).data, status=201)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        self.get_object()
        o = services.confirm_order(int(pk), request.user)
        audit(request, "confirm", "order", o.id, f"确认订单 {o.order_ref or '(负责人确认)'} 并生成业绩")
        return Response(self.get_serializer(o).data)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        if not can(request.user, "order.revoke"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("仅负责人/管理员可撤销确认")
        self.get_object()
        o = services.revoke_order(int(pk), request.user, str(request.data.get("reason", "")))
        audit(request, "revoke", "order", o.id, f"撤销确认：{o.revoke_reason}")
        return Response(self.get_serializer(o).data)
