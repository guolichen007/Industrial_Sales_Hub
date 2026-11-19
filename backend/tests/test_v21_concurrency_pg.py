"""V2.1-LAN：PostgreSQL barrier 并发反例（L2-1、L2-2、LAN-04）。SQLite 下跳过。

每个用例用 threading.Barrier 让多个真实 HTTP 层请求（APIClient，各自独立数据库连接）同时起跑，
然后断言：无 500、无重复业绩、已冻结版本的快照/行/金额/PDF 哈希一致、草稿不出现半状态。
"""
import threading

import pytest
from django.db import connection
from rest_framework.test import APIClient

from apps.contracts.models import OrderConfirmation
from apps.performances.models import Performance
from apps.quotations.models import QuoteLine, QuoteRevision

pytestmark = pytest.mark.django_db(transaction=True)


def need_pg():
    if connection.vendor != "postgresql":
        pytest.skip("并发反例需要 PostgreSQL 行锁")


def run_parallel(funcs):
    bar = threading.Barrier(len(funcs))
    results = [None] * len(funcs)

    def wrap(i, f):
        try:
            bar.wait(timeout=30)
            results[i] = f()
        except Exception as e:  # noqa: BLE001
            results[i] = e
        finally:
            connection.close()

    ts = [threading.Thread(target=wrap, args=(i, f)) for i, f in enumerate(funcs)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    return results


def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def status_codes(results):
    out = []
    for r in results:
        assert not isinstance(r, Exception), f"未处理异常：{r!r}"
        out.append(r.status_code)
    return out


def assert_frozen_consistent(rid):
    import hashlib

    from apps.common.pdfkit import canonical_json, read_private

    rev = QuoteRevision.objects.get(pk=rid)
    assert rev.status == "GENERATED"
    lines = list(QuoteLine.objects.filter(revision=rev).order_by("sort", "id"))
    assert len(lines) == len(rev.snapshot["lines"]), "冻结后行数与快照不一致"
    assert [str(l.line_gross) for l in lines] == [l["line_gross"] for l in rev.snapshot["lines"]]
    assert str(rev.total_gross) == rev.snapshot["totals"]["gross"]
    assert hashlib.sha256(canonical_json(rev.snapshot)).hexdigest() == rev.snapshot_sha256
    assert hashlib.sha256(read_private(rev.pdf_storage_key)).hexdigest() == rev.pdf_sha256


@pytest.mark.parametrize("trial", range(6))
def test_render_races_with_edits(users, base, make_quote, trial):
    need_pg()
    _, rid = make_quote(render=False)
    line_id = QuoteLine.objects.filter(revision_id=rid).first().id
    u = users["assistant"]
    res = run_parallel([
        lambda: client(u).post(f"/api/quote-revisions/{rid}/render/"),
        lambda: client(u).post(f"/api/quote-revisions/{rid}/render/"),
        lambda: client(u).patch(f"/api/quote-revisions/{rid}/", {"remark": "race"}, format="json"),
        lambda: client(u).post(f"/api/quote-revisions/{rid}/lines/", {"product": base["prods"][1].id, "qty": "1"}, format="json"),
        lambda: client(u).patch(f"/api/quote-revisions/{rid}/lines/{line_id}/", {"qty": "9"}, format="json"),
    ])
    codes = status_codes(res)
    assert 500 not in codes, codes
    assert codes[:2].count(200) == 1, f"生成必须恰好成功一次：{codes}"
    assert all(c in (200, 201, 409) for c in codes), codes
    assert_frozen_consistent(rid)


def test_concurrent_next_revision_only_one(users, make_quote):
    need_pg()
    qid, _ = make_quote()
    u = users["assistant"]
    res = run_parallel([lambda: client(u).post(f"/api/quotations/{qid}/revisions/") for _ in range(5)])
    codes = status_codes(res)
    assert codes.count(201) == 1 and 500 not in codes, codes
    assert QuoteRevision.objects.filter(quote_id=qid).count() == 2


def test_concurrent_same_idempotency_key(users, base):
    need_pg()
    u = users["leader"]
    body = {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "basis": "CONTRACT", "order_ref": "C-IDEM", "idempotency_key": "same-key", "confirm_now": True}
    res = run_parallel([lambda: client(u).post("/api/order-confirmations/", body, format="json") for _ in range(5)])
    codes = status_codes(res)
    assert 500 not in codes, codes
    assert set(codes) <= {200, 201, 409}, codes
    assert OrderConfirmation.objects.count() == 1
    assert Performance.objects.count() == 1


def test_concurrent_same_order_ref_different_keys(users, base):
    need_pg()
    u = users["leader"]
    res = run_parallel([
        (lambda i=i: client(u).post("/api/order-confirmations/", {"project": base["p_sales"].id, "legal_entity": base["ents"][0].id, "basis": "CONTRACT", "order_ref": "C-SAME", "idempotency_key": f"k{i}", "confirm_now": True}, format="json"))
        for i in range(5)
    ])
    codes = status_codes(res)
    assert codes.count(201) == 1 and 500 not in codes, codes
    assert all(c in (201, 409) for c in codes), codes
    assert Performance.objects.count() == 1


def test_concurrent_leader_confirmations_without_ref_with_retry(users, base):
    """负责人无单号确认：同一草稿被重复点击确认 5 次，只生成一条业绩。"""
    need_pg()
    o = OrderConfirmation.objects.create(project=base["p_sales"], legal_entity=base["ents"][0], basis="LEADER", reason="口头确认")
    u = users["leader"]
    res = run_parallel([lambda: client(u).post(f"/api/order-confirmations/{o.id}/confirm/") for _ in range(5)])
    codes = status_codes(res)
    assert set(codes) == {200}, codes
    assert Performance.objects.filter(order_confirmation=o).count() == 1


def test_five_roles_mixed_concurrent(users, base, make_quote):
    """LAN-04：五角色同时操作（报价生成、订单确认、执行更新、售前跟进、管理员账号查询）。"""
    need_pg()
    _, rid = make_quote(render=False)
    o = OrderConfirmation.objects.create(project=base["p_other"], legal_entity=base["ents"][1], basis="ORDER", order_ref="MIX-1")
    res = run_parallel([
        lambda: client(users["assistant"]).post(f"/api/quote-revisions/{rid}/render/"),
        lambda: client(users["leader"]).post(f"/api/order-confirmations/{o.id}/confirm/"),
        lambda: client(users["finance"]).post(f"/api/order-confirmations/{o.id}/confirm/"),
        lambda: client(users["sales"]).post(f"/api/projects/{base['p_sales'].id}/updates/", {"stream": "PRESALE", "title": "并发跟进"}, format="json"),
        lambda: client(users["admin"]).get("/api/users/"),
    ])
    codes = status_codes(res)
    assert 500 not in codes, codes
    assert Performance.objects.filter(order_confirmation=o).count() == 1
    assert_frozen_consistent(rid)


def test_concurrent_last_admin_demotion(users):
    """两个管理员同时互相降级：至少保留一个管理员。"""
    need_pg()
    from apps.accounts.models import User

    a2 = User(username="admin2", role="ADMIN")
    a2.set_password("Abc12345xyz")
    a2.save()
    a1 = users["admin"]
    res = run_parallel([
        lambda: client(a1).patch(f"/api/users/{a2.id}/", {"role": "SALES"}, format="json"),
        lambda: client(a2).patch(f"/api/users/{a1.id}/", {"role": "SALES"}, format="json"),
    ])
    status_codes(res)
    assert User.objects.filter(role="ADMIN", is_active=True).count() >= 1


def test_pdf_failure_does_not_hang_and_records_error(users, make_quote, monkeypatch):
    """复审 P0：PDF 渲染失败时不得挂起（旧实现用第二连接写被锁的行会无限等待）；草稿不变、last_error 已提交。"""
    need_pg()
    import time as _t

    from apps.quotations import services

    _, rid = make_quote(render=False)
    before = QuoteRevision.objects.get(pk=rid)

    def boom(snap):
        raise RuntimeError("模拟字体缺失")

    monkeypatch.setattr(services, "render_quote_pdf", boom)
    t0 = _t.time()
    r = client(users["assistant"]).post(f"/api/quote-revisions/{rid}/render/")
    assert _t.time() - t0 < 10, "PDF 失败不应挂起"
    assert r.status_code == 500 and r.json()["code"] == "pdf_failed"
    after = QuoteRevision.objects.get(pk=rid)
    assert after.status == "DRAFT" and after.last_error == "模拟字体缺失" and after.lock_version == before.lock_version and not after.pdf_storage_key
