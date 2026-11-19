"""升级前检查与迁移（L4 升级演练）。

python manage.py ish_upgrade --backup-dest <目录> [--pg-bin ...]
1. 检查 MODE、数据库连接、待执行迁移列表；
2. 先执行一次一致性备份（label=pre-upgrade），失败则中止；
3. 进入维护模式执行 migrate；迁移失败：维护模式保持开启（禁止继续写入）并返回非 0，提示按备份回滚；
4. 迁移成功：运行 verify_integrity（旧报价 PDF 原字节哈希、附件、案例）后解除维护。
"""
import json

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "升级：预备份 → 迁移 → 完整性校验"

    def add_arguments(self, parser):
        parser.add_argument("--backup-dest", required=True)
        parser.add_argument("--pg-bin", default=None)
        parser.add_argument("--plan-only", action="store_true")

    def handle(self, *args, backup_dest, pg_bin, plan_only, **kw):
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        pending = [f"{m.app_label}.{m.name}" for m, _ in plan]
        self.stdout.write(f"当前版本 {settings.APP_VERSION}，待执行迁移 {len(pending)} 个：{pending}")
        if plan_only:
            return
        call_command("ish_backup", dest=backup_dest, pg_bin=pg_bin, label="pre-upgrade", stdout=self.stdout)
        settings.MAINTENANCE_FLAG.write_text("upgrade", encoding="utf-8")
        try:
            call_command("migrate", interactive=False, stdout=self.stdout)
        except Exception as e:
            # 保持维护模式：数据库可能处于部分迁移状态，禁止继续写入，必须按预备份回滚
            status = {"ok": False, "stage": "migrate", "error": str(e)[:500], "action": "保持维护模式；请执行 05-恢复备份 回滚到 pre-upgrade 备份，并恢复旧版 app 目录"}
            (settings.DATA_DIR / "upgrade_status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
            raise CommandError(f"迁移失败，系统保持只读维护模式。请按升级手册回滚：{e}")
        call_command("verify_integrity", stdout=self.stdout)
        settings.MAINTENANCE_FLAG.unlink(missing_ok=True)
        (settings.DATA_DIR / "upgrade_status.json").write_text(json.dumps({"ok": True, "version": settings.APP_VERSION, "applied": pending}, ensure_ascii=False, indent=1), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"升级完成：已应用 {len(pending)} 个迁移，完整性校验通过"))
