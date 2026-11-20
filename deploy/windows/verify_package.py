"""RC2 安装包独立复核（只读）：python3 -I verify_package.py <zip> <SHA256SUMS.txt> <expected_git_sha>"""
import hashlib
import json
import re
import sys
import zipfile
from pathlib import PurePosixPath

zp, sums_path, want_sha = sys.argv[1], sys.argv[2], sys.argv[3]
res = {"zip": zp}
h = hashlib.sha256()
with open(zp, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
res["zip_sha256"] = h.hexdigest()
z = zipfile.ZipFile(zp)
names = z.namelist()
root = names[0].split("/")[0]
bad = []
for n in names:
    p = PurePosixPath(n)
    if n.startswith("/") or ".." in p.parts or re.match(r"^[A-Za-z]:", n) or "\\" in n:
        bad.append(f"路径穿越/非法：{n}")
    if not n.startswith(root + "/"):
        bad.append(f"不在单一根目录：{n}")
    info = z.getinfo(n)
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        bad.append(f"符号链接：{n}")
res["entries"] = len(names)
res["root"] = root
listed = {}
for line in open(sums_path, encoding="utf-8"):
    if line.strip():
        d, rel = line.rstrip("\n").split("  ", 1)
        listed[rel] = d
inner_sums = z.read(f"{root}/SHA256SUMS.txt").decode("utf-8")
res["sums_file_equals_inner"] = inner_sums == open(sums_path, encoding="utf-8").read()
ok = mism = 0
files = {n[len(root) + 1:]: n for n in names if not n.endswith("/")}
for rel, d in listed.items():
    if rel not in files:
        bad.append(f"清单中有、包中缺：{rel}")
        continue
    if hashlib.sha256(z.read(files[rel])).hexdigest() == d:
        ok += 1
    else:
        mism += 1
        bad.append(f"哈希不一致：{rel}")
extra = set(files) - set(listed) - {"SHA256SUMS.txt"}
for e in sorted(extra):
    bad.append(f"包中有、清单无：{e}")
res["manifest_items"] = len(listed)
res["hash_ok"] = ok
res["hash_mismatch"] = mism
ver = z.read(f"{root}/VERSION.txt").decode().strip()
res["version_txt"] = ver
if want_sha not in ver:
    bad.append(f"VERSION.txt 未指向 {want_sha}")
settings = z.read(f"{root}/app/backend/config/settings.py").decode()
res["app_version"] = re.search(r'APP_VERSION = "([^"]+)"', settings).group(1)
forbid_name = re.compile(r"(\.sqlite3$|\.dump$|/app\.env$|postgres-superuser|\.xlsx?$|\.xlsm$|/pgdata/|/data/private/|/backups/|/tests/|seed_demo\.py$|\.pyc$)", re.I)
forbid_text = ["博恩特", "孚尔德", "科莱德", "Bolenteck", "Kelad", "太原向明", "哈尔滨重型", "大连通达", "Demo@2026", "Ci-admin-2026x", "Lan-2026-pass", "BEGIN PRIVATE KEY", "BEGIN RSA PRIVATE KEY"]
for rel, n in files.items():
    if forbid_name.search("/" + rel):
        bad.append(f"不应包含：{rel}")
    if rel.endswith((".py", ".ps1", ".bat", ".md", ".json", ".txt", ".js", ".html", ".css", ".xml", ".cfg", ".ini")) and not rel.startswith(("runtime/", "licenses/")):
        t = z.read(n).decode("utf-8", errors="ignore")
        for s in forbid_text:
            if s in t:
                bad.append(f"含敏感文本「{s}」：{rel}")
must = ["01-安装部署.bat", "07-升级程序.bat", "09-导入主体配置.bat", "scripts/upgrade.ps1", "scripts/restore.ps1", "scripts/common.ps1", "tools/make-test-next-version.ps1",
        "runtime/python/python.exe", "runtime/pgsql/bin/postgres.exe", "runtime/pgsql/bin/pg_dump.exe", "runtime/pgsql/bin/pg_restore.exe", "runtime/winsw/WinSW-x64.exe", "runtime/vc_redist.x64.exe",
        "app/backend/apps/quotations/pdf.py", "app/backend/apps/quotations/pdf_r2.py", "app/backend/apps/accounts/management/commands/ish_restore.py", "app/frontend/dist/index.html",
        "licenses/SBOM.json", "使用说明.md", "备份恢复与升级手册.md", "Windows实机验收手册.md", "HTTPS内网证书配置手册.md", "config-templates/entities.json"]
for m in must:
    if m not in files:
        bad.append(f"缺少：{m}")
ents = json.loads(z.read(f"{root}/config-templates/entities.json").decode("utf-8-sig"))
if any("template_verified" in e for e in ents):
    bad.append("entities.json 含签收字段")
dist_js = [n for n in files if n.startswith("app/frontend/dist/") and n.endswith((".js", ".html", ".css"))]
for n in dist_js:
    if re.search(r"https?://(cdn|fonts\.googleapis|unpkg|jsdelivr)", z.read(files[n]).decode("utf-8", "ignore")):
        bad.append(f"前端引用外部 CDN：{n}")
sbom = json.loads(z.read(f"{root}/licenses/SBOM.json"))
res["sbom_python_packages"] = len(sbom.get("python_packages", []))
pgv = [n for n in files if n.startswith("runtime/python/python3") and n.endswith(".zip")]
res["python_runtime"] = pgv
res["problems"] = bad
print(json.dumps(res, ensure_ascii=False, indent=1))
sys.exit(1 if bad else 0)
