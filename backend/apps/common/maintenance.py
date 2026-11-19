"""写请求与备份的互斥：PostgreSQL advisory lock。

- 每个写请求在事务内获取共享锁 pg_advisory_xact_lock_shared(key)；
- 备份脚本先放置 MAINTENANCE 标志（新写请求被中间件 503 拒绝），再获取排他锁 pg_advisory_lock(key)：
  这会等待所有已经进入的写事务提交，然后在同一时刻开始 pg_dump 与私有文件快照。
SQLite（仅演示）下为空操作。
"""
from django.conf import settings
from django.db import connection


class WriteTransactionMiddleware:
    """所有 /api/ 写请求：单一数据库事务 + 共享 advisory lock。

    - 响应状态 ≥ 400（含业务错误与未处理异常转换的 500）时整体回滚，不留下半状态；
    - 视图可设置 response._ish_commit = True 强制提交（例如记录 PDF 生成失败原因）；
    - 登录/登出/CSRF 不包在事务中（登录失败审计必须保留）。
    """

    SAFE = {"GET", "HEAD", "OPTIONS"}
    EXEMPT = ("/api/auth/login/", "/api/auth/logout/", "/api/auth/csrf/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.db import transaction

        if request.method in self.SAFE or not request.path.startswith("/api/") or request.path.startswith(self.EXEMPT):
            return self.get_response(request)
        with transaction.atomic():
            if connection.vendor == "postgresql":
                with connection.cursor() as c:
                    c.execute("SET LOCAL lock_timeout = '20s'")
                    c.execute("SELECT pg_advisory_xact_lock_shared(%s)", [settings.ADVISORY_LOCK_KEY])
            response = self.get_response(request)
            if response.status_code >= 400 and not getattr(response, "_ish_commit", False):
                transaction.set_rollback(True)
        return response
