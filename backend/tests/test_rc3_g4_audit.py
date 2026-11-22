"""RC3-G4：操作人、时间与字段级审计；发生时间与系统时间分离；安全活动时间线（T14–T16）。

反例（RC2 baa9ef6）：报价条款修改、报价行新增/改价/删行、联系人修改无审计；项目无 updated_by；无项目活动接口；
进展 happened_at 可由客户端任意填写（包括未来时间）。
"""
import pytest
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.projects.models import ProjectUpdate

pytestmark = pytest.mark.django_db


def test_T14_quote_line_terms_contact_project_audited(client_for, base, make_quote):
    qid, rid = make_quote(render=False)
    c = client_for("assistant")
    lid = c.get(f"/api/quote-revisions/{rid}/").json()["lines"][0]["id"]
    n0 = AuditEvent.objects.count()
    last = AuditEvent.objects.latest("id").id  # make_quote 夹具自身已产生 add_line 审计
    assert c.patch(f"/api/quote-revisions/{rid}/", {"payment_terms": "预付 30%"}, format="json").status_code == 200
    assert c.post(f"/api/quote-revisions/{rid}/lines/", {"name": "安装调试", "unit_price": "5000"}, format="json").status_code == 201
    assert c.patch(f"/api/quote-revisions/{rid}/lines/{lid}/", {"unit_price": "38000", "qty": "3"}, format="json").status_code == 200
    assert c.delete(f"/api/quote-revisions/{rid}/lines/{lid}/").status_code == 200
    evs = list(AuditEvent.objects.filter(resource="quote_revision", resource_id=str(rid), id__gt=last).order_by("id"))
    assert [e.action for e in evs] == ["update_revision", "add_line", "edit_line", "delete_line"]
    assert evs[0].detail["fields"] == ["payment_terms"]
    assert sorted(evs[2].detail["fields"]) == ["qty", "unit_price"]
    for e in evs:
        assert e.actor_name == "assistant" and e.at is not None
        assert "38000" not in str(e.detail) and "5000" not in str(e.detail)  # 价格值脱敏
    # 联系人修改
    from apps.organizations.models import Contact

    ct = Contact.objects.first()
    assert c.patch(f"/api/contacts/{ct.id}/", {"mobile": "139"}, format="json").status_code == 200
    ev = AuditEvent.objects.filter(resource="contact", action="update").latest("id")
    assert ev.detail["fields"] == ["mobile"] and ev.actor_name == "assistant"
    # 项目资料：updated_by + 字段名
    r = client_for("finance").patch(f"/api/projects/{base['p_sales'].id}/", {"location": "新厂址"}, format="json")
    assert r.json()["updated_by_name"] == "finance"
    assert AuditEvent.objects.filter(resource="project", action="update").latest("id").detail["fields"] == ["location"]
    assert AuditEvent.objects.count() >= n0 + 6


def test_T14_audit_rolls_back_with_failed_business_write(client_for, base, make_quote):
    """审计与业务写入同一事务：业务失败（冻结版本改价 409）时不留审计。"""
    qid, rid = make_quote()  # 已生成冻结
    n0 = AuditEvent.objects.filter(resource="quote_revision", action="update_revision").count()
    assert client_for("assistant").patch(f"/api/quote-revisions/{rid}/", {"payment_terms": "x"}, format="json").status_code == 409
    assert AuditEvent.objects.filter(resource="quote_revision", action="update_revision").count() == n0


def test_T15_happened_at_backfill_but_created_at_server_side(client_for, base):
    pid = base["p_sales"].id
    s = client_for("sales")
    r = s.post(f"/api/projects/{pid}/updates/", {"stream": "PRESALE", "title": "补录上周拜访", "happened_at": "2026-11-10T10:00:00+08:00", "created_at": "2001-01-01T00:00:00Z"}, format="json")
    assert r.status_code == 201
    u = ProjectUpdate.objects.get(pk=r.json()["id"])
    assert u.happened_at.date().isoformat() == "2026-11-10"
    assert abs((timezone.now() - u.created_at).total_seconds()) < 60  # 系统时间不可伪造
    assert s.post(f"/api/projects/{pid}/updates/", {"stream": "PRESALE", "title": "未来", "happened_at": "2099-01-01T00:00:00Z"}, format="json").status_code == 400


def test_T16_activity_timeline_role_trimmed(client_for, base, make_quote):
    pid = base["p_sales"].id
    client_for("sales").patch(f"/api/projects/{pid}/", {"description": "售前说明"}, format="json")
    qid, rid = make_quote(render=False)
    client_for("assistant").patch(f"/api/quote-revisions/{rid}/lines/{client_for('assistant').get(f'/api/quote-revisions/{rid}/').json()['lines'][0]['id']}/", {"unit_price": "31000"}, format="json")
    client_for("assistant").post(f"/api/projects/{pid}/handoff/", {"note": "中标，金额 120 万"}, format="json")
    client_for("finance").post(f"/api/projects/{pid}/tracks/", {"track": "PAYMENT", "code": "PREPAID", "private_note": "到账 36 万"}, format="json")
    # 第二位销售（可见不可编辑）
    s2 = client_for("sales2").get(f"/api/projects/{pid}/activity/")
    assert s2.status_code == 200
    rows = s2.json()
    labels = [r["action_label"] for r in rows]
    assert "中标确认/转交商务" in labels and "记录交付/资金状态" in labels and "修改项目资料" in labels
    body = s2.content.decode()
    for secret in ("120 万", "36 万", "31000", "报价：", "summary", "fields", "unit_price"):
        assert secret not in body, secret
    assert all(r["actor_name"] and r["at"] for r in rows)
    # 商务：可见摘要与报价动作
    a = client_for("assistant").get(f"/api/projects/{pid}/activity/").json()
    assert any(r["action_label"].startswith("报价：") for r in a) and any("summary" in r for r in a)
    # 报价动作只给有报价权限者：RC3 收口 D-2 后财务默认有报价权限（可见）；RC2 口径下财务无报价权限（不可见）
    f = client_for("finance").get(f"/api/projects/{pid}/activity/").json()
    assert any(r["action_label"].startswith("报价：") for r in f)
    from django.test import override_settings

    with override_settings(FINANCE_SCOPE="RC2", FINANCE_QUOTE=False):
        f = client_for("finance").get(f"/api/projects/{pid}/activity/").json()
        assert not any(r["action_label"].startswith("报价：") for r in f)
    assert client_for("admin").get(f"/api/projects/{pid}/activity/").status_code in (403, 404)


def test_performance_updated_by_and_field_audit(client_for, users, base):
    from apps.performances.models import Performance

    p = Performance.objects.create(source="MANUAL", status="PENDING", title="x", year=2025, created_by=users["assistant"])
    r = client_for("finance").patch(f"/api/performances/{p.id}/", {"region": "西北"}, format="json")
    assert r.status_code == 200 and r.json()["updated_by_name"] == "finance" and r.json()["created_by_name"] == "assistant"
    assert AuditEvent.objects.filter(resource="performance", action="update").latest("id").detail["fields"] == ["region"]
