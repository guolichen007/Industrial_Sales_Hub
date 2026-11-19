"""工业销售管理系统 V2.1 — Django 配置。

运行模式 ISH_MODE（见 docs/adr/ADR-004-局域网部署与运行模式.md）：
  DEMO   本机演示（默认）。允许 DEBUG、SQLite、演示数据；ALLOWED_HOSTS 仅回环地址，局域网无法访问。
  LAN    正式局域网部署。禁止 DEBUG / 开发密钥 / 演示数据；必须 PostgreSQL；
         ISH_HTTPS=1 时启用 Secure Cookie 与 HSTS；ISH_HTTPS=0 为“隔离内网 HTTP 试用”（明示风险）。
  CLOUD  保留的云部署配置（Nginx + HTTPS），本期不验收。

配置来源：先读取 ISH_CONFIG_FILE 指向的 KEY=VALUE 文件（Windows 安装后为 config\app.env），
已存在的环境变量优先。

常用变量：
  DJANGO_SECRET_KEY        会话签名密钥（LAN/CLOUD 必填，≥50 字符）
  DJANGO_DEBUG             仅 DEMO 可为 1
  DJANGO_ALLOWED_HOSTS     LAN 必填：主机局域网 IP、计算机名、内网域名（逗号分隔）
  DATABASE_URL             postgres://user:pass@127.0.0.1:port/db（LAN 必填）
  DATA_DIR                 私有数据目录（PDF、附件、备份状态）
  ISH_HTTPS                LAN：1=经可信反向代理提供 HTTPS；0=HTTP 隔离试用
  ISH_TRUST_PROXY_HEADER   1=信任反向代理 X-Forwarded-Proto（仅当应用只监听回环、前面有代理时）
  ISH_PROJECT_VISIBILITY   ALL（默认，D7：销售可查看全团队项目公共进度）/ OWN
  PERFORMANCE_CONFIRM_MODE OR（D3 已确认）/ AND
  PDF_FONT_PATH / PDF_LATIN_FONT_PATH  报价 PDF 字体（默认使用仓库内置可分发字体）
"""
import os

from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
APP_VERSION = "2.1.0-rc1"
DEV_SECRET = "dev-only-insecure-key-change-me"


def _load_config_file():
    path = os.environ.get("ISH_CONFIG_FILE")
    if not path:
        return
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"配置文件不存在：{p}")
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_config_file()


def env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


class ConfigError(RuntimeError):
    pass


MODE = os.environ.get("ISH_MODE", "DEMO").strip().upper()
if MODE not in {"DEMO", "LAN", "CLOUD"}:
    raise ConfigError(f"ISH_MODE 只能是 DEMO / LAN / CLOUD，当前：{MODE}")

DEBUG = env_bool("DJANGO_DEBUG", MODE == "DEMO")
if DEBUG and MODE != "DEMO":
    raise ConfigError(f"{MODE} 模式禁止 DJANGO_DEBUG=1")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or (DEV_SECRET if MODE == "DEMO" else "")
if MODE != "DEMO" and (not SECRET_KEY or SECRET_KEY == DEV_SECRET or len(SECRET_KEY) < 50):
    raise ConfigError(f"{MODE} 模式必须设置不少于 50 字符的随机 DJANGO_SECRET_KEY（不得使用开发默认值）")

if MODE == "DEMO":
    # 演示模式仅回环可访问：即使有人把服务绑到 0.0.0.0，局域网请求也会因 Host 不匹配被拒绝
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "testserver"]
else:
    ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
    if not ALLOWED_HOSTS:
        raise ConfigError(f"{MODE} 模式必须设置 DJANGO_ALLOWED_HOSTS（主机局域网 IP / 计算机名 / 内网域名）")
    if "*" in ALLOWED_HOSTS:
        raise ConfigError("DJANGO_ALLOWED_HOSTS 不允许使用 *")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

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
    "apps.common.middleware.MaintenanceMiddleware",
    "apps.common.maintenance.WriteTransactionMiddleware",
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
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"connect_timeout": 10},
    }


DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
PRIVATE_STORAGE_DIR = DATA_DIR / "private"

if os.environ.get("DATABASE_URL"):
    DATABASES = {"default": _database_from_url(os.environ["DATABASE_URL"])}
elif MODE != "DEMO":
    raise ConfigError(f"{MODE} 模式必须使用 PostgreSQL（设置 DATABASE_URL）；SQLite 仅用于演示")
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
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "apps.accounts.validators.CharacterClassValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "/static/"
STATIC_ROOT = DATA_DIR / "static"
FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST", BASE_DIR.parent / "frontend" / "dist"))
MAINTENANCE_FLAG = DATA_DIR / "MAINTENANCE"
BACKUP_STATUS_FILE = DATA_DIR / "backup_status.json"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 10
SESSION_COOKIE_NAME = "ish_session"
CSRF_COOKIE_NAME = "csrftoken"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "SAMEORIGIN"  # 允许同源内嵌 PDF 预览
SECURE_REFERRER_POLICY = "same-origin"

HTTPS_ENABLED = {"DEMO": False, "LAN": env_bool("ISH_HTTPS", False), "CLOUD": True}[MODE]
TRUST_PROXY_HEADER = env_bool("ISH_TRUST_PROXY_HEADER", MODE == "CLOUD")
SESSION_COOKIE_SECURE = HTTPS_ENABLED
CSRF_COOKIE_SECURE = HTTPS_ENABLED
if TRUST_PROXY_HEADER:
    # 仅当应用只监听回环、所有请求都经可信反向代理时才启用，否则客户端可伪造该头
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if HTTPS_ENABLED:
    # 局域网：HSTS 仅 1 天、不含子域名、不 preload，避免影响甲方其他内网站点
    SECURE_HSTS_SECONDS = 31536000 if MODE == "CLOUD" else 86400
# HTTP 隔离试用（LAN 且 ISH_HTTPS=0）：不设置 Secure Cookie 与 HSTS，否则浏览器无法登录；
# 该状态在系统诊断与登录页明示“未加密传输”，不得作为正式安全基线（见 ADR-004）。
# 有意保留的部署检查项：W004/W008 HTTPS 由代理负责或处于 HTTP 试用；W019 SAMEORIGIN 用于 PDF 预览；
#   W005/W021 子域名 HSTS 与 preload 会影响甲方整个域名；W012/W016 在 HTTP 试用时无法启用。
SILENCED_SYSTEM_CHECKS = ["security.W004", "security.W008", "security.W019", "security.W005", "security.W021"]
if not HTTPS_ENABLED:
    SILENCED_SYSTEM_CHECKS += ["security.W012", "security.W016"]

DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "apps.common.parsers.ObjectJSONParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "EXCEPTION_HANDLER": "apps.common.errors.exception_handler",
    "COERCE_DECIMAL_TO_STRING": True,
    "UNAUTHENTICATED_USER": None,
}

# ---- 业务决策开关（见 docs/decisions/V2.1-LAN-决策记录.md） ----
PERFORMANCE_CONFIRM_MODE = os.environ.get("PERFORMANCE_CONFIRM_MODE", "OR").strip().upper()  # D3
if PERFORMANCE_CONFIRM_MODE not in {"OR", "AND"}:
    raise ConfigError("PERFORMANCE_CONFIRM_MODE 只能是 OR / AND")
PROJECT_VISIBILITY = os.environ.get("ISH_PROJECT_VISIBILITY", "ALL").strip().upper()  # D7
if PROJECT_VISIBILITY not in {"ALL", "OWN"}:
    raise ConfigError("ISH_PROJECT_VISIBILITY 只能是 ALL / OWN")
PDF_FONT_PATH = os.environ.get("PDF_FONT_PATH", "")
PDF_LATIN_FONT_PATH = os.environ.get("PDF_LATIN_FONT_PATH", "")
DEMO_SEED_ALLOWED = MODE == "DEMO"
ADVISORY_LOCK_KEY = 0x15A1E5  # 备份维护窗口使用的 PostgreSQL advisory lock 编号

LOG_DIR = Path(os.environ.get("ISH_LOG_DIR", DATA_DIR / "logs"))
_handlers = {"console": {"class": "logging.StreamHandler", "formatter": "plain"}}
if MODE != "DEMO":
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _handlers["file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str(LOG_DIR / "app.log"),
        "maxBytes": 10 * 1024 * 1024,
        "backupCount": 10,
        "encoding": "utf-8",
        "formatter": "plain",
    }
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": _handlers,
    "root": {"handlers": list(_handlers), "level": "WARNING" if DEBUG else "INFO"},
    "loggers": {"django.request": {"level": "ERROR"}},
}
