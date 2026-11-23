"""文档静态守卫（6912049 审查 R75-05）：任何文档/脚本都不得出现“按 Industrial Sales Hub* 通配直接 Remove-NetFirewallRule”的命令，
否则会删除管理员手工建立的 HTTPS 443 规则（与 RC3-T17 承诺冲突）。只读列举（Select/Get-NetFirewallAddressFilter）或先 Where-Object 精确过滤允许。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_no_wildcard_firewall_rule_removal_in_docs_and_scripts():
    bad = []
    files = list((ROOT / "docs").rglob("*.md")) + list((ROOT / "deploy" / "windows").rglob("*.ps1")) + list((ROOT / "deploy" / "windows").rglob("*.md")) + [ROOT / "README.md"]
    pat = re.compile(r"Get-NetFirewallRule\s+-DisplayName\s+['\"]Industrial Sales Hub\*['\"][^\n|]*\|\s*Remove-NetFirewallRule")
    for f in files:
        if f.exists() and pat.search(f.read_text(encoding="utf-8", errors="ignore")):
            bad.append(str(f.relative_to(ROOT)))
    assert not bad, bad


def test_uat_inquiry_status_expectation_matches_v02():
    t = (ROOT / "docs" / "RC3-Windows实机验收手册.md").read_text(encoding="utf-8")
    assert "以该询价新建报价 | 询价变为“已报价”" not in t and "报价中" in t
