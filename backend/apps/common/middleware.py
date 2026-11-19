import json
import re
import uuid

from django.conf import settings
from django.http import HttpResponse

_RID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIdMiddleware:
    """为每个请求分配 request_id；只接受安全字符的外部 ID，避免日志注入。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = request.headers.get("X-Request-Id", "")
        request.request_id = rid if _RID.match(rid) else uuid.uuid4().hex[:16]
        response = self.get_response(request)
        response["X-Request-Id"] = request.request_id
        return response


class MaintenanceMiddleware:
    """备份/升级维护窗口：存在 DATA_DIR/MAINTENANCE 标志文件时拒绝写请求（503），读请求照常。

    备份脚本流程：创建标志 → 获取 PostgreSQL advisory lock（等待进行中的写事务结束）→ pg_dump + 文件快照 → 删除标志。
    写请求同时持有同一 advisory lock 的共享锁（见 apps.common.maintenance），保证不存在“标志创建前已进入”的写事务漏网。
    """

    SAFE = {"GET", "HEAD", "OPTIONS"}
    ALLOW_PATHS = ("/api/auth/login/", "/api/auth/logout/", "/api/auth/csrf/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in self.SAFE and request.path.startswith("/api/") and not request.path.startswith(self.ALLOW_PATHS):
            if settings.MAINTENANCE_FLAG.exists():
                body = {"code": "maintenance", "message": "系统正在备份或维护，暂时只读，请稍后重试。", "field_errors": {}, "request_id": getattr(request, "request_id", "")}
                resp = HttpResponse(json.dumps(body, ensure_ascii=False), status=503, content_type="application/json")
                resp["Retry-After"] = "60"
                return resp
        return self.get_response(request)


class SecurityHeadersMiddleware:
    CSP = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; object-src 'none'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self.CSP)
        response.setdefault("Referrer-Policy", "same-origin")
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.setdefault("X-ISH-Version", settings.APP_VERSION)
        if request.path.startswith("/api/"):
            response.setdefault("Cache-Control", "no-store")
        return response
