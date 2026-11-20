"""安装初始化：仅创建缺失的报价出具主体（create-only），**从不修改已有主体**。

用法：python manage.py seed_entities <entities.json>
- 已存在的主体（按 code）完全不动：不改名称、条款、价税口径、签收状态。重复安装/修复安全（RC2-R01）。
- JSON 中不允许出现 template_verified：模板签收只能由负责人在系统内按模板版本完成。
- 需要用真实配置覆盖已有主体时，使用独立命令 import_entity_config（必须指定负责人授权并写审计）。
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.organizations.models import LegalEntity

FIELDS = {f.name for f in LegalEntity._meta.get_fields() if getattr(f, "concrete", False) and f.name != "id"}
FORBIDDEN = {"template_verified", "verified_template_version", "verified_by", "verified_at"}


def load_entity_rows(path) -> list:
    p = Path(path)
    if not p.exists():
        raise CommandError(f"文件不存在：{p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise CommandError(f"JSON 格式错误：{e}")
    if not isinstance(data, list):
        raise CommandError("JSON 顶层必须是数组")
    for row in data:
        if not isinstance(row, dict) or not row.get("code"):
            raise CommandError("每个主体必须是对象且有 code")
        bad = set(row) & FORBIDDEN
        if bad:
            raise CommandError(f"配置文件不得包含签收字段 {sorted(bad)}：模板签收只能由负责人在系统内完成")
        unknown = set(row) - FIELDS
        if unknown:
            raise CommandError(f"未知字段：{sorted(unknown)}")
    return data


class Command(BaseCommand):
    help = "安装初始化：只创建缺失的出具主体（不修改已有主体）"

    def add_arguments(self, parser):
        parser.add_argument("path")

    @transaction.atomic
    def handle(self, *args, path, **kw):
        rows = load_entity_rows(path)
        for row in rows:
            if LegalEntity.objects.filter(code=row["code"]).exists():
                self.stdout.write(f"已存在，保持不变：{row['code']}")
                continue
            obj = LegalEntity.objects.create(**row)
            self.stdout.write(f"新增：{obj.code} {obj.name}")
