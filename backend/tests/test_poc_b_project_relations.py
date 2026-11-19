"""POC-B：项目独立建档、多参与方、多报价、订单确认唯一业绩、撤销留痕。"""
import threading

import pytest
from django.db import connection

from apps.contracts.models import OrderConfirmation
from apps.contracts.services import confirm_order
from apps.performances.models import Performance
from apps.projects.models import Project


def test_U01_sales_creates_project_without_customer_then_links_parties(client_for, base, users):
    c = client_for("sales")
    r = c.post("/api/projects/", {"name": "无客户先建项目"}, format="json")
    assert r.status_code == 201, r.content
    pid = r.json()["id"]
    assert r.json()["sales_owner"] == users["sales"].id and r.json()["parties"] == []
    for acc, role in [(base["accs"][0], "主机厂"), (base["accs"][1], "主机厂"), (base["accs"][2], "投资方")]:
        assert c.post(f"/api/projects/{pid}/parties/", {"account": acc.id, "party_role": role}, format="json").status_code == 201
    # 同组织同身份去重；同组织不同身份允许
    assert c.post(f"/api/projects/{pid}/parties/", {"account": base["accs"][0].id, "party_role": "主机厂"}, format="json").status_code == 409
    assert c.post(f"/api/projects/{pid}/parties/", {"account": base["accs"][2].id, "party_role": "业主"}, format="json").status_code == 201
    assert len(c.get(f"/api/projects/{pid}/").json()["parties"]) == 4


def test_U02_two_quotes_same_project_different_recipients_and_entities(make_quote, base, client_for):
    q1, _ = make_quote(recipient=base["accs"][0], entity=base["ents"][0])
    q2, _ = make_quote(recipient=base["accs"][1], entity=base["ents"][1])
    c = client_for("assistant")
    a, b = c.get(f"/api/quotations/{q1}/").json(), c.get(f"/api/quotations/{q2}/").json()
    assert a["project"] == b["project"] == base["p_sales"].id
    assert (a["recipient"], a["legal_entity"]) != (b["recipient"], b["legal_entity"])
    # 生成后不可更换接收客户/出具公司
    assert c.patch(f"/api/quotations/{q1}/", {"recipient": base["accs"][2].id}, format="json").status_code == 409


def test_lost_quote_does_not_lose_project_and_one_order_one_performance(make_quote, base, client_for):
    q1, _ = make_quote(recipient=base["accs"][0], entity=base["ents"][0])
    q2, _ = make_quote(recipient=base["accs"][1], entity=base["ents"][1])
    c = client_for("assistant")
    c.post(f"/api/quotations/{q2}/status/", {"status": "LOST"}, format="json")
    base["p_sales"].refresh_from_db()
    assert base["p_sales"].presale_phase != "失标"
    r = c.post("/api/order-confirmations/", {"project": base["p_sales"].id, "quote": q1, "basis": "CONTRACT", "order_ref": "HT-001", "contract_amount": "88140", "confirm_now": True}, format="json")
    assert r.status_code == 201, r.content
    assert Performance.objects.filter(order_confirmation_id=r.json()["id"]).count() == 1
    p = Project.objects.get(pk=base["p_sales"].id)
    assert p.presale_phase == "成交" and p.execution_phase == "执行中" and p.milestones.count() == 8
    assert c.get(f"/api/quotations/{q1}/").json()["status"] == "ACCEPTED"
    assert c.get(f"/api/quotations/{q2}/").json()["status"] == "LOST"


def test_U06_repeat_confirm_is_idempotent(base, client_for):
    c = client_for("leader")
    body = {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "basis": "ORDER", "order_ref": "PO-9", "idempotency_key": "k-1"}
    r1 = c.post("/api/order-confirmations/", body, format="json")
    r2 = c.post("/api/order-confirmations/", body, format="json")  # 重复提交（同一幂等键）
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    oid = r1.json()["id"]
    for _ in range(3):
        assert c.post(f"/api/order-confirmations/{oid}/confirm/").status_code == 200
    assert Performance.objects.filter(order_confirmation_id=oid).count() == 1
    # 同主体同订单号再次登记被阻断
    r = c.post("/api/order-confirmations/", {**body, "idempotency_key": "k-2"}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "duplicate_order"
    # 不同主体可用相同订单号
    r = c.post("/api/order-confirmations/", {**body, "idempotency_key": "k-3", "legal_entity": base["ents"][1].id}, format="json")
    assert r.status_code == 201


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirm_creates_single_performance(base, users):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite 不支持行锁并发验证；在 PostgreSQL CI 中执行")
    o = OrderConfirmation.objects.create(project=base["p_sales"], legal_entity=base["ents"][0], basis="ORDER", order_ref="PO-C")
    errs = []

    def run():
        try:
            confirm_order(o.id, users["leader"])
        except Exception as e:  # noqa: BLE001
            errs.append(e)
        finally:
            connection.close()

    ts = [threading.Thread(target=run) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert Performance.objects.filter(order_confirmation=o).count() == 1


def test_leader_basis_requires_reason_and_leader(base, client_for):
    body = {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "basis": "LEADER"}
    r = client_for("assistant").post("/api/order-confirmations/", body, format="json")
    oid = r.json()["id"]
    assert client_for("assistant").post(f"/api/order-confirmations/{oid}/confirm/").status_code == 403
    r = client_for("leader").post(f"/api/order-confirmations/{oid}/confirm/")
    assert r.status_code == 400 and r.json()["code"] == "reason_required"
    r = client_for("leader").post("/api/order-confirmations/", {**body, "reason": "口头确认，合同流程中", "confirm_now": True}, format="json")
    assert r.status_code == 201 and r.json()["status"] == "CONFIRMED"


def test_and_mode_requires_leader(base, client_for, settings):
    settings.PERFORMANCE_CONFIRM_MODE = "AND"
    r = client_for("assistant").post("/api/order-confirmations/", {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "basis": "CONTRACT", "order_ref": "HT-A"}, format="json")
    oid = r.json()["id"]
    assert client_for("assistant").post(f"/api/order-confirmations/{oid}/confirm/").status_code == 403
    assert client_for("leader").post(f"/api/order-confirmations/{oid}/confirm/").status_code == 200


def test_revoke_keeps_trace_and_allows_reuse_of_order_ref(base, client_for):
    c = client_for("leader")
    r = c.post("/api/order-confirmations/", {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "basis": "CONTRACT", "order_ref": "HT-R", "confirm_now": True}, format="json")
    oid = r.json()["id"]
    assert client_for("assistant").post(f"/api/order-confirmations/{oid}/revoke/", {"reason": "x"}, format="json").status_code == 403
    assert c.post(f"/api/order-confirmations/{oid}/revoke/", {"reason": ""}, format="json").status_code == 400
    r = c.post(f"/api/order-confirmations/{oid}/revoke/", {"reason": "合同作废"}, format="json")
    assert r.json()["status"] == "REVOKED"
    perf = Performance.objects.get(order_confirmation_id=oid)
    assert perf.status == "REVOKED"  # 保留痕迹而非删除
    assert c.post(f"/api/order-confirmations/{oid}/confirm/").status_code == 409
    r = c.post("/api/order-confirmations/", {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "basis": "CONTRACT", "order_ref": "HT-R"}, format="json")
    assert r.status_code == 201


def test_customer_referenced_by_project_cannot_be_deleted(base, client_for):
    c = client_for("leader")
    pid = base["p_sales"].id
    c.post(f"/api/projects/{pid}/parties/", {"account": base["accs"][0].id, "party_role": "主机厂"}, format="json")
    r = c.delete(f"/api/accounts/{base['accs'][0].id}/")
    assert r.status_code == 409 and r.json()["code"] == "protected"
    assert Project.objects.filter(pk=pid).exists()


def test_manual_performance_is_pending_until_leader_approves(client_for):
    r = client_for("assistant").post("/api/performances/", {"title": "历史项目", "year": 2021, "internal_amount": "100000"}, format="json")
    assert r.status_code == 201 and r.json()["status"] == "PENDING" and r.json()["source"] == "MANUAL"
    pid = r.json()["id"]
    assert client_for("assistant").post(f"/api/performances/{pid}/approve/").status_code == 403
    assert client_for("leader").post(f"/api/performances/{pid}/approve/").json()["status"] == "CONFIRMED"


@pytest.mark.django_db(transaction=True)
def test_concurrent_project_creation_gets_unique_codes(users):
    if connection.vendor == "sqlite":
        pytest.skip("在 PostgreSQL 中执行")
    from rest_framework.test import APIClient

    codes, errs = [], []

    def run(i):
        try:
            c = APIClient()
            c.force_authenticate(users["sales"])
            r = c.post("/api/projects/", {"name": f"并发项目{i}"}, format="json")
            codes.append(r.json().get("code"))
        except Exception as e:  # noqa: BLE001
            errs.append(e)
        finally:
            connection.close()

    ts = [threading.Thread(target=run, args=(i,)) for i in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(set(codes)) == 5 and None not in codes
