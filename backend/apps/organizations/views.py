from django.db.models import Count, Q
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.audit.service import audit
from apps.common.params import int_param, str_param
from apps.common.roles import HasCap, can

from .models import Account, Contact, LegalEntity, normalize_name


class LegalEntitySerializer(serializers.ModelSerializer):
    class Meta:
        model = LegalEntity
        fields = "__all__"


class LegalEntityViewSet(viewsets.ModelViewSet):
    queryset = LegalEntity.objects.all()
    serializer_class = LegalEntitySerializer
    permission_classes = [HasCap.of("legal_entity.write", read_cap="*")]
    http_method_names = ["get", "post", "patch"]

    def perform_update(self, s):
        e = s.save()
        audit(self.request, "update", "legal_entity", e.id, f"修改出具主体 {e.short_name}")


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = ["id", "account", "name", "department", "position", "mobile", "phone", "email", "remark"]


class AccountSerializer(serializers.ModelSerializer):
    contacts = ContactSerializer(many=True, read_only=True)
    project_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Account
        fields = ["id", "name", "kinds", "industry", "region", "address", "remark", "active", "contacts", "project_count", "created_at"]

    def validate_kinds(self, v):
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            raise serializers.ValidationError("业务身份应为文字列表")
        return list(dict.fromkeys(x.strip() for x in v))[:8]


class AccountViewSet(viewsets.ModelViewSet):
    serializer_class = AccountSerializer
    permission_classes = [HasCap.of("account.write", read_cap="account.read")]

    def get_queryset(self):
        qs = Account.objects.prefetch_related("contacts").annotate(project_count=Count("project_parties__project", distinct=True))
        p = self.request.query_params
        if q := p.get("q"):
            q = str_param(q, "q", 100)
            qs = qs.filter(Q(name__icontains=q) | Q(contacts__name__icontains=q) | Q(region__icontains=q)).distinct()
        if k := p.get("kind"):
            # JSON 包含查询在 SQLite/PG 间行为不同，这里用文本匹配保证兼容
            qs = qs.filter(kinds__icontains=k)
        if p.get("active") in ("0", "1"):
            qs = qs.filter(active=p["active"] == "1")
        return qs

    def destroy(self, request, *a, **kw):
        if not can(request.user, "account.delete"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("仅部门负责人可删除客户；被项目或报价引用的客户只能停用")
        obj = self.get_object()
        audit(request, "delete", "account", obj.id, f"删除客户 {obj.name}")
        return super().destroy(request, *a, **kw)

    def perform_create(self, s):
        a = s.save(created_by=self.request.user)
        audit(self.request, "create", "account", a.id, f"新建客户 {a.name}")

    def perform_update(self, s):
        a = s.save()
        audit(self.request, "update", "account", a.id, f"修改客户 {a.name}")

    @action(detail=False, methods=["get"], url_path="check-duplicate")
    def check_duplicate(self, request):
        name = str_param(request.query_params.get("name", ""), "name", 150)
        exclude = int_param(request.query_params.get("exclude"), "exclude")
        n = normalize_name(name)
        if len(n) < 2:
            return Response([])
        qs = Account.objects.filter(Q(normalized_name=n) | Q(normalized_name__contains=n) if len(n) >= 4 else Q(normalized_name=n))
        if exclude:
            qs = qs.exclude(pk=exclude)
        return Response([{"id": a.id, "name": a.name} for a in qs[:5]])

    @action(detail=True, methods=["get"])
    def projects(self, request, pk=None):
        from apps.projects.scope import visible_projects

        acc = self.get_object()
        qs = visible_projects(request.user).filter(parties__account=acc).distinct()
        return Response([
            {"id": p.id, "code": p.code, "name": p.name, "presale_phase": p.presale_phase, "execution_phase": p.execution_phase,
             "roles": sorted({x.party_role for x in p.parties.all() if x.account_id == acc.id})}
            for p in qs.prefetch_related("parties")
        ])


class ContactViewSet(viewsets.ModelViewSet):
    queryset = Contact.objects.select_related("account")
    serializer_class = ContactSerializer
    permission_classes = [HasCap.of("account.write", read_cap="account.read")]

    def get_queryset(self):
        qs = super().get_queryset()
        if (a := int_param(self.request.query_params.get("account"), "account")) is not None:
            qs = qs.filter(account_id=a)
        return qs

    def perform_create(self, s):
        c = s.save()
        audit(self.request, "create", "contact", c.id, f"新增联系人 {c.name}（{c.account.name}）")
