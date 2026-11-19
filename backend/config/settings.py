"""工业销售管理系统 V2.0 — Django 配置。

环境变量（均可选，生产环境必须设置标注 * 的项）：
  DJANGO_SECRET_KEY *      会话签名密钥
  DJANGO_DEBUG             1/0，默认 1（本地试用）
  DJANGO_ALLOWED_HOSTS     逗号分隔
  DATABASE_URL             postgres://user:pass@host:5432/dbname；未设置时使用本地 SQLite 便于试用
  DATA_DIR                 私有数据目录（PDF、附件），默认 backend/data
  PDF_FONT_PATH            报价/案例 PDF 使用的 TrueType 中文字体（.ttf/.ttc）
  PERFORMANCE_CONFIRM_MODE OR / AND —— 甲方待确认事项 D3，默认 OR
  SALES_SEE_ALL_PROJECTS   1/0 —— 销售是否可查看全部项目，默认 0（仅自己/参与项目）
  SALES_QUOTE_SUMMARY      1/0 —— 甲方待确认事项 D2，销售是否可看无金额报价摘要，默认 0
"""
import os
from pathlib import Path
from urllib.parse import urlparse, unquote

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or (
    "dev-only-insecure-key-change-me" if DEBUG else None
)
if not SECRET_KEY:
    raise RuntimeError("生产环境必须设置 DJANGO_SECRET_KEY")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.accounts",
    "apps.audit",
    "apps.organizations",
    "apps.products",
    "apps.projects",
    "apps.quotations",
    "apps.contracts",
    "apps.performances",
    "apps.files",
    "apps.imports",
]

MIDDLEWARE = [
    "apps.common.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.common.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": False,
        "OPTIONS": {"context_processors": []},
    }
]


def _database_from_url(url):
    u = urlparse(url)
    if u.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError("DATABASE_URL 仅支持 postgres://")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(u.path.lstrip("/")),
        "USER": unquote(u.username or ""),
        "PASSWORD": unquote(u.password or ""),
        "HOST": u.hostname or "localhost",
        "PORT": str(u.port or 5432),
        "CONN_MAX_AGE": 60,
    }


DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
PRIVATE_STORAGE_DIR = DATA_DIR / "private"

if os.environ.get("DATABASE_URL"):
    DATABASES = {"default": _database_from_url(os.environ["DATABASE_URL"])}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": DATA_DIR / "db.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
    }

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
STATIC_ROOT = DATA_DIR / "static"
FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST", BASE_DIR.parent / "frontend" / "dist"))

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 10
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "SAMEORIGIN"  # 允许同源内嵌 PDF 预览
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 31536000
# 有意保留的部署检查项（见 docs/adr/ADR-003-部署与安全基线.md）：
#   W008 HTTP→HTTPS 跳转由 Nginx 完成；W019 SAMEORIGIN 用于系统内 PDF 预览；
#   W005/W021 子域名 HSTS 与 preload 会影响甲方整个现有域名，需甲方 IT 决定。
SILENCED_SYSTEM_CHECKS = ["security.W008", "security.W019", "security.W005", "security.W021"]

DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "EXCEPTION_HANDLER": "apps.common.errors.exception_handler",
    "COERCE_DECIMAL_TO_STRING": True,
    "UNAUTHENTICATED_USER": None,
}

# ---- 甲方待确认事项的可切换默认值（见 docs/decisions/G0-决策记录.md） ----
PERFORMANCE_CONFIRM_MODE = os.environ.get("PERFORMANCE_CONFIRM_MODE", "OR").upper()  # D3
SALES_SEE_ALL_PROJECTS = env_bool("SALES_SEE_ALL_PROJECTS", False)
SALES_QUOTE_SUMMARY = env_bool("SALES_QUOTE_SUMMARY", False)  # D2
PDF_FONT_PATH = os.environ.get("PDF_FONT_PATH", "")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO" if not DEBUG else "WARNING"},
}
