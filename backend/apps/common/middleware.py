import uuid


class RequestIdMiddleware:
    """为每个请求分配 request_id，写入审计与错误响应。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
        request.request_id = rid[:64]
        response = self.get_response(request)
        response["X-Request-Id"] = request.request_id
        return response


class SecurityHeadersMiddleware:
    CSP = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; object-src 'none'; frame-ancestors 'self'; base-uri 'self'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self.CSP)
        response.setdefault("Referrer-Policy", "same-origin")
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.path.startswith("/api/"):
            response.setdefault("Cache-Control", "no-store")
        return response
