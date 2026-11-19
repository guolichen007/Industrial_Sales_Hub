from .models import AuditEvent

# 审计中禁止落入的敏感键
_REDACT = {"password", "internal_amount", "contract_amount", "unit_price", "received_amount", "invoiced_amount"}


def _clean(d):
    if isinstance(d, dict):
        return {k: ("***" if k in _REDACT else _clean(v)) for k, v in d.items()}
    if isinstance(d, list):
        return [_clean(x) for x in d]
    return d if isinstance(d, (str, int, float, bool, type(None))) else str(d)


def audit(request, action, resource, resource_id="", summary="", detail=None):
    user = getattr(request, "user", None)
    actor = user if user is not None and user.is_authenticated else None
    AuditEvent.objects.create(
        actor=actor,
        actor_name=getattr(actor, "name", "") if actor else "",
        action=action,
        resource=resource,
        resource_id=str(resource_id or ""),
        summary=summary[:300],
        detail=_clean(detail or {}),
        request_id=getattr(request, "request_id", "") or "",
        ip=(request.META.get("REMOTE_ADDR", "") if request is not None else ""),
    )
