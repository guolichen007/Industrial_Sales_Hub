from rest_framework import serializers, viewsets

from apps.common.pagination import StandardPagination
from apps.common.roles import HasCap

from .models import AuditEvent


TECH_RESOURCES = {"user", "legal_entity"}


class AuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = ["id", "actor_name", "action", "resource", "resource_id", "summary", "request_id", "at"]

    def to_representation(self, obj):
        d = super().to_representation(obj)
        from apps.common.roles import ADMIN, role_of

        if role_of(self.context["request"].user) == ADMIN and obj.resource not in TECH_RESOURCES:
            d["summary"] = "（业务操作，摘要仅负责人可见）"  # 管理员为技术角色：不显示项目/客户/报价等业务内容
        return d


class AuditViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditSerializer
    permission_classes = [HasCap.of("audit.read")]
    pagination_class = StandardPagination

    def get_queryset(self):
        qs = AuditEvent.objects.all()
        if r := self.request.query_params.get("resource"):
            qs = qs.filter(resource=r)
        if rid := self.request.query_params.get("resource_id"):
            qs = qs.filter(resource_id=rid)
        return qs
