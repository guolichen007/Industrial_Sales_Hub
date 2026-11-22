"""RC3 收口 C2（D-6）：内部业绩 PDF 未选择导出状态时服务端只导出“已确认”；其他状态须显式 export_status，标题与筛选摘要注明。

反例（02b5464）：GET /api/performances/export-pdf/ 不带参数时包含待核实与已撤销业绩。
"""
import re

import pytest

from apps.audit.models import AuditEvent
from tests.test_rc3_g3_internal_pdf import _perf, _text

pytestmark = pytest.mark.django_db


def _rows(users):
    return {
        "c": _perf(users, title="已确认业绩甲", status="CONFIRMED"),
        "p": _perf(users, title="待核实业绩乙", status="PENDING"),
        "r": _perf(users, title="已撤销业绩丙", status="REVOKED"),
        "o": _perf(users, title="他人已确认丁", status="CONFIRMED", sales_owner=users["sales2"]),
    }


def test_C2_default_exports_confirmed_only(client_for, users, base):
    _rows(users)
    r = client_for("sales").get("/api/performances/export-pdf/")
    t = re.sub(r"[ \t]+", "", _text(r.content))
    assert "已确认业绩甲" in t and "待核实业绩乙" not in t and "已撤销业绩丙" not in t and "他人已确认丁" not in t
    assert "内部业绩查询结果（仅已确认）" in t and "导出状态=仅已确认" in t and "共1条" in t
    ev = AuditEvent.objects.filter(action="export_pdf").latest("id")
    assert ev.detail["filters"]["export_status"] == "CONFIRMED" and ev.detail["rows"] == 1


@pytest.mark.parametrize("es,want,title", [("PENDING", {"待核实业绩乙"}, "仅待核实"), ("REVOKED", {"已撤销业绩丙"}, "仅已撤销"),
                                            ("ALL", {"已确认业绩甲", "待核实业绩乙", "已撤销业绩丙"}, "全部状态（含待核实/已撤销）")])
def test_C2_explicit_export_status(client_for, users, base, es, want, title):
    _rows(users)
    t = re.sub(r"[ \t]+", "", _text(client_for("sales").get(f"/api/performances/export-pdf/?export_status={es}").content))
    for name in ("已确认业绩甲", "待核实业绩乙", "已撤销业绩丙"):
        assert (name in t) == (name in want), (es, name)
    assert "他人已确认丁" not in t  # 显式选择状态也不能越出本人范围
    assert f"内部业绩查询结果（{title}）" in t
    if es == "ALL":
        assert "待核实" in t and "已撤销" in t  # 每行状态列清晰


def test_C2_list_status_param_does_not_change_pdf_scope_and_bad_values_rejected(client_for, users, base):
    _rows(users)
    s = client_for("sales")
    # 列表筛选 status=PENDING 不会让 PDF 默认导出待核实（防止空串/列表参数混淆“默认已确认”与“全部”）
    t = _text(s.get("/api/performances/export-pdf/?status=PENDING").content)
    assert "已确认业绩甲" in t and "待核实业绩乙" not in t
    assert s.get("/api/performances/export-pdf/?export_status=").status_code == 200  # 空值 = 默认（已确认）
    assert s.get("/api/performances/export-pdf/?export_status=EVERYTHING").status_code == 400
    # 销售不能通过查询参数拿到他人业绩
    t = _text(s.get(f"/api/performances/export-pdf/?export_status=ALL&sales_owner={users['sales2'].id}").content)
    assert "他人已确认丁" not in t and "共 0 条" in t
    # 列表接口本身仍可查看待核实/已撤销（台账语义不变）
    assert {x["title"] for x in s.get("/api/performances/?status=REVOKED").json()} == {"已撤销业绩丙"}


def test_C2_leader_scope_and_cap(client_for, users, base):
    _rows(users)
    t = _text(client_for("leader").get("/api/performances/export-pdf/?export_status=ALL").content)
    assert "他人已确认丁" in t and "7654321" not in t
    t = _text(client_for("leader").get("/api/performances/export-pdf/").content)
    assert "待核实业绩乙" not in t and "他人已确认丁" in t
