"""G4 预备：Excel 导入预检/幂等提交、附件类型签名与下载鉴权。"""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook

from apps.imports.service import TEMPLATES
from apps.performances.models import Performance


def _xlsx(kind, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(TEMPLATES[kind])
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_U09_performance_import_preview_commit_idempotent(client_for):
    rows = [[f"历史项目{i}", 2020 + i % 5, "华东", "港口", "制动器", "斗轮机", "", "", "", 10000 + i, "", f"HT-{i}"] for i in range(100)]
    rows.append(["历史项目1", 2021, "华东", "港口", "制动器", "斗轮机", "", "", "", 1, "", "HT-1"])  # 文件内重复
    rows.append(["", "abc", "", "", "", "", "", "", "", "x", "", ""])  # 错误行
    c = client_for("assistant")
    data = _xlsx("performances", rows)
    r = c.post("/api/import-batches/preview/", {"kind": "performances", "file": SimpleUploadedFile("p.xlsx", data)}, format="multipart")
    assert r.status_code == 200, r.content
    s = r.json()["summary"]
    assert s == {"new": 100, "duplicate": 0, "error": 2, "total": 102}
    err = [x for x in r.json()["rows"] if x["status"] == "error"]
    assert err[0]["row"] == 102 and err[1]["row"] == 103  # 带行号
    assert Performance.objects.count() == 0  # 预检不写库
    bid = r.json()["id"]
    assert c.post(f"/api/import-batches/{bid}/commit/").json()["result"] == {"created": 100, "skipped": 2}
    assert c.post(f"/api/import-batches/{bid}/commit/").json()["already"] is True
    # 再导入同一文件：全部识别为重复
    r = c.post("/api/import-batches/preview/", {"kind": "performances", "file": SimpleUploadedFile("p.xlsx", data)}, format="multipart")
    assert r.json()["summary"]["duplicate"] == 100
    c.post(f"/api/import-batches/{r.json()['id']}/commit/")
    assert Performance.objects.count() == 100
    assert set(Performance.objects.values_list("status", flat=True)) == {"PENDING"}


def test_import_rejects_wrong_header_and_xlsm(client_for):
    c = client_for("assistant")
    wb = Workbook()
    wb.active.append(["错误表头"])
    b = io.BytesIO()
    wb.save(b)
    r = c.post("/api/import-batches/preview/", {"kind": "products", "file": SimpleUploadedFile("p.xlsx", b.getvalue())}, format="multipart")
    assert r.status_code == 400 and r.json()["code"] == "bad_template"
    r = c.post("/api/import-batches/preview/", {"kind": "products", "file": SimpleUploadedFile("p.xlsm", b.getvalue())}, format="multipart")
    assert r.status_code == 400
    assert client_for("sales").post("/api/import-batches/preview/", {"kind": "products"}, format="multipart").status_code == 403


def test_product_import(client_for):
    c = client_for("leader")
    data = _xlsx("products", [["X-1", "X-1", "新制动器", "制动器", "B品牌", "台", 13, "1,200.50", "说明", "力矩=600;电压=380V"]])
    r = c.post("/api/import-batches/preview/", {"kind": "products", "file": SimpleUploadedFile("p.xlsx", data)}, format="multipart")
    assert r.json()["summary"]["new"] == 1
    c.post(f"/api/import-batches/{r.json()['id']}/commit/")
    p = c.get("/api/products/?q=X-1").json()[0]
    assert p["tax_rate"] == "0.1300" and p["base_price"] == "1200.50" and p["params"][0]["label"] == "力矩"


def test_attachment_signature_and_scope(client_for, base):
    pid = base["p_sales"].id
    c = client_for("sales")
    fake = SimpleUploadedFile("evil.pdf", b"<script>alert(1)</script>", content_type="application/pdf")
    r = c.post("/api/attachments/", {"owner_type": "project", "owner_id": pid, "file": fake}, format="multipart")
    assert r.status_code == 400 and r.json()["code"] == "bad_signature"
    r = c.post("/api/attachments/", {"owner_type": "project", "owner_id": pid, "file": SimpleUploadedFile("x.exe", b"MZ")}, format="multipart")
    assert r.status_code == 400
    ok = SimpleUploadedFile("现场照片.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    r = c.post("/api/attachments/", {"owner_type": "project", "owner_id": pid, "file": ok}, format="multipart")
    assert r.status_code == 201
    aid = r.json()["id"]
    assert c.get(f"/api/attachments/{aid}/download/").status_code == 200
    # 其他销售看不到该项目 → 不能下载
    assert client_for("sales2").get(f"/api/attachments/{aid}/download/").status_code == 403
    # 销售不能读财务附件
    r = client_for("finance").post("/api/attachments/", {"owner_type": "finance", "owner_id": pid, "file": SimpleUploadedFile("invoice.pdf", b"%PDF-1.4 x")}, format="multipart")
    assert r.status_code == 201
    assert c.get(f"/api/attachments/{r.json()['id']}/download/").status_code == 403
    assert c.get(f"/api/attachments/?owner_type=finance&owner_id={pid}").status_code == 403
