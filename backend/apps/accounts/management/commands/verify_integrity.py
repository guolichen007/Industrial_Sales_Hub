"""完整性校验（恢复演练/升级后使用）：用户角色、表行数、报价快照/PDF 哈希、案例 PDF、附件、项目多参与方。

python manage.py verify_integrity [--expect-manifest <备份 manifest.json>]
"""
import json

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.common.pdfkit import canonical_json, read_private, sha256_bytes
from apps.files.models import Attachment
from apps.performances.models import CaseRelease
from apps.projects.models import ProjectParty
from apps.quotations.models import QuoteRevision


class Command(BaseCommand):
    help = "校验数据与私有文件一致性"

    def add_arguments(self, parser):
        parser.add_argument("--expect-manifest", default=None)
        parser.add_argument("--private-dir", default=None, help="在指定私有目录（例如恢复 staging 目录）上校验文件")

    def handle(self, *args, expect_manifest=None, private_dir=None, **kw):
        from pathlib import Path

        from django.conf import settings

        if private_dir:
            settings.PRIVATE_STORAGE_DIR = Path(private_dir)
        errors = []
        revs = QuoteRevision.objects.filter(status="GENERATED")
        for r in revs:
            if sha256_bytes(canonical_json(r.snapshot)) != r.snapshot_sha256:
                errors.append(f"报价快照哈希不一致 {r.id}")
            try:
                if sha256_bytes(read_private(r.pdf_storage_key)) != r.pdf_sha256:
                    errors.append(f"报价 PDF 哈希不一致 {r.id}")
            except FileNotFoundError:
                errors.append(f"报价 PDF 缺失 {r.id}")
        for c in CaseRelease.objects.all():
            try:
                if sha256_bytes(read_private(c.pdf_storage_key)) != c.pdf_sha256:
                    errors.append(f"案例 PDF 哈希不一致 {c.id}")
            except FileNotFoundError:
                errors.append(f"案例 PDF 缺失 {c.id}")
        from apps.performances.models import CasePhoto

        for ph in CasePhoto.objects.all():
            try:
                if sha256_bytes(read_private(ph.storage_key)) != ph.sha256:
                    errors.append(f"案例照片哈希不一致 {ph.id}")
            except FileNotFoundError:
                errors.append(f"案例照片缺失 {ph.id}")
        from apps.organizations.models import LegalEntity

        for e in LegalEntity.objects.exclude(logo_key=""):
            try:
                if sha256_bytes(read_private(e.logo_key)) != e.logo_sha256:
                    errors.append(f"主体 Logo 哈希不一致 {e.id}")
            except FileNotFoundError:
                errors.append(f"主体 Logo 缺失 {e.id}")
        for a in Attachment.objects.all():
            try:
                if sha256_bytes(read_private(a.storage_key)) != a.sha256:
                    errors.append(f"附件哈希不一致 {a.id}")
            except FileNotFoundError:
                errors.append(f"附件缺失 {a.id}")
        if expect_manifest:
            from apps.accounts.management.commands.ish_backup import sha256_file, table_counts

            man = json.loads(open(expect_manifest, encoding="utf-8").read())
            now = table_counts()
            for label, n in man.get("table_counts", {}).items():
                if now.get(label) != n:
                    errors.append(f"表行数与备份清单不一致：{label} 期望 {n} 实际 {now.get(label)}")
            # 私有目录必须与备份清单逐文件一致（不多不少，哈希相同）
            root = settings.PRIVATE_STORAGE_DIR
            want = {f["path"]: f["sha256"] for f in man.get("files", [])}
            have = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} if root.exists() else set()
            for rel in sorted(set(want) - have)[:20]:
                errors.append(f"私有文件缺失：{rel}")
            for rel in sorted(have - set(want))[:20]:
                errors.append(f"私有目录含备份清单之外的文件：{rel}")
            for rel in sorted(set(want) & have):
                if sha256_file(root / rel) != want[rel]:
                    errors.append(f"私有文件哈希不一致：{rel}")
        roles = sorted(set(User.objects.values_list("role", flat=True)))
        self.stdout.write(f"用户 {User.objects.count()}（角色：{roles}）")
        self.stdout.write(f"已生成报价版本 {revs.count()}，案例册 {CaseRelease.objects.count()}，附件 {Attachment.objects.count()}，项目参与关系 {ProjectParty.objects.count()}")
        if errors:
            raise CommandError("完整性校验失败：\n" + "\n".join(errors[:50]))
        self.stdout.write(self.style.SUCCESS("完整性校验通过"))
