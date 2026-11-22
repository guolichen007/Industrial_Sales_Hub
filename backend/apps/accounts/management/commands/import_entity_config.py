r"""显式导入出具主体真实配置（安装端执行，不随安装自动运行）。

用法：python manage.py import_entity_config <真实配置.json> --authorized-by <负责人用户名> [--dry-run]

- 必须指定一个启用的“部门负责人”账号作为授权人（业务授权，不是 Windows 管理员）；
- 只更新 JSON 中出现的字段；不出现的字段保持不变；不存在的主体会新建；
- 永不修改模板签收状态（JSON 中出现签收字段直接拒绝）；
- 每个主体写一条审计（动作 import_config，记录变更字段名，不记录银行账号等敏感值）；
- --dry-run 只打印差异，不写库。
真实配置文件含公司全称、税号、银行账号，只能保存在安装主机的 config\ 下，禁止提交到公共仓库。
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.management.commands.seed_entities import load_entity_rows
from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.audit.service import _clean
from apps.common.roles import LEADER
from apps.organizations.models import LegalEntity


class Command(BaseCommand):
    help = "导入/更新出具主体真实配置（需负责人授权）"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--authorized-by", required=True, help="授权的部门负责人用户名")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, path, authorized_by, dry_run, **kw):
        leader = User.objects.filter(username=authorized_by or "", role=LEADER, is_active=True).first()
        if not leader:
            raise CommandError("必须由启用状态的“部门负责人”账号授权（--authorized-by <用户名>）；管理员、商务、销售、财务均不可")
        rows = load_entity_rows(path)
        with transaction.atomic():
            for row in rows:
                obj = LegalEntity.objects.filter(code=row["code"]).first()
                if obj is None:
                    changed = sorted(k for k in row if k != "code")
                    if not dry_run:
                        obj = LegalEntity.objects.create(**row)
                    action = "新增"
                else:
                    changed = sorted(k for k, v in row.items() if k != "code" and getattr(obj, k) != v)
                    if not dry_run and changed:
                        for k in changed:
                            setattr(obj, k, row[k])
                        obj.save()
                    action = "更新" if changed else "无变化"
                self.stdout.write(f"{'[预览] ' if dry_run else ''}{action}：{row['code']} 字段 {changed}")
                if not dry_run and changed:
                    AuditEvent.objects.create(actor=leader, actor_name=leader.name, action="import_config", resource="legal_entity",
                                              resource_id=str(obj.id), summary=f"导入出具主体配置 {row['code']}（{action}）",
                                              detail=_clean({"fields": changed, "source": "import_entity_config"}))
            if dry_run:
                transaction.set_rollback(True)
