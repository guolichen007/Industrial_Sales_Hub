"""模拟安装的数据指纹：表行数 + 迁移 + 私有文件哈希 + 已生成报价 PDF 哈希（manage.py shell 执行，输出 JSON）。"""
import hashlib
import json

from django.conf import settings
from django.db import connection

from apps.accounts.management.commands.ish_backup import table_counts
from apps.quotations.models import QuoteRevision

root = settings.PRIVATE_STORAGE_DIR
files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()} if root.exists() else {}
with connection.cursor() as c:
    c.execute("SELECT app||'.'||name FROM django_migrations ORDER BY 1")
    migs = [r[0] for r in c.fetchall()]
    c.execute("SELECT to_regclass('sim_upgrade_marker') IS NOT NULL")
    marker = c.fetchone()[0]
print("FINGERPRINT=" + json.dumps({"counts": table_counts(), "files": files, "migrations": migs, "pdfs": {r.id: r.pdf_sha256 for r in QuoteRevision.objects.filter(status="GENERATED")}, "sim_marker_table": marker}, ensure_ascii=False, sort_keys=True))
