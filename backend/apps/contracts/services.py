"""订单确认 → 业绩：受控、幂等、可撤销留痕。"""
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.errors import BusinessError
from apps.common.roles import can
from apps.performances.models import Performance
from apps.projects.models import Project
from apps.projects.views import init_milestones

from .models import OrderConfirmation


def _performance_from_order(order: OrderConfirmation, user) -> Performance:
    p = order.project
    host = next((x.account.name for x in p.parties.select_related("account") if x.party_role == "主机厂"), "")
    owner = next((x.account.name for x in p.parties.select_related("account") if x.party_role == "业主"), "")
    return Performance.objects.create(
        project=p,
        order_confirmation=order,
        source="ORDER",
        status="CONFIRMED",
        title=p.name,
        year=(order.signed_on or timezone.localdate()).year,
        region=p.region,
        industry=p.industry,
        product_summary=order.product_summary,
        machine_type=p.machine_type,
        host_maker=host,
        owner_org=owner,
        investor=p.investor,
        sales_owner=p.sales_owner,
        internal_amount=order.contract_amount,
        created_by=user,
        confirmed_by=user,
        confirmed_at=timezone.now(),
    )


def confirm_order(order_id: int, user) -> OrderConfirmation:
    """确认订单。并发/重复点击安全：行锁 + OneToOne 约束 + 已确认直接返回（幂等）。"""
    with transaction.atomic():
        order = OrderConfirmation.objects.select_for_update(of=("self",)).select_related("project").get(pk=order_id)
        if order.status == "CONFIRMED":
            return order  # 幂等：重复确认不再生成业绩
        if order.status == "REVOKED":
            raise BusinessError("已撤销的确认记录不能再次确认，请新建。", code="revoked")
        mode = settings.PERFORMANCE_CONFIRM_MODE
        if order.basis == "LEADER":
            if not can(user, "performance.approve"):
                raise BusinessError("“负责人确认”方式只能由部门负责人/管理员确认。", code="leader_required", http_status=403)
            if not order.reason.strip():
                raise BusinessError("负责人确认（无合同/订单号）必须填写确认理由。", code="reason_required", http_status=400)
        else:
            if not order.order_ref.strip():
                raise BusinessError("以合同/订单为依据时必须填写合同/订单号。", code="order_ref_required", http_status=400)
            if mode == "AND" and not can(user, "performance.approve"):
                raise BusinessError("当前规则为“合同/订单 且 负责人确认”，需由负责人完成确认。", code="leader_required", http_status=403)
        dup = OrderConfirmation.objects.filter(legal_entity=order.legal_entity, order_ref=order.order_ref).exclude(pk=order.pk).exclude(status="REVOKED")
        if order.order_ref and dup.exists():
            raise BusinessError(f"合同/订单号 {order.order_ref} 在该出具主体下已存在，禁止重复归集业绩。", code="duplicate_order")
        order.status = "CONFIRMED"
        order.confirmed_by = user
        order.confirmed_at = timezone.now()
        try:
            order.save()
            _performance_from_order(order, user)
        except IntegrityError as e:
            raise BusinessError("重复确认：该订单已生成业绩。", code="duplicate_performance") from e
        p = Project.objects.select_for_update().get(pk=order.project_id)
        p.presale_phase = "成交"
        p.save(update_fields=["presale_phase", "updated_at"])
        if order.quote_id and order.quote.status in ("GENERATED", "SENT"):
            order.quote.status = "ACCEPTED"
            order.quote.save(update_fields=["status", "updated_at"])
        init_milestones(p, user)
        return order


def revoke_order(order_id: int, user, reason: str) -> OrderConfirmation:
    if not reason.strip():
        raise BusinessError("撤销必须填写原因", code="reason_required", http_status=400)
    with transaction.atomic():
        order = OrderConfirmation.objects.select_for_update().get(pk=order_id)
        if order.status == "REVOKED":
            return order
        order.status = "REVOKED"
        order.revoked_by = user
        order.revoked_at = timezone.now()
        order.revoke_reason = reason[:300]
        order.save()
        Performance.objects.filter(order_confirmation=order).update(status="REVOKED")
        return order
