"""升级的数据库阶段（RC2-R03，由新版本程序执行，被 07-升级程序 调用）。

    python manage.py ish_upgrade --pre-backup <已验证的 pre-upgrade 备份目录> [--plan-only] [--json]

前提（由升级器 upgrade.ps1 保证并在此复核）：
- 旧程序在旧运行时下已经完成一致性备份，并已用旧程序做过恢复演练；升级 ID 与该备份一一对应；
- 升级器持有全局运维锁，并通过 ISH_OPS_TOKEN 传入；维护标记由升级器以同一 token 放置；
- 应用服务已停止，数据库服务运行。
本命令**不再自行备份**（RC1 在迁移前于新模型下再做一次备份，含混且依赖新代码）。

步骤：校验 pre-upgrade 备份（SHA256/清单）→ 数据库大版本与 schema 兼容性 → migrate → verify_integrity（含历史报价 PDF 原字节哈希）。
失败：返回非 0，维护标记保持（由升级器执行回滚：恢复旧发行文件 + 改名切回升级前数据库快照）。
"""
import json
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

SUPPORTED_PG_MAJOR = 16


class Command(BaseCommand):
    help = "升级数据库阶段：校验预备份 → 兼容性 → 迁移 → 完整性校验"

    def add_arguments(self, parser):
        parser.add_argument("--pre-backup", default=None, help="升级器已完成并验证的 pre-upgrade 备份目录")
        parser.add_argument("--backup-dest", default=None, help="（已废弃，保留兼容）")
        parser.add_argument("--pg-bin", default=None)
        parser.add_argument("--plan-only", action="store_true")
        parser.add_argument("--lock-wait", type=float, default=0)

    def handle(self, *args, pre_backup, backup_dest, pg_bin, plan_only, lock_wait, **kw):
        from apps.common.opslock import OpsBusy, maintenance_owner, ops_lock

        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        pending = [f"{m.app_label}.{m.name}" for m, _ in plan]
        with connection.cursor() as c:
            c.execute("SHOW server_version_num")
            major = int(c.fetchone()[0]) // 10000
        applied = set(f"{a}.{n}" for a, n in executor.loader.applied_migrations)
        unknown = sorted(applied - set(f"{a}.{n}" for a, n in executor.loader.disk_migrations))
        info = {"version": settings.APP_VERSION, "pg_major": major, "pending": pending, "unknown_applied": unknown}
        self.stdout.write(json.dumps(info, ensure_ascii=False))
        if major != SUPPORTED_PG_MAJOR:
            raise CommandError(f"数据库大版本 {major} 与本程序支持的 {SUPPORTED_PG_MAJOR} 不一致：跨大版本升级需要 pg_upgrade，本升级器拒绝执行")
        if unknown:
            raise CommandError(f"数据库包含本程序不认识的迁移 {unknown[:5]}（数据库来自更新的版本）：拒绝降级")
        if plan_only:
            return
        try:
            with ops_lock("upgrade-db", wait=lock_wait) as token:
                if not pre_backup:
                    raise CommandError("必须指定 --pre-backup（升级器在旧程序下完成并验证的备份）。本命令不再自行备份。")
                mo = maintenance_owner()
                if not mo or mo.get("token") != token:
                    raise CommandError("升级必须在升级器放置的维护标记下进行（未发现同一运维 token 的维护标记）")
                from apps.accounts.management.commands.ish_verify_backup import verify_backup_dir

                man = verify_backup_dir(Path(pre_backup))
                self.stdout.write(f"pre-upgrade 备份校验通过：{pre_backup}（版本 {man.get('app_version')}，{len(man.get('files', []))} 个私有文件）")
                status_file = settings.DATA_DIR / "upgrade_status.json"
                from apps.common.opslock import write_status_file

                try:
                    call_command("migrate", interactive=False, stdout=self.stdout)
                    call_command("verify_integrity", stdout=self.stdout)
                except Exception as e:
                    write_status_file(status_file, {"ok": False, "stage": "migrate/verify", "version": settings.APP_VERSION, "error": str(e)[:500], "pre_backup": str(pre_backup)})
                    raise CommandError(f"迁移或完整性校验失败（维护标记保持，由升级器回滚）：{e}")
                write_status_file(status_file, {"ok": True, "version": settings.APP_VERSION, "applied": pending, "pre_backup": str(pre_backup)})
        except OpsBusy as e:
            raise CommandError(str(e), returncode=75)
        self.stdout.write(self.style.SUCCESS(f"数据库升级完成：已应用 {len(pending)} 个迁移，完整性校验通过"))
