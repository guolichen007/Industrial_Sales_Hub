"""导入三家报价出具主体的主数据配置（JSON），不导入任何客户、项目或账号。

用法：python manage.py seed_entities <entities.json>
JSON：[{"code":"A","short_name":"…","name":"…","template_key":"A","price_basis":"NET",...}, ...]
已存在的主体（按 code）只更新 JSON 中出现的字段；重复执行安全。
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.organizations.models import LegalEntity

FIELDS = {f.name for f in LegalEntity._meta.get_fields() if getattr(f, "concrete", False) and f.name != "id"}


class Command(BaseCommand):
    help = "导入/更新出具主体配置"

    def add_arguments(self, parser):
        parser.add_argument("path")

    @transaction.atomic
    def handle(self, *args, path, **kw):
        p = Path(path)
        if not p.exists():
            raise CommandError(f"文件不存在：{p}")
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        if not isinstance(data, list):
            raise CommandError("JSON 顶层必须是数组")
        for row in data:
            unknown = set(row) - FIELDS
            if unknown:
                raise CommandError(f"未知字段：{sorted(unknown)}")
            code = row.get("code")
            if not code:
                raise CommandError("每个主体必须有 code")
            obj, created = LegalEntity.objects.get_or_create(code=code, defaults={k: v for k, v in row.items() if k != "code"})
            if not created:
                for k, v in row.items():
                    setattr(obj, k, v)
                obj.save()
            self.stdout.write(f"{'新增' if created else '更新'}：{obj.code} {obj.name}")
