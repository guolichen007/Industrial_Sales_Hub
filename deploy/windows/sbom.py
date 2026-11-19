"""从 site-packages 的 *.dist-info 生成简单 SBOM（名称、版本、许可证、来源），并复制各包许可证文件。"""
import json
import shutil
import sys
from email.parser import Parser
from pathlib import Path

site, lic_dir = Path(sys.argv[1]), Path(sys.argv[2])
out = []
(lic_dir / "python-packages").mkdir(parents=True, exist_ok=True)
for d in sorted(site.glob("*.dist-info")):
    meta = Parser().parsestr((d / "METADATA").read_text(encoding="utf-8", errors="replace"))
    lic = meta.get("License-Expression") or meta.get("License") or ""
    if not lic or len(lic) > 80:
        cls = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
        lic = "; ".join(cls) or (lic[:80] + "…" if lic else "见许可证文件")
    files = [p for p in d.rglob("*") if p.is_file() and p.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))]
    for p in files:
        shutil.copy2(p, lic_dir / "python-packages" / f"{meta['Name']}-{p.name}")
    out.append({"name": meta["Name"], "version": meta["Version"], "license": lic, "home": meta.get("Home-page") or "", "license_files": [p.name for p in files]})
print(json.dumps({"format": "ish-sbom-1", "python_packages": out}, ensure_ascii=False, indent=1))
