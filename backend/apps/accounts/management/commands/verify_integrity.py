"""完整性校验（恢复演练用）：用户角色、报价快照/PDF 哈希、案例 PDF、附件、项目多参与方。"""
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.common.pdfkit import canonical_json, read_private, sha256_bytes
from apps.files.models import Attachment
from apps.performances.models import CaseRelease
from apps.projects.models import ProjectParty
from apps.quotations.models import QuoteRevision


class Command(BaseCommand):
    help = "校验备份恢复后的数据与文件一致性"

    def handle(self, *args, **kw):
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
        for a in Attachment.objects.all():
            try:
                if sha256_bytes(read_private(a.storage_key)) != a.sha256:
                    errors.append(f"附件哈希不一致 {a.id}")
            except FileNotFoundError:
                errors.append(f"附件缺失 {a.id}")
        self.stdout.write(f"用户 {User.objects.count()}（角色：{sorted(set(User.objects.values_list('role', flat=True)))}）")
        self.stdout.write(f"已生成报价版本 {revs.count()}，案例册 {CaseRelease.objects.count()}，附件 {Attachment.objects.count()}，项目参与关系 {ProjectParty.objects.count()}")
        if errors:
            raise CommandError("完整性校验失败：\n" + "\n".join(errors))
        self.stdout.write(self.style.SUCCESS("完整性校验通过"))
