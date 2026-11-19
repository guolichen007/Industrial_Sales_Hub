"""订单确认 → 业绩：受控、幂等、可撤销留痕（V2.1）。"""
import hashlib
import json

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.errors import BusinessError
from apps.common.roles import can
from apps.performances.models import Performance
from apps.projects.models import Project, ProjectUpdate
from apps.projects.views import init_milestones, refresh_execution_phase

from .models import OrderConfirmation, normalize_order_ref

FINGERPRINT_FIELDS = ("project", "quote", "quote_revision", "legal_entity", "customer", "basis", "order_ref", "signed_on", "contract_amount", "product_summary", "reason", "confirm_now")


def fingerprint(data: dict) -> str:
    """幂等请求指纹：同一 idempotency_key 必须对应同一请求内容。"""
    norm = {}
    for k in FINGERPRINT_FIELDS:
        v = data.get(k)
        if hasattr(v, "pk"):
            v = v.pk
        if k == "order_ref":
            v = normalize_order_ref(v or "")
        norm[k] = "" if v in (None, "") else str(v)
    return hashlib.sha256(json.dumps(norm, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _performance_from_order(order: OrderConfirmation, user) -> Performance:
    p = order.project
    host = next((x.account.name for x in p.parties.select_related("account") if x.party_role == "主机厂"), "")
    owner = next((x.account.name for x in p.parties.select_related("account") if x.party_role == "业主"), "")
    return Performance.objects.create(
        project=p,
        order_confirmation=order,
        source="ORDER",
        status="CONFIRMED",
        title=p.name,  # 不含合同编号：标题会进入销售视图与对外案例
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
    """确认订单。并发/重复安全：锁顺序 Project → Order；已确认直接返回（幂等）；OneToOne 约束兜底。"""
    with transaction.atomic():
        project_id = OrderConfirmation.objects.filter(pk=order_id).values_list("project_id", flat=True).first()
        if project_id is None:
            raise BusinessError("确认记录不存在", code="not_found", http_status=404)
        project = Project.objects.select_for_update().get(pk=project_id)
        order = OrderConfirmation.objects.select_for_update(of=("self",)).select_related("quote", "quote_revision").get(pk=order_id)
        if order.status == "CONFIRMED":
            return order
        if order.status == "REVOKED":
            raise BusinessError("已撤销的确认记录不能再次确认，请新建。", code="revoked")
        mode = settings.PERFORMANCE_CONFIRM_MODE
        if order.basis == "LEADER":
            if not can(user, "performance.approve"):
                raise BusinessError("“负责人确认”方式只能由部门负责人确认。", code="leader_required", http_status=403)
            if not order.reason.strip():
                raise BusinessError("负责人确认（无合同/订单号）必须填写确认理由。", code="reason_required", http_status=400)
        else:
            if not order.order_ref_norm:
                raise BusinessError("以合同/订单为依据时必须填写合同/订单号。", code="order_ref_required", http_status=400)
            if mode == "AND" and not can(user, "performance.approve"):
                raise BusinessError("当前规则为“合同/订单 且 负责人确认”，需由负责人完成确认。", code="leader_required", http_status=403)
        if order.order_ref_norm and OrderConfirmation.objects.filter(legal_entity=order.legal_entity, order_ref_norm=order.order_ref_norm, status="CONFIRMED").exclude(pk=order.pk).exists():
            raise BusinessError(f"合同/订单号 {order.order_ref} 在该出具主体下已确认，禁止重复归集业绩。", code="duplicate_order")
        order.status = "CONFIRMED"
        order.confirmed_by = user
        order.confirmed_at = timezone.now()
        try:
            with transaction.atomic():
                order.save()
                _performance_from_order(order, user)
        except IntegrityError as e:
            raise BusinessError("重复确认：该订单号已存在或已生成业绩。", code="duplicate_order") from e
        project.presale_phase = "成交"
        project.save(update_fields=["presale_phase", "updated_at"])
        if order.quote_id:
            from apps.quotations.models import Quote

            q = Quote.objects.select_for_update().get(pk=order.quote_id)
            if q.status in ("GENERATED", "SENT"):
                q.status = "ACCEPTED"
                q.save(update_fields=["status", "updated_at"])
        init_milestones(project, user)
        ProjectUpdate.objects.create(project=project, stream=ProjectUpdate.EXECUTION, title=f"合同/订单确认：{order.order_ref or '负责人确认'}",
                                     content=order.product_summary, happened_at=timezone.now(), created_by=user)
        return order


def revoke_order(order_id: int, user, reason: str) -> OrderConfirmation:
    """撤销确认：保留记录与执行历史；若项目已无其他有效订单，售前阶段回到“结果待定”，执行阶段标记暂停。"""
    if not reason.strip():
        raise BusinessError("撤销必须填写原因", code="reason_required", http_status=400)
    with transaction.atomic():
        project_id = OrderConfirmation.objects.filter(pk=order_id).values_list("project_id", flat=True).first()
        if project_id is None:
            raise BusinessError("确认记录不存在", code="not_found", http_status=404)
        project = Project.objects.select_for_update().get(pk=project_id)
        order = OrderConfirmation.objects.select_for_update(of=("self",)).get(pk=order_id)
        if order.status == "REVOKED":
            return order
        was_confirmed = order.status == "CONFIRMED"
        order.status = "REVOKED"
        order.revoked_by = user
        order.revoked_at = timezone.now()
        order.revoke_reason = reason[:300]
        order.save()
        Performance.objects.filter(order_confirmation=order).update(status="REVOKED")
        if was_confirmed:
            others = project.orders.filter(status="CONFIRMED").exists()
            if not others:
                # 最后一笔有效订单被撤销：保留真实执行节点和历史，项目暂停执行、售前回到结果待定
                project.presale_phase = "结果待定"
                if project.milestones.exists():
                    project.execution_phase = "暂停"
                project.save(update_fields=["presale_phase", "execution_phase", "updated_at"])
                if order.quote_id:
                    from apps.quotations.models import Quote

                    q = Quote.objects.select_for_update().get(pk=order.quote_id)
                    if q.status == "ACCEPTED" and not q.orders.filter(status="CONFIRMED").exists():
                        q.status = "SENT"
                        q.save(update_fields=["status", "updated_at"])
            else:
                refresh_execution_phase(project)
            ProjectUpdate.objects.create(project=project, stream=ProjectUpdate.EXECUTION, title=f"撤销合同/订单确认：{order.order_ref or '负责人确认'}",
                                         content=f"原因：{order.revoke_reason}" + ("" if others else "；项目已无有效订单，执行暂停"), happened_at=timezone.now(), created_by=user)
        return order
