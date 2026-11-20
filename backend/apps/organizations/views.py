from django.db.models import Count, Q
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.audit.service import audit
from apps.common.params import int_param, str_param
from apps.common.roles import HasCap, can

from .models import Account, AccountCategory, Contact, LegalEntity, normalize_name


class LegalEntitySerializer(serializers.ModelSerializer):
    signoff = serializers.SerializerMethodField()
    template_version = serializers.SerializerMethodField()

    class Meta:
        model = LegalEntity
        exclude = ["template_verified"]
        read_only_fields = ["logo_key", "logo_sha256"]

    def get_template_version(self, obj):
        from apps.quotations.pdf import TEMPLATE_VERSION

        return TEMPLATE_VERSION

    def get_signoff(self, obj):
        so = obj.current_signoff()
        if not so:
            return None
        return {"id": so.id, "template_key": so.template_key, "template_version": so.template_version, "signed_by": so.signed_by.name, "signed_at": so.signed_at, "note": so.note}


class LegalEntityPublicSerializer(serializers.ModelSerializer):
    """无报价/配置权限的角色只看到公司名称与模板标识（用于订单登记、案例署名下拉）。"""

    class Meta:
        model = LegalEntity
        fields = ["id", "code", "name", "short_name", "template_key", "active"]


class LegalEntityViewSet(viewsets.ModelViewSet):
    queryset = LegalEntity.objects.all()

    def get_serializer_class(self):
        return LegalEntitySerializer if can(self.request.user, "legal_entity.write") else LegalEntityPublicSerializer
    permission_classes = [HasCap.of("legal_entity.write", read_cap="*")]
    http_method_names = ["get", "post", "patch"]

    def perform_update(self, s):
        before = s.instance.signoff_config_sha256()
        e = s.save()
        changed = e.signoff_config_sha256() != before
        audit(self.request, "update", "legal_entity", e.id, f"修改出具主体 {e.short_name}" + ("（关键模板配置变化：原签收失效，需负责人重新签收）" if changed else ""))

    @action(detail=True, methods=["post"])
    def signoff(self, request, pk=None):
        """负责人签收当前模板版本 + 当前主体关键配置。仅 LEADER；必须填写签收说明（核对了哪些样张/黄金用例）。"""
        from apps.common.errors import BusinessError
        from apps.quotations.pdf import TEMPLATE_VERSION

        from .models import TemplateSignoff

        if not can(request.user, "template.signoff"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("仅部门负责人可签收报价模板")
        e = LegalEntity.objects.select_for_update().get(pk=self.get_object().pk)
        note = str_param(request.data.get("note"), "note", 300, required=True)
        if len(note.strip()) < 6:
            raise BusinessError("请写明签收依据（例如：已与 2026-11 真实样张逐项比对、财务 10 组黄金用例一致）", code="note_required")
        if e.current_signoff(TEMPLATE_VERSION):
            raise BusinessError("当前模板版本与配置已签收", code="already_signed")
        so = TemplateSignoff.objects.create(entity=e, template_key=e.template_key, template_version=TEMPLATE_VERSION, config_sha256=e.signoff_config_sha256(),
                                            config={k: getattr(e, k) for k in e.SIGNOFF_FIELDS}, note=note, signed_by=request.user)
        audit(request, "signoff", "legal_entity", e.id, f"签收报价模板 {e.short_name} · {e.template_key}/{TEMPLATE_VERSION}", {"signoff_id": so.id, "note": note})
        return Response(LegalEntitySerializer(e, context={"request": request}).data, status=201)

    @action(detail=True, methods=["post"], url_path="signoff/revoke")
    def revoke_signoff(self, request, pk=None):
        from django.utils import timezone

        from apps.common.errors import BusinessError

        if not can(request.user, "template.signoff"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("仅部门负责人可撤销签收")
        e = self.get_object()
        e = LegalEntity.objects.select_for_update().get(pk=e.pk)
        so = e.current_signoff()
        if not so:
            raise BusinessError("当前没有有效签收", code="not_signed")
        so.revoked_by, so.revoked_at, so.revoke_reason = request.user, timezone.now(), str_param(request.data.get("reason"), "reason", 300) or ""
        so.save()
        audit(request, "signoff_revoke", "legal_entity", e.id, f"撤销报价模板签收 {e.short_name}", {"signoff_id": so.id})
        return Response(LegalEntitySerializer(e, context={"request": request}).data)

    @action(detail=True, methods=["get"], url_path="signoffs")
    def signoffs(self, request, pk=None):
        if not can(request.user, "legal_entity.write"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied()
        e = self.get_object()
        return Response([{"id": x.id, "template_key": x.template_key, "template_version": x.template_version, "note": x.note, "signed_by": x.signed_by.name, "signed_at": x.signed_at,
                          "revoked_at": x.revoked_at, "revoke_reason": x.revoke_reason, "config": x.config} for x in e.signoffs.select_related("signed_by")])

    @action(detail=True, methods=["post"], url_path="logo")
    def logo(self, request, pk=None):
        """上传主体 Logo（PNG/JPG，≤1 MB）；用 Pillow 重新编码为 PNG（去除元数据）。更换 Logo 会使签收失效。"""
        import io

        from apps.common.errors import BusinessError
        from apps.common.pdfkit import sha256_bytes, store_private

        if not can(request.user, "legal_entity.write"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied()
        e = self.get_object()
        f = request.FILES.get("file")
        if not f or f.size > 1024 * 1024:
            raise BusinessError("请选择不超过 1 MB 的 PNG/JPG 图片", code="bad_logo", http_status=400)
        try:
            from PIL import Image

            im = Image.open(f)
            if im.format not in ("PNG", "JPEG"):
                raise ValueError(im.format)
            im.load()
            out = io.BytesIO()
            (im.convert("RGBA") if im.mode in ("P", "LA") else im).save(out, "PNG")
            data = out.getvalue()
        except Exception:  # noqa: BLE001
            raise BusinessError("图片无法识别，请使用 PNG 或 JPG", code="bad_logo", http_status=400)
        e.logo_key, e.logo_sha256 = store_private("logos", data, ".png"), sha256_bytes(data)
        e.save(update_fields=["logo_key", "logo_sha256"])
        audit(request, "update", "legal_entity", e.id, f"更换出具主体 Logo {e.short_name}（签收需重新确认）")
        return Response(LegalEntitySerializer(e, context={"request": request}).data)

    @action(detail=True, methods=["get"], url_path="logo/image")
    def logo_image(self, request, pk=None):
        from django.http import HttpResponse

        from apps.common.pdfkit import read_private

        e = self.get_object()
        if not e.logo_key or not can(request.user, "legal_entity.write"):
            from rest_framework.exceptions import NotFound

            raise NotFound()
        return HttpResponse(read_private(e.logo_key), content_type="image/png")


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = ["id", "account", "name", "department", "position", "mobile", "phone", "email", "remark"]


class AccountCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountCategory
        fields = ["id", "name", "sort", "active"]


class AccountCategoryViewSet(viewsets.ModelViewSet):
    """客户类别字典：业务角色可读，负责人/商务维护（RC2，原型“可编辑客户分类”）。"""

    queryset = AccountCategory.objects.all()
    serializer_class = AccountCategorySerializer
    permission_classes = [HasCap.of("account.category", read_cap="account.read")]
    http_method_names = ["get", "post", "patch"]

    def perform_create(self, s):
        o = s.save()
        audit(self.request, "create", "account_category", o.id, f"新增客户类别 {o.name}")

    def perform_update(self, s):
        o = s.save()
        audit(self.request, "update", "account_category", o.id, f"修改客户类别 {o.name}")


class AccountSerializer(serializers.ModelSerializer):
    contacts = ContactSerializer(many=True, read_only=True)
    project_count = serializers.IntegerField(read_only=True)
    categories = serializers.PrimaryKeyRelatedField(many=True, queryset=AccountCategory.objects.filter(active=True), required=False)
    category_names = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = ["id", "name", "kinds", "categories", "category_names", "industry", "region", "address", "remark", "active", "contacts", "project_count", "created_at"]

    def get_category_names(self, obj):
        return [c.name for c in obj.categories.all()]

    def validate_kinds(self, v):
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            raise serializers.ValidationError("业务身份应为文字列表")
        return list(dict.fromkeys(x.strip() for x in v))[:8]


class AccountViewSet(viewsets.ModelViewSet):
    serializer_class = AccountSerializer
    permission_classes = [HasCap.of("account.write", read_cap="account.read")]

    def get_queryset(self):
        from apps.projects.scope import visible_projects

        vis = visible_projects(self.request.user)
        qs = Account.objects.prefetch_related("contacts").annotate(project_count=Count("project_parties__project", filter=Q(project_parties__project__in=vis), distinct=True))
        if self.action != "list":
            return qs  # 列表筛选参数（q/kind/category…）只作用于列表；详情与子资源（如 projects?q=）不受影响
        p = self.request.query_params
        if q := p.get("q"):
            q = str_param(q, "q", 100)
            qs = qs.filter(Q(name__icontains=q) | Q(contacts__name__icontains=q) | Q(region__icontains=q)).distinct()
        if k := p.get("kind"):
            # JSON 包含查询在 SQLite/PG 间行为不同，这里用文本匹配保证兼容
            qs = qs.filter(kinds__icontains=k)
        if p.get("active") in ("0", "1"):
            qs = qs.filter(active=p["active"] == "1")
        if (cat := int_param(p.get("category"), "category")) is not None:
            qs = qs.filter(categories=cat)
        return qs.prefetch_related("categories")

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
        qs = visible_projects(request.user).filter(parties__account=acc).distinct().select_related("sales_owner")
        p = request.query_params
        if v := p.get("presale_phase"):
            qs = qs.filter(presale_phase=str_param(v, "presale_phase", 20))
        if v := p.get("execution_phase"):
            qs = qs.filter(execution_phase=str_param(v, "execution_phase", 20))
        if v := p.get("role"):
            qs = qs.filter(parties__account=acc, parties__party_role=str_param(v, "role", 20))
        if v := p.get("q"):
            v = str_param(v, "q", 100)
            qs = qs.filter(Q(name__icontains=v) | Q(code__icontains=v))
        return Response([
            {"id": x.id, "code": x.code, "name": x.name, "presale_phase": x.presale_phase, "execution_phase": x.execution_phase,
             "sales_owner_name": x.sales_owner.name if x.sales_owner else "", "updated_at": x.updated_at,
             "roles": sorted({y.party_role for y in x.parties.all() if y.account_id == acc.id})}
            for x in qs.prefetch_related("parties").order_by("-updated_at")
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

    def destroy(self, request, *a, **kw):
        if not can(request.user, "account.delete"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("仅部门负责人可删除联系人")
        obj = self.get_object()
        audit(request, "delete", "contact", obj.id, f"删除联系人 {obj.name}")
        return super().destroy(request, *a, **kw)
