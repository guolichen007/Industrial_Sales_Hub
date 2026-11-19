from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.service import audit
from apps.common.errors import BusinessError
from apps.common.roles import CAP, HasCap, role_of
from django.conf import settings

from .models import User


def me_payload(user):
    role = role_of(user)
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "role": role,
        "role_label": user.get_role_display(),
        "caps": sorted(k for k, roles in CAP.items() if role in roles),
        "flags": {
            "sales_quote_summary": settings.SALES_QUOTE_SUMMARY,
            "performance_confirm_mode": settings.PERFORMANCE_CONFIRM_MODE,
            "sales_see_all_projects": settings.SALES_SEE_ALL_PROJECTS,
        },
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class LoginView(APIView):
    permission_classes = [AllowAny]
    MAX_FAILS = 8

    def post(self, request):
        username = str(request.data.get("username", "")).strip()[:150]
        password = str(request.data.get("password", ""))
        ip = request.META.get("REMOTE_ADDR", "")
        key = f"login-fail:{ip}:{username.lower()}"
        if cache.get(key, 0) >= self.MAX_FAILS:
            raise BusinessError("失败次数过多，请 15 分钟后再试。", code="rate_limited", http_status=429)
        user = authenticate(request, username=username, password=password)
        if not user or not user.is_active:
            cache.set(key, cache.get(key, 0) + 1, 900)
            audit(request, "login_failed", "user", "", f"登录失败：{username}")
            raise BusinessError("用户名或密码错误", code="invalid_credentials", http_status=400)
        cache.delete(key)
        login(request, user)
        audit(request, "login", "user", user.id, "登录")
        return Response(me_payload(user))


class LogoutView(APIView):
    def post(self, request):
        audit(request, "logout", "user", request.user.id, "退出")
        logout(request)
        return Response({"ok": True})


class MeView(APIView):
    def get(self, request):
        return Response(me_payload(request.user))


class ChangePasswordView(APIView):
    def post(self, request):
        old, new = request.data.get("old_password", ""), request.data.get("new_password", "")
        if not request.user.check_password(old):
            raise BusinessError("原密码不正确", code="invalid_password", http_status=400)
        try:
            validate_password(new, request.user)
        except Exception as e:  # noqa: BLE001
            raise serializers.ValidationError({"new_password": list(getattr(e, "messages", [str(e)]))})
        request.user.set_password(new)
        request.user.save()
        update_session_auth_hash(request, request.user)
        audit(request, "password_change", "user", request.user.id, "修改密码")
        return Response({"ok": True})


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "display_name", "phone", "email", "role", "role_label", "is_active", "last_login", "password"]
        read_only_fields = ["last_login"]

    def validate_password(self, v):
        if v:
            validate_password(v)
        return v

    def create(self, data):
        pwd = data.pop("password", None)
        if not pwd:
            raise serializers.ValidationError({"password": "新建账号必须设置初始密码"})
        u = User(**data)
        u.set_password(pwd)
        u.save()
        return u

    def update(self, inst, data):
        pwd = data.pop("password", None)
        for k, v in data.items():
            setattr(inst, k, v)
        if pwd:
            inst.set_password(pwd)
        inst.save()
        return inst


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [HasCap.of("user.manage")]
    http_method_names = ["get", "post", "patch"]

    def perform_create(self, s):
        u = s.save()
        audit(self.request, "create", "user", u.id, f"新建账号 {u.username}（{u.get_role_display()}）")

    def perform_update(self, s):
        u = s.save()
        if not u.is_active:
            # 停用后立即失效：删除其所有会话
            from django.contrib.sessions.models import Session

            for sess in Session.objects.all():
                if str(sess.get_decoded().get("_auth_user_id")) == str(u.id):
                    sess.delete()
        audit(self.request, "update", "user", u.id, f"修改账号 {u.username}", {k: v for k, v in s.validated_data.items()})


class UserOptionsView(APIView):
    """供负责人/成员下拉框使用，所有登录用户可读，仅返回姓名和角色。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response([{"id": u.id, "name": u.name, "role": u.role} for u in User.objects.filter(is_active=True)])
