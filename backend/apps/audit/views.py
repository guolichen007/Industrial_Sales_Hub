from rest_framework import serializers, viewsets

from apps.common.pagination import StandardPagination
from apps.common.roles import HasCap

from .models import AuditEvent


class AuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = ["id", "actor_name", "action", "resource", "resource_id", "summary", "request_id", "at"]


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
