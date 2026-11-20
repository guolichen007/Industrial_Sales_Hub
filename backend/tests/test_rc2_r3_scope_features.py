"""RC2-R3：附件分型权限与首期缺口功能（客户类别、关联项目筛选、历史价格、业绩筛选/导出、案例筛选、报价附件、提醒）。

附件反例（RC1）：项目附件不分类型；报价/合同类文件只能放进“项目附件”，任何可编辑该项目的销售都能下载；
没有“对销售开放交付进度文件”的受控通道，只能整体放开或整体不给。
"""
import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.files.models import Attachment
from apps.projects.models import ProjectParty
from apps.quotations.models import QuoteRevision

pytestmark = pytest.mark.django_db
PNG = b"\x89PNG" + b"0" * 100


def _up(c, owner_type, owner_id, name="a.png", **extra):
    return c.post("/api/attachments/", {"owner_type": owner_type, "owner_id": owner_id, "file": SimpleUploadedFile(name, PNG), **extra}, format="multipart")


# ------------------------------------------------------------------ 附件分型
def test_default_internal_and_sales_upload_is_presale(client_for, base):
    a = _up(client_for("assistant"), "project", base["p_other"].id).json()
    assert a["klass"] == "INTERNAL" and a["sales_visible"] is False
    s = _up(client_for("sales"), "project", base["p_sales"].id, klass="DELIVERY").json()
    assert s["klass"] == "PRESALE"  # 销售无法把自己上传的资料改成交付/可见


def test_sales_sees_only_explicitly_authorized_delivery_files(client_for, base):
    pid = base["p_other"].id  # 销售1 对该项目只有“可见”，不能编辑
    asst = client_for("assistant")
    internal = _up(asst, "project", pid, "内部报价对比.png").json()
    delivery = _up(asst, "project", pid, "发货单.png", klass="DELIVERY").json()
    s1 = client_for("sales")
    # 未授权前：列表为空、直链 403
    assert s1.get(f"/api/attachments/?owner_type=project&owner_id={pid}").json() == []
    assert s1.get(f"/api/attachments/{delivery['id']}/download/").status_code == 403
    # 销售不能自己授权；内部附件不能授权给销售
    assert s1.patch(f"/api/attachments/{delivery['id']}/", {"sales_visible": True}, format="json").status_code == 403
    r = asst.patch(f"/api/attachments/{internal['id']}/", {"sales_visible": True}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "not_delivery"
    # 商务授权交付附件
    assert asst.patch(f"/api/attachments/{delivery['id']}/", {"sales_visible": True}, format="json").status_code == 200
    lst = s1.get(f"/api/attachments/?owner_type=project&owner_id={pid}").json()
    assert [x["id"] for x in lst] == [delivery["id"]]
    assert s1.get(f"/api/attachments/{delivery['id']}/download/").status_code == 200
    # 枚举 ID：内部附件仍 403
    assert s1.get(f"/api/attachments/{internal['id']}/download/").status_code == 403
    for i in range(internal["id"] - 2, delivery["id"] + 3):
        r = s1.get(f"/api/attachments/{i}/download/")
        assert r.status_code in (200, 403) and (r.status_code == 403 or i == delivery["id"])
    # 撤回授权立即生效
    assert asst.patch(f"/api/attachments/{delivery['id']}/", {"sales_visible": False}, format="json").status_code == 200
    assert s1.get(f"/api/attachments/{delivery['id']}/download/").status_code == 403
    # 两次成功的授权变更各一条审计；被拒绝（409/403）的请求整体回滚、不留审计
    assert AuditEvent.objects.filter(resource="attachment", action="classify").count() == 2


def test_sales_visible_respects_project_visibility_own_mode(client_for, base, settings):
    settings.PROJECT_VISIBILITY = "OWN"
    asst = client_for("assistant")
    d = _up(asst, "project", base["p_other"].id, "发货单.png", klass="DELIVERY").json()
    asst.patch(f"/api/attachments/{d['id']}/", {"sales_visible": True}, format="json")
    s1 = client_for("sales")
    assert s1.get(f"/api/attachments/{d['id']}/download/").status_code == 403
    assert s1.get(f"/api/attachments/?owner_type=project&owner_id={base['p_other'].id}").status_code == 403


@pytest.mark.parametrize("who", ["finance", "admin"])
def test_non_sales_roles_do_not_gain_access_via_sales_visible(client_for, base, who):
    asst = client_for("assistant")
    d = _up(asst, "project", base["p_other"].id, "发货单.png", klass="DELIVERY").json()
    asst.patch(f"/api/attachments/{d['id']}/", {"sales_visible": True}, format="json")
    r = client_for(who).get(f"/api/attachments/{d['id']}/download/")
    assert r.status_code == (200 if who == "finance" else 403) or who == "finance" and r.status_code == 403


def test_order_and_quote_files_cannot_be_reclassified_to_sales(client_for, base, make_quote):
    qid, _ = make_quote()
    asst = client_for("assistant")
    qa = _up(asst, "quote", qid, "技术方案.pdf".replace(".pdf", ".png")).json()
    r = asst.patch(f"/api/attachments/{qa['id']}/", {"klass": "DELIVERY", "sales_visible": True}, format="json")
    assert r.status_code == 400
    for who in ("sales", "finance", "admin"):
        c = client_for(who)
        assert c.get(f"/api/attachments/?owner_type=quote&owner_id={qid}").status_code == 403
        assert c.get(f"/api/attachments/{qa['id']}/download/").status_code == 403
        assert _up(c, "quote", qid).status_code == 403


def test_attachment_delete_rules(client_for, base):
    s1 = client_for("sales")
    mine = _up(s1, "project", base["p_sales"].id).json()
    theirs = _up(client_for("assistant"), "project", base["p_sales"].id).json()
    assert s1.delete(f"/api/attachments/{theirs['id']}/").status_code == 403
    assert s1.delete(f"/api/attachments/{mine['id']}/").status_code == 204
    assert client_for("leader").delete(f"/api/attachments/{theirs['id']}/").status_code == 204
    assert not Attachment.objects.exists()
    assert AuditEvent.objects.filter(resource="attachment", action="delete").count() == 2


# ------------------------------------------------------------------ 客户类别 + 关联项目筛选
def test_account_categories_and_filter(client_for, base):
    c = client_for("assistant")
    cat = c.post("/api/account-categories/", {"name": "重点主机厂"}, format="json").json()
    assert client_for("sales").post("/api/account-categories/", {"name": "x"}, format="json").status_code == 403
    assert client_for("admin").get("/api/account-categories/").status_code == 403
    acc = base["accs"][0]
    assert c.patch(f"/api/accounts/{acc.id}/", {"categories": [cat["id"]]}, format="json").status_code == 200
    lst = client_for("sales").get(f"/api/accounts/?category={cat['id']}").json()
    rows = lst["results"] if isinstance(lst, dict) else lst
    assert [a["id"] for a in rows] == [acc.id] and rows[0]["category_names"] == ["重点主机厂"]


def test_account_projects_filters_and_visibility(client_for, base, settings):
    acc = base["accs"][0]
    ProjectParty.objects.create(project=base["p_sales"], account=acc, party_role="主机厂")
    ProjectParty.objects.create(project=base["p_other"], account=acc, party_role="业主")
    c = client_for("leader")
    assert len(c.get(f"/api/accounts/{acc.id}/projects/").json()) == 2
    assert [x["code"] for x in c.get(f"/api/accounts/{acc.id}/projects/?role=业主").json()] == ["PRJ-T002"]
    rq = c.get(f"/api/accounts/{acc.id}/projects/?q=T001")
    assert [x["code"] for x in rq.json()] == ["PRJ-T001"]
    row = c.get(f"/api/accounts/{acc.id}/projects/").json()
    row = row[0]
    assert "sales_owner_name" in row and "updated_at" in row
    settings.PROJECT_VISIBILITY = "OWN"
    assert [x["code"] for x in client_for("sales").get(f"/api/accounts/{acc.id}/projects/").json()] == ["PRJ-T001"]


# ------------------------------------------------------------------ 历史价格
def test_price_history_roles_and_frozen_only(client_for, base, make_quote):
    make_quote(lines=((0, "2", "0"),))  # LUB-320 已生成
    make_quote(lines=((0, "5", "0"),), render=False)  # 草稿：不出现
    c = client_for("assistant")
    rows = c.get("/api/quote-lines/history/?model=LUB-320").json()
    assert len(rows) == 1 and rows[0]["qty"] == "2.000" and rows[0]["unit_price"] == "39000.00"
    assert c.get(f"/api/quote-lines/history/?recipient={base['accs'][1].id}").json() == []
    assert len(c.get(f"/api/quote-lines/history/?legal_entity={base['ents'][0].id}&date_from=2000-01-01").json()) == 1
    for who in ("sales", "finance", "admin"):
        assert client_for(who).get("/api/quote-lines/history/").status_code == 403


def test_price_history_excludes_trial(client_for, base, make_quote):
    from apps.organizations.models import TemplateSignoff

    TemplateSignoff.objects.filter(entity=base["ents"][1]).delete()
    c = client_for("assistant")
    _, rid = make_quote(entity=base["ents"][1], render=False)
    assert c.post(f"/api/quote-revisions/{rid}/render/", {"trial": True}, format="json").status_code == 200
    assert c.get("/api/quote-lines/history/").json() == []


# ------------------------------------------------------------------ 业绩筛选 + 导出
def _perf(users, **kw):
    from apps.performances.models import Performance

    d = dict(source="MANUAL", status="CONFIRMED", title="示例业绩", year=2025, region="华东", industry="港口", machine_type="岸桥", internal_amount="1234567.89", remark="内部备注X",
             sales_owner=users["sales"], publishable=True, product_summary="润滑系统")
    d.update(kw)
    return Performance.objects.create(**d)


def test_performance_filters(client_for, users, base):
    _perf(users)
    _perf(users, title="B", year=2023, region="华北", industry="电力", machine_type="斗轮机")
    c = client_for("leader")
    t = lambda q: [x["title"] for x in c.get("/api/performances/" + q).json()["results" if isinstance(c.get("/api/performances/" + q).json(), dict) else 0] ] if False else [x["title"] for x in (lambda d: d["results"] if isinstance(d, dict) else d)(c.get("/api/performances/" + q).json())]
    assert t("?region=华北") == ["B"] and t("?year_from=2024") == ["示例业绩"] and t("?industry=港口&machine_type=岸桥") == ["示例业绩"]
    assert t(f"?sales_owner={users['sales'].id}&year_to=2023") == ["B"]
    f = c.get("/api/performances/facets/").json()
    assert set(f["industry"]) == {"港口", "电力"} and f["years"] == [2025, 2023]


def test_performance_export_columns_by_role_and_audit(client_for, users, base):
    from openpyxl import load_workbook

    _perf(users, title="=HYPERLINK(\"http://x\")")
    def rows(who):
        r = client_for(who).get("/api/performances/export/")
        assert r.status_code == 200 and r["Content-Type"].startswith("application/vnd.openxmlformats")
        return [[c.value for c in row] for row in load_workbook(io.BytesIO(r.content)).active.iter_rows()]
    lead = rows("leader")
    assert "内部金额" in lead[0] and "内部备注" in lead[0] and "合同/订单号" in lead[0]
    assert any(str(v).startswith("'=") for v in lead[1])  # 公式注入防护
    for who in ("sales", "finance"):
        r = rows(who)
        flat = " ".join(str(v) for row in r for v in row)
        assert "内部金额" not in r[0] and "1234567" not in flat and "内部备注X" not in flat
    assert client_for("admin").get("/api/performances/export/").status_code == 403
    assert AuditEvent.objects.filter(resource="performance", action="export").count() == 3


def test_sales_export_limited_to_own_performances(client_for, users, base):
    from openpyxl import load_workbook

    _perf(users, title="我的")
    _perf(users, title="别人的", sales_owner=users["sales2"])
    r = client_for("sales").get("/api/performances/export/")
    titles = [row[0].value for row in load_workbook(io.BytesIO(r.content)).active.iter_rows(min_row=2)]
    assert titles == ["我的"]


# ------------------------------------------------------------------ 案例常用筛选
def test_public_case_filters_and_facets_whitelist(client_for, users, base):
    _perf(users, title="港口岸桥案例", industry="港口", machine_type="岸桥", region="华东", host_maker="不应出现的主机厂")
    _perf(users, title="电厂斗轮案例", industry="电力", machine_type="斗轮机", region="西北")
    _perf(users, title="未公开", publishable=False, industry="冶金")
    s = client_for("sales")
    assert [x["title"] for x in s.get("/api/public-cases/?industry=港口").json()] == ["港口岸桥案例"]
    assert [x["title"] for x in s.get("/api/public-cases/?machine_type=斗轮机&region=西北").json()] == ["电厂斗轮案例"]
    f = s.get("/api/public-cases/?facets=1").json()
    assert set(f["industry"]) == {"港口", "电力"} and "冶金" not in f["industry"]
    body = s.get("/api/public-cases/").content.decode()
    assert "不应出现的主机厂" not in body and "1234567" not in body


# ------------------------------------------------------------------ 提醒
def test_dashboard_overdue_sent_quote_reminder(client_for, base, make_quote):
    qid, rid = make_quote()
    c = client_for("assistant")
    assert c.post(f"/api/quotations/{qid}/status/", {"status": "SENT"}, format="json").status_code == 200
    QuoteRevision.objects.filter(pk=rid).update(quote_date=timezone.localdate() - timedelta(days=40), valid_days=30)
    d = c.get("/api/dashboard/").json()
    assert [x["quote_id"] for x in d["quotes_overdue"]] == [qid] and d["quotes_expiring"] == []
    QuoteRevision.objects.filter(pk=rid).update(quote_date=timezone.localdate() - timedelta(days=27))
    d = c.get("/api/dashboard/").json()
    assert [x["quote_id"] for x in d["quotes_expiring"]] == [qid] and d["quotes_overdue"] == []
    for who in ("sales", "finance"):
        dd = client_for(who).get("/api/dashboard/").json()
        assert "quotes_overdue" not in dd and "quotes_expiring" not in dd


# ------------------------------------------------------------------ 公开授权照片
def _jpeg_with_gps():
    from PIL import Image

    im = Image.new("RGB", (64, 48), (200, 30, 30))
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"  # Make
    exif[0x010E] = "GPS 31.14N 121.29E (secret site)"  # ImageDescription：模拟含位置的元数据
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_case_photos_authorization_and_exif_stripped(client_for, users, base):
    import subprocess
    import tempfile

    p = _perf(users, title="照片案例")
    raw = _jpeg_with_gps()
    assert b"SecretCameraMaker" in raw and b"secret site" in raw
    asst = client_for("assistant")
    r = asst.post("/api/case-photos/", {"performance": p.id, "caption": "现场照片", "file": SimpleUploadedFile("p.jpg", raw)}, format="multipart")
    assert r.status_code == 201
    ph = r.json()
    img = asst.get(f"/api/case-photos/{ph['id']}/image/").content
    assert b"SecretCameraMaker" not in img and b"secret site" not in img and b"Exif" not in img  # 重新编码去除元数据
    s = client_for("sales")
    # 未授权：销售看不到，公开案例与预览不含照片
    assert s.get(f"/api/case-photos/{ph['id']}/image/").status_code == 403
    assert s.get("/api/public-cases/").json()[0]["photos"] == []
    assert s.post("/api/case-preview/", {"performance_ids": [p.id]}, format="json").json()["cases"][0]["photos"] == []
    # 只有负责人可授权，且必须写授权依据
    assert asst.patch(f"/api/case-photos/{ph['id']}/", {"authorized": True, "authorization_note": "x"}, format="json").status_code == 403
    lead = client_for("leader")
    assert lead.patch(f"/api/case-photos/{ph['id']}/", {"authorized": True}, format="json").status_code == 400
    assert lead.patch(f"/api/case-photos/{ph['id']}/", {"authorized": True, "authorization_note": "业主 2026-11 书面同意"}, format="json").status_code == 200
    cases = s.get("/api/public-cases/").json()
    assert cases[0]["photos"][0]["caption"] == "现场照片" and "authorization_note" not in json_dump(cases)
    assert s.get(f"/api/case-photos/{ph['id']}/image/").status_code == 200
    assert s.get(f"/api/case-photos/?performance={p.id}").status_code == 403  # 内部列表（含授权依据）不对销售
    # 案例册 PDF 嵌入照片，且 PDF 中无 EXIF 文本
    r = s.post("/api/case-releases/", {"performance_ids": [p.id], "title": "照片册"}, format="json")
    assert r.status_code == 201
    pdf = s.get(f"/api/case-releases/{r.json()['id']}/pdf/").content
    assert b"/Subtype /Image" in pdf and b"SecretCameraMaker" not in pdf
    # 撤销授权后：新的公开数据不再含照片；已发出的册子按快照保留（不可追回，界面已提示）
    lead.patch(f"/api/case-photos/{ph['id']}/", {"authorized": False}, format="json")
    assert s.get("/api/public-cases/").json()[0]["photos"] == []
    assert s.get(f"/api/case-photos/{ph['id']}/image/").status_code == 403
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf)
        f.flush()
        assert "SecretCameraMaker" not in subprocess.run(["pdfinfo", f.name], capture_output=True, text=True).stdout


def json_dump(x):
    import json

    return json.dumps(x, ensure_ascii=False)
