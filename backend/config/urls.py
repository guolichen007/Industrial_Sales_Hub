from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.urls import include, path, re_path
from rest_framework.routers import DefaultRouter

from apps.accounts import views as acc
from apps.audit.views import AuditViewSet
from apps.common.dashboard import DashboardView
from apps.contracts.views import OrderViewSet
from apps.files import views as files
from apps.imports import views as imports
from apps.organizations import views as org
from apps.performances import views as perf
from apps.products import views as prod
from apps.projects.views import ProjectViewSet
from apps.quotations import views as quo

router = DefaultRouter(trailing_slash=True)
router.register("users", acc.UserViewSet, basename="user")
router.register("legal-entities", org.LegalEntityViewSet, basename="legal-entity")
router.register("accounts", org.AccountViewSet, basename="account")
router.register("contacts", org.ContactViewSet, basename="contact")
router.register("product-categories", prod.CategoryViewSet, basename="category")
router.register("parameter-definitions", prod.ParameterDefinitionViewSet, basename="paramdef")
router.register("products", prod.ProductViewSet, basename="product")
router.register("projects", ProjectViewSet, basename="project")
router.register("quotations", quo.QuoteViewSet, basename="quotation")
router.register("quote-revisions", quo.RevisionViewSet, basename="quote-revision")
router.register("order-confirmations", OrderViewSet, basename="order")
router.register("performances", perf.PerformanceViewSet, basename="performance")
router.register("case-releases", perf.CaseReleaseViewSet, basename="case-release")
router.register("audit-events", AuditViewSet, basename="audit")


def health(_request):
    from django.db import connection

    with connection.cursor() as c:
        c.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


def spa(request, path=""):
    """生产环境由 Nginx 提供静态文件；此视图用于单机试用时直接托管前端构建产物。"""
    dist = settings.FRONTEND_DIST
    target = (dist / path).resolve() if path else None
    if target and target.is_file() and dist.resolve() in target.parents:
        return FileResponse(open(target, "rb"))
    index = dist / "index.html"
    if not index.exists():
        raise Http404("前端尚未构建：请在 frontend 目录执行 npm run build")
    return FileResponse(open(index, "rb"), content_type="text/html")


api = [
    path("auth/csrf/", acc.CsrfView.as_view()),
    path("auth/login/", acc.LoginView.as_view()),
    path("auth/logout/", acc.LogoutView.as_view()),
    path("auth/me/", acc.MeView.as_view()),
    path("auth/password/", acc.ChangePasswordView.as_view()),
    path("user-options/", acc.UserOptionsView.as_view()),
    path("dashboard/", DashboardView.as_view()),
    path("case-preview/", perf.CasePreviewView.as_view()),
    path("attachments/", files.AttachmentListView.as_view()),
    path("attachments/<int:pk>/download/", files.AttachmentDownloadView.as_view()),
    path("import-batches/template/<str:kind>/", imports.TemplateView.as_view()),
    path("import-batches/preview/", imports.PreviewView.as_view()),
    path("import-batches/<int:pk>/commit/", imports.CommitView.as_view()),
    path("", include(router.urls)),
]

urlpatterns = [
    path("api/", include(api)),
    path("healthz", health),
    re_path(r"^(?!api/)(?P<path>.*)$", spa),
]
