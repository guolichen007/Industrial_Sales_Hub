"""RC2-R4 本地 TLS：HTTPS 反向代理模式的安全设置（子进程加载 settings，验证真实配置结果）。

- ISH_HTTPS=1：Secure 会话/CSRF Cookie、HSTS（局域网 1 天、不含子域名、不 preload）、COOP；
- 只有 ISH_TRUST_PROXY_HEADER=1 时才信任 X-Forwarded-Proto（应用只监听回环、前置代理时）；
- HTTP 隔离试用（ISH_HTTPS=0）：Cookie 非 Secure（能登录的前提），且不信任代理头；
- LAN 下不允许 DEBUG、ALLOWED_HOSTS='*'；
- 经代理（X-Forwarded-Proto: https）时，CSRF 只接受 CSRF_TRUSTED_ORIGINS 中的 https 来源。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PROBE = r"""
import json, django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
from django.conf import settings
django.setup()
print("JSON=" + json.dumps({
  "https": settings.HTTPS_ENABLED, "session_secure": settings.SESSION_COOKIE_SECURE, "csrf_secure": settings.CSRF_COOKIE_SECURE,
  "hsts": getattr(settings, "SECURE_HSTS_SECONDS", 0), "hsts_sub": getattr(settings, "SECURE_HSTS_INCLUDE_SUBDOMAINS", False),
  "hsts_preload": getattr(settings, "SECURE_HSTS_PRELOAD", False), "proxy_header": getattr(settings, "SECURE_PROXY_SSL_HEADER", None),
  "coop": settings.SECURE_CROSS_ORIGIN_OPENER_POLICY, "debug": settings.DEBUG, "trusted": settings.CSRF_TRUSTED_ORIGINS}))
"""


def _settings(tmp_path, **env):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("ISH_", "DJANGO_"))}
    e.update({"ISH_MODE": "LAN", "DJANGO_SECRET_KEY": "x" * 60, "DJANGO_ALLOWED_HOSTS": "sales.example.internal,10.0.0.5", "DATA_DIR": str(tmp_path),
              "DATABASE_URL": os.environ.get("DATABASE_URL", "postgres://u:p@127.0.0.1:5432/x")})
    e.update(env)
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=str(BACKEND), env=e, capture_output=True, text=True)
    if r.returncode != 0:
        return {"error": (r.stderr or r.stdout)[-400:]}
    return json.loads(r.stdout.split("JSON=", 1)[1])


def test_https_proxy_mode_sets_secure_cookies_and_trusts_proxy_only_when_told(tmp_path):
    s = _settings(tmp_path, ISH_HTTPS="1", ISH_TRUST_PROXY_HEADER="1", DJANGO_CSRF_TRUSTED_ORIGINS="https://sales.example.internal")
    assert s["https"] and s["session_secure"] and s["csrf_secure"]
    assert s["hsts"] == 86400 and not s["hsts_sub"] and not s["hsts_preload"]
    assert s["proxy_header"] == ["HTTP_X_FORWARDED_PROTO", "https"]
    assert s["coop"] == "same-origin" and s["trusted"] == ["https://sales.example.internal"]
    s2 = _settings(tmp_path, ISH_HTTPS="1")
    assert s2["proxy_header"] is None  # 未声明前置代理时不信任客户端可伪造的头


def test_http_trial_mode_is_explicit_and_does_not_trust_proxy(tmp_path):
    s = _settings(tmp_path, ISH_HTTPS="0")
    assert not s["https"] and not s["session_secure"] and s["proxy_header"] is None and not s["debug"]


@pytest.mark.parametrize("env,msg", [({"DJANGO_DEBUG": "1"}, "DEBUG"), ({"DJANGO_ALLOWED_HOSTS": "*"}, "*"), ({"DJANGO_SECRET_KEY": "short"}, "SECRET_KEY")])
def test_lan_refuses_unsafe_settings(tmp_path, env, msg):
    assert msg in _settings(tmp_path, **env).get("error", "")


@pytest.mark.django_db
def test_csrf_behind_proxy_only_accepts_trusted_https_origin(settings, users):
    from django.test import Client

    settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    settings.CSRF_TRUSTED_ORIGINS = ["https://sales.example.internal"]
    settings.ALLOWED_HOSTS = ["sales.example.internal"]
    settings.SESSION_COOKIE_SECURE = settings.CSRF_COOKIE_SECURE = True
    c = Client(enforce_csrf_checks=True)
    r = c.get("/api/auth/csrf/", HTTP_HOST="sales.example.internal", HTTP_X_FORWARDED_PROTO="https")
    assert "secure" in str(r.cookies.get("csrftoken").output()).lower()
    tok = r.cookies["csrftoken"].value
    body = json.dumps({"username": "leader", "password": "Test@12345"})
    ok = c.post("/api/auth/login/", body, content_type="application/json", HTTP_HOST="sales.example.internal", HTTP_X_FORWARDED_PROTO="https",
                HTTP_X_CSRFTOKEN=tok, HTTP_ORIGIN="https://sales.example.internal", HTTP_REFERER="https://sales.example.internal/")
    assert ok.status_code == 200, ok.content
    assert "secure" in str(ok.cookies.get("ish_session").output()).lower()
    c2 = Client(enforce_csrf_checks=True)
    c2.get("/api/auth/csrf/", HTTP_HOST="sales.example.internal", HTTP_X_FORWARDED_PROTO="https")
    bad = c2.post("/api/auth/login/", body, content_type="application/json", HTTP_HOST="sales.example.internal", HTTP_X_FORWARDED_PROTO="https",
                  HTTP_X_CSRFTOKEN=c2.cookies["csrftoken"].value, HTTP_ORIGIN="https://evil.example", HTTP_REFERER="https://evil.example/")
    assert bad.status_code == 403
