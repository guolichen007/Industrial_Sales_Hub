from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditEvent
from apps.audit.service import audit
from apps.common.errors import BusinessError
from apps.common.params import str_param
from apps.common.roles import ADMIN, HasCap, effective_caps, role_of

from .models import User

LOGIN_MAX_FAILS = 8
LOGIN_WINDOW = timedelta(minutes=15)


def me_payload(user):
    role = role_of(user)
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "role": role,
        "role_label": user.get_role_display(),
        "caps": effective_caps(role),
        "flags": {
            "performance_confirm_mode": settings.PERFORMANCE_CONFIRM_MODE,
            "project_visibility": settings.PROJECT_VISIBILITY,
            "mode": settings.MODE,
            "https": settings.HTTPS_ENABLED,
        },
        "version": settings.APP_VERSION,
    }


def kill_sessions(user_id):
    """使某用户全部会话失效（停用、改密码、改角色时调用）。"""
    for sess in Session.objects.filter(expire_date__gt=timezone.now()):
        try:
            if str(sess.get_decoded().get("_auth_user_id")) == str(user_id):
                sess.delete()
        except Exception:  # noqa: BLE001 —— 损坏的会话直接删除
            sess.delete()


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class PublicInfoView(APIView):
    """未登录也可读取的系统信息（登录页显示模式、是否已初始化、HTTP 试用提示）。不含任何业务数据。"""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({
            "version": settings.APP_VERSION,
            "mode": settings.MODE,
            "https": settings.HTTPS_ENABLED,
            "initialized": User.objects.filter(role=ADMIN, is_active=True).exists(),
            "maintenance": settings.MAINTENANCE_FLAG.exists(),
        })


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    """登录。失败限速基于数据库审计记录（多线程/多进程一致），按“用户名”与“来源地址+用户名”双维度。

    RC2：登录也强制 CSRF（DRF 只对已认证会话检查 CSRF，未登录的登录请求原本豁免，存在“登录 CSRF”风险：
    第三方页面可把用户登录到攻击者账号）。前端每次写请求都会先获取 csrftoken 并携带 X-CSRFToken。"""

    permission_classes = [AllowAny]

    def post(self, request):
        username = str_param(request.data.get("username"), "username", 150)
        password = request.data.get("password")
        if not isinstance(password, str) or not username:
            raise BusinessError("请输入用户名和密码", code="invalid_credentials", http_status=400)
        ip = request.META.get("REMOTE_ADDR", "")
        since = timezone.now() - LOGIN_WINDOW
        fails = AuditEvent.objects.filter(action="login_failed", resource_id=username.lower()[:40], at__gte=since)
        if fails.filter(ip=ip).count() >= LOGIN_MAX_FAILS or fails.count() >= LOGIN_MAX_FAILS * 3:
            raise BusinessError("失败次数过多，请 15 分钟后再试，或联系管理员。", code="rate_limited", http_status=429)
        user = authenticate(request, username=username, password=password)
        if not user or not user.is_active:
            audit(request, "login_failed", "user", username.lower()[:40], "登录失败")
            raise BusinessError("用户名或密码错误", code="invalid_credentials", http_status=400)
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
        old, new = request.data.get("old_password"), request.data.get("new_password")
        if not isinstance(old, str) or not isinstance(new, str):
            raise BusinessError("请填写原密码和新密码", code="invalid_password", http_status=400)
        if not request.user.check_password(old):
            raise BusinessError("原密码不正确", code="invalid_password", http_status=400)
        try:
            validate_password(new, request.user)
        except DjangoValidationError as e:
            raise serializers.ValidationError({"new_password": list(e.messages)})
        request.user.set_password(new)
        request.user.save()
        update_session_auth_hash(request, request.user)
        audit(request, "password_change", "user", request.user.id, "修改密码")
        return Response({"ok": True})


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True, trim_whitespace=False)
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "display_name", "phone", "email", "role", "role_label", "is_active", "last_login", "password"]
        read_only_fields = ["last_login"]

    def validate(self, data):
        pwd = data.get("password")
        if pwd:
            probe = User(username=data.get("username") or (self.instance.username if self.instance else ""), display_name=data.get("display_name", ""), email=data.get("email", ""))
            try:
                validate_password(pwd, probe)
            except DjangoValidationError as e:
                raise serializers.ValidationError({"password": list(e.messages)})
        if self.instance and "username" in data and data["username"] != self.instance.username:
            raise serializers.ValidationError({"username": "用户名创建后不可修改"})
        return data

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
        # 固定锁顺序避免死锁：先按 id 顺序锁住全部启用管理员行，再锁目标行
        admins = list(User.objects.select_for_update().filter(role=ADMIN, is_active=True).order_by("id").values_list("id", flat=True))
        inst = User.objects.select_for_update().get(pk=s.instance.pk)
        new_role = s.validated_data.get("role", inst.role)
        new_active = s.validated_data.get("is_active", inst.is_active)
        if inst.role == ADMIN and inst.is_active and (new_role != ADMIN or not new_active):
            # 最后一个管理员保护
            if len(admins) <= 1:
                raise BusinessError("不能停用或降级最后一个管理员，请先创建另一个管理员。", code="last_admin")
        role_changed = new_role != inst.role
        s.instance = inst
        u = s.save()
        if not u.is_active or role_changed or s.validated_data.get("password"):
            kill_sessions(u.id)  # 停用、改角色、重置密码后立即失效
        changed = {k: v for k, v in s.validated_data.items() if k != "password"}
        if s.validated_data.get("password"):
            changed["password"] = "(已重置)"
        audit(self.request, "update", "user", u.id, f"修改账号 {u.username}", changed)


class UserOptionsView(APIView):
    """供负责人/成员下拉框使用，所有登录用户可读，仅返回姓名和角色。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response([{"id": u.id, "name": u.name, "role": u.role} for u in User.objects.filter(is_active=True)])


class SystemStatusView(APIView):
    """系统诊断（管理员/负责人）：版本、模式、数据库、备份状态、磁盘。不提供任何命令执行能力。"""

    permission_classes = [HasCap.of("system.status", read_cap="system.status")]

    def get(self, request):
        import json
        import shutil

        from django.db import connection

        with connection.cursor() as c:
            c.execute("SELECT version()" if connection.vendor == "postgresql" else "SELECT sqlite_version()")
            dbv = c.fetchone()[0]
        try:
            backup = json.loads(settings.BACKUP_STATUS_FILE.read_text(encoding="utf-8")) if settings.BACKUP_STATUS_FILE.exists() else None
        except (OSError, ValueError):
            backup = {"error": "备份状态文件无法读取"}
        du = shutil.disk_usage(settings.DATA_DIR)
        return Response({
            "version": settings.APP_VERSION,
            "mode": settings.MODE,
            "https": settings.HTTPS_ENABLED,
            "debug": settings.DEBUG,
            "database": {"vendor": connection.vendor, "version": dbv.split(",")[0]},
            "allowed_hosts": settings.ALLOWED_HOSTS,
            "project_visibility": settings.PROJECT_VISIBILITY,
            "performance_confirm_mode": settings.PERFORMANCE_CONFIRM_MODE,
            "maintenance": settings.MAINTENANCE_FLAG.exists(),
            "backup": backup,
            "disk": {"total_gb": round(du.total / 2**30, 1), "free_gb": round(du.free / 2**30, 1)},
            "users": {"total": User.objects.count(), "active": User.objects.filter(is_active=True).count(), "admins": User.objects.filter(role=ADMIN, is_active=True).count()},
        })


__all__ = ["transaction"]
