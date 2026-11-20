"""docs/api.md 与实际路由一致：每个 /api/ 路由前缀都必须在文档中出现（防止文档再次过期）。"""
import re
from pathlib import Path

from django.urls import get_resolver


def _routes():
    out = set()

    def walk(patterns, prefix=""):
        for p in patterns:
            s = prefix + str(p.pattern)
            if hasattr(p, "url_patterns"):
                walk(p.url_patterns, s)
            else:
                out.add(s)

    walk(get_resolver().url_patterns)
    return out


def test_every_api_route_family_is_documented():
    doc = (Path(__file__).resolve().parents[2] / "docs" / "api.md").read_text(encoding="utf-8")
    missing = set()
    for r in _routes():
        if not r.startswith("api/"):
            continue
        tail = r[4:]
        first = re.split(r"[/(\\^$?]", tail.lstrip("^"))[0]
        if not first or first.startswith("<"):
            continue
        if f"`{first}" not in doc and f"{first}/" not in doc:
            missing.add(first)
    assert not missing, f"docs/api.md 缺少这些接口族：{sorted(missing)}"
    assert "finance/" in doc and "403" in doc  # 已关闭能力的说明仍在
