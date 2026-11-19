"""统一错误响应：{code, message, field_errors, request_id}"""
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, OperationalError
from django.db.models import ProtectedError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_handler


class BusinessError(exceptions.APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "business_rule"
    default_detail = "违反业务规则"

    def __init__(self, message, code="business_rule", http_status=None):
        super().__init__(detail=message, code=code)
        self.biz_code = code
        if http_status:
            self.status_code = http_status


def _flatten(detail):
    if isinstance(detail, list):
        return "；".join(str(_flatten(d)) for d in detail)
    if isinstance(detail, dict):
        return "；".join(f"{k}: {_flatten(v)}" for k, v in detail.items())
    return str(detail)


def exception_handler(exc, context):
    request = context.get("request")
    rid = getattr(request, "request_id", None) if request is not None else None
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(exc.message_dict if hasattr(exc, "error_dict") else exc.messages)
    if isinstance(exc, FileNotFoundError):
        exc = BusinessError("文件不存在或已丢失，请联系管理员检查备份。", code="file_missing", http_status=410)
    if isinstance(exc, OperationalError) and ("could not obtain lock" in str(exc).lower() or "lock timeout" in str(exc).lower()):
        exc = BusinessError("记录正被他人处理，请稍后重试。", code="busy", http_status=409)
    if isinstance(exc, ProtectedError):
        exc = BusinessError("该记录已被其他业务数据引用，不能删除；可改为停用。", code="protected")
    if isinstance(exc, IntegrityError):  # ProtectedError 是 IntegrityError 子类，必须先判断
        exc = BusinessError("数据冲突：记录已存在或已被其他人修改，请刷新后重试。", code="conflict")
    if isinstance(exc, Http404):
        exc = exceptions.NotFound("记录不存在或无权访问")
    if isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    response = drf_handler(exc, context)
    if response is None:
        return None

    field_errors = {}
    if isinstance(exc, exceptions.ValidationError):
        code = "validation_error"
        if isinstance(response.data, dict):
            field_errors = {k: _flatten(v) for k, v in response.data.items()}
            message = "提交内容有误：" + "；".join(f"{k} {v}" for k, v in field_errors.items())
        else:
            message = _flatten(response.data)
    elif isinstance(exc, BusinessError):
        code = exc.biz_code
        message = str(exc.detail)
    elif isinstance(exc, exceptions.NotAuthenticated):
        code, message = "not_authenticated", "请先登录"
    elif isinstance(exc, exceptions.PermissionDenied):
        code = "permission_denied"
        message = str(exc.detail) if str(exc.detail) != "You do not have permission to perform this action." else "当前角色无权执行此操作"
    elif isinstance(exc, exceptions.NotFound):
        code, message = "not_found", "记录不存在或无权访问"
    else:
        code = getattr(exc, "default_code", "error")
        message = _flatten(response.data.get("detail", response.data)) if isinstance(response.data, dict) else _flatten(response.data)

    response.data = {"code": code, "message": message, "field_errors": field_errors, "request_id": rid}
    return response
