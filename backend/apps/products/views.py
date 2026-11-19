from django.db.models import Q
from rest_framework import serializers, viewsets

from apps.audit.service import audit
from apps.common.roles import HasCap, can

from .models import ParameterDefinition, Product, ProductCategory


class ParameterDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ParameterDefinition
        fields = ["id", "category", "key", "label", "unit", "sort"]


class CategorySerializer(serializers.ModelSerializer):
    parameters = ParameterDefinitionSerializer(many=True, read_only=True)

    class Meta:
        model = ProductCategory
        fields = ["id", "name", "sort", "parameters"]


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Product
        fields = ["id", "sku", "model", "name", "category", "category_name", "brand", "drawing_no", "unit", "tax_rate", "base_price", "technical_desc", "params", "active", "updated_at"]

    def validate_params(self, v):
        if not isinstance(v, list):
            raise serializers.ValidationError("参数应为列表")
        out, seen = [], set()
        for i, p in enumerate(v):
            if not isinstance(p, dict) or not str(p.get("label", "")).strip():
                raise serializers.ValidationError(f"第 {i + 1} 个参数缺少名称")
            key = str(p.get("key") or f"p{i + 1}").strip()[:40]
            if key in seen:
                key = f"{key}_{i + 1}"
            seen.add(key)
            out.append({"key": key, "label": str(p["label"]).strip()[:40], "value": str(p.get("value", "")).strip()[:120], "unit": str(p.get("unit", "")).strip()[:20]})
        return out

    def validate_tax_rate(self, v):
        if not (0 <= v < 1):
            raise serializers.ValidationError("税率应在 0～1 之间，例如 0.13")
        return v

    def validate_base_price(self, v):
        if v < 0:
            raise serializers.ValidationError("价格不能为负")
        return v


class ProductPublicSerializer(ProductSerializer):
    """无报价权限的角色看到的产品信息：不含参考价格。"""

    class Meta(ProductSerializer.Meta):
        fields = [f for f in ProductSerializer.Meta.fields if f != "base_price"]


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = ProductCategory.objects.prefetch_related("parameters")
    serializer_class = CategorySerializer
    permission_classes = [HasCap.of("product.write", read_cap="*")]


class ParameterDefinitionViewSet(viewsets.ModelViewSet):
    queryset = ParameterDefinition.objects.all()
    serializer_class = ParameterDefinitionSerializer
    permission_classes = [HasCap.of("product.write", read_cap="*")]


class ProductViewSet(viewsets.ModelViewSet):
    permission_classes = [HasCap.of("product.write", read_cap="*")]
    http_method_names = ["get", "post", "patch"]  # 不提供删除：停用代替，保证历史报价可追溯

    def get_serializer_class(self):
        return ProductSerializer if can(self.request.user, "product.see_price") else ProductPublicSerializer

    def get_queryset(self):
        qs = Product.objects.select_related("category")
        p = self.request.query_params
        if q := p.get("q"):
            qs = qs.filter(Q(sku__icontains=q) | Q(model__icontains=q) | Q(name__icontains=q) | Q(brand__icontains=q))
        if c := p.get("category"):
            qs = qs.filter(category_id=c)
        if p.get("active") in ("0", "1"):
            qs = qs.filter(active=p["active"] == "1")
        return qs

    def perform_create(self, s):
        o = s.save(created_by=self.request.user)
        audit(self.request, "create", "product", o.id, f"新增产品 {o.sku}")

    def perform_update(self, s):
        o = s.save()
        audit(self.request, "update", "product", o.id, f"修改产品 {o.sku}", {k: str(v) for k, v in s.validated_data.items() if k != "params"})
