"""RC3-G3：销售业绩筛选 → 内部安全版 PDF（T11–T13）。

反例（RC2 baa9ef6）：GET /api/performances/export-pdf/ 不存在（404）；销售只有 Excel 导出或对外案例册（仅限已确认 + 已公开审批）。
"""
import re
import subprocess
import tempfile

import pytest

from apps.audit.models import AuditEvent
from apps.performances.models import Performance

pytestmark = pytest.mark.django_db


def _perf(users, **kw):
    d = dict(source="MANUAL", status="CONFIRMED", title="业绩", year=2025, region="华东", industry="港口", machine_type="岸桥", internal_amount="7654321.00",
             remark="内部备注SECRET", sales_owner=users["sales"], publishable=False, product_summary="润滑系统", host_maker="某主机厂")
    d.update(kw)
    return Performance.objects.create(**d)


def _text(pdf: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf)
        f.flush()
        try:
            t = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True, timeout=30).stdout
            info = subprocess.run(["pdfinfo", f.name], capture_output=True, text=True, timeout=30).stdout
        except FileNotFoundError:
            pytest.skip("需要 poppler-utils")
    return t + "\n" + info


def test_T11_sales_filtered_three_rows_safe_fields(client_for, users, base):
    from apps.contracts.models import OrderConfirmation

    o = OrderConfirmation.objects.create(project=base["p_sales"], legal_entity=base["ents"][0], basis="CONTRACT", order_ref="HT-SECRET-001")
    mine = [_perf(users, title=f"我的港口业绩{i}", order_confirmation=o if i == 0 else None) for i in range(3)]
    _perf(users, title="我的电力业绩", industry="电力")
    _perf(users, title="别人的港口业绩", sales_owner=users["sales2"])
    r = client_for("sales").get("/api/performances/export-pdf/?industry=港口")
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf"
    t = re.sub(r"[ \t]+", "", _text(r.content))
    for p in mine:
        assert p.title in t
    assert "我的电力业绩" not in t and "别人的港口业绩" not in t
    assert "共3条" in t and "行业=港口" in t and "生成人：sales" in t and "第1页/共1页" in t.replace(" ", "")
    assert "公司内部资料" in t and "INTERNAL" in t
    for secret in ("7654321", "7,654,321", "HT-SECRET-001", "内部备注SECRET", "单价"):
        assert secret not in t, secret
    for secret in (b"7654321", b"HT-SECRET"):
        assert secret not in r.content  # 二进制流中也不存在（不依赖文本抽取）
    ev = AuditEvent.objects.filter(resource="performance", action="export_pdf").latest("id")
    assert ev.detail["rows"] == 3 and ev.detail["filters"] == {"industry": "港口", "export_status": "CONFIRMED"} and len(ev.detail["pdf_sha256"]) == 64  # RC3 收口 D-6：审计记录导出状态
    assert ev.detail["pdf_sha256"] == r["X-Content-SHA256"]


def test_T12_other_sales_rows_cannot_be_included(client_for, users, base):
    other = _perf(users, title="别人的业绩X", sales_owner=users["sales2"])
    s = client_for("sales")
    for q in (f"?ids={other.id}", f"?performance_ids={other.id}"):
        assert s.get("/api/performances/export-pdf/" + q).status_code == 400
    t = _text(s.get(f"/api/performances/export-pdf/?project={base['p_other'].id}").content)
    assert "别人的业绩X" not in t
    t = _text(s.get("/api/performances/export-pdf/?q=别人的业绩X").content)
    assert "共 0 条" in t and t.count("别人的业绩X") == 1  # 只出现在“筛选条件”回显中，不出现在数据行
    assert client_for("admin").get("/api/performances/export-pdf/").status_code == 403


def test_T13_internal_pdf_is_not_a_public_case(client_for, users, base):
    p = _perf(users, title="未公开业绩", publishable=False)
    s = client_for("sales")
    # 内部 PDF 可以包含本人未公开业绩，但带“禁止外发/不是对外案例”标识
    t = _text(s.get("/api/performances/export-pdf/").content)
    assert "未公开业绩" in t and "不是对外案例" in t
    # 对外案例册仍然拒绝未公开审批的业绩
    r = s.post("/api/case-releases/", {"performance_ids": [p.id], "title": "试图外发"}, format="json")
    assert r.status_code == 400 and "not_publishable" in r.content.decode()
    # 内部导出不产生对外案例记录
    from apps.performances.models import CaseRelease

    assert not CaseRelease.objects.exists()


def test_finance_and_leader_scope(client_for, users, base):
    _perf(users, title="甲")
    _perf(users, title="乙", sales_owner=users["sales2"])
    for who in ("finance", "leader"):
        t = _text(client_for(who).get("/api/performances/export-pdf/").content)
        assert "甲" in t and "乙" in t and "7654321" not in t  # 负责人也只拿到安全版（金额需 Excel 内部导出）


def test_long_result_paginates(client_for, users, base):
    for i in range(80):
        _perf(users, title=f"长列表业绩{i:03d}")
    r = client_for("sales").get("/api/performances/export-pdf/")
    t = _text(r.content).replace(" ", "")
    n = len(re.findall(rb"/Type /Page[^s]", r.content))
    assert n >= 3 and f"第{n}页/共{n}页" in t and "长列表业绩079" in t
