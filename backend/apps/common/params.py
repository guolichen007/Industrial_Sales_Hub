"""请求参数解析：非法输入一律 400（不允许 500）。"""
from decimal import Decimal, InvalidOperation

from rest_framework.exceptions import ValidationError


def int_param(value, name, required=False):
    if value in (None, ""):
        if required:
            raise ValidationError({name: "必填"})
        return None
    if isinstance(value, bool):
        raise ValidationError({name: "应为整数"})
    try:
        v = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationError({name: "应为整数"})
    if v < 0 or v > 2**62:
        raise ValidationError({name: "超出范围"})
    return v


def int_list(value, name):
    if not isinstance(value, list):
        raise ValidationError({name: "应为整数列表"})
    out = []
    for x in value:
        v = int_param(x, name, required=True)
        if v not in out:
            out.append(v)
    return out


def str_param(value, name, max_len=200, required=False):
    if value is None:
        value = ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValidationError({name: "应为文本"})
    s = str(value).strip()
    if required and not s:
        raise ValidationError({name: "必填"})
    if len(s) > max_len:
        raise ValidationError({name: f"不超过 {max_len} 字"})
    return s


def bool_param(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def decimal_param(value, name, max_digits=14, places=2, required=False):
    if value in (None, ""):
        if required:
            raise ValidationError({name: "必填"})
        return None
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValidationError({name: "应为数字"})
    if not d.is_finite() or d < 0 or d >= Decimal(10) ** (max_digits - places):
        raise ValidationError({name: "数值无效或超出范围"})
    return d
