"""在模拟安装上写入虚构业务数据（manage.py shell 执行）：账号、主体、客户、项目、报价（生成 PDF）、附件。"""
import hashlib
from decimal import Decimal

from apps.accounts.models import User
from apps.common.pdfkit import store_private
from apps.files.models import Attachment
from apps.organizations.models import Account, LegalEntity
from apps.products.models import Product, ProductCategory
from apps.projects.models import Project
from apps.quotations import services
from apps.quotations.models import Quote, QuoteLine

leader, _ = User.objects.get_or_create(username="sim_leader", defaults={"role": "LEADER", "display_name": "模拟负责人"})
ent = LegalEntity.objects.order_by("code").first()
acc, _ = Account.objects.get_or_create(name="模拟客户（虚构）有限公司")
cat, _ = ProductCategory.objects.get_or_create(name="模拟类别")
prod, _ = Product.objects.get_or_create(sku="SIM-1", defaults={"model": "SIM-1", "name": "模拟产品", "category": cat, "unit": "台", "base_price": Decimal("1000"), "tax_rate": Decimal("0.13")})
n = Project.objects.count() + 1
p = Project.objects.create(code=f"PRJ-SIM{n:03d}", name=f"模拟项目{n}", sales_owner=leader, created_by=leader)
q = Quote.objects.create(code=f"QT-SIM-{n:04d}", project=p, recipient=acc, legal_entity=ent, owner=leader)
from django.utils import timezone

rev = q.revisions.create(version=1, quote_date=timezone.localdate(), price_basis=ent.price_basis, created_by=leader)
QuoteLine.objects.create(revision=rev, model=prod.model, name=prod.name, unit="台", qty=Decimal("2"), unit_price=Decimal("1000"))
services.generate_revision(rev.id, leader)
data = b"\x89PNG" + bytes(range(256)) * 40
Attachment.objects.create(owner_type="project", owner_id=p.id, filename="模拟附件.png", content_type="image/png", size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                          storage_key=store_private("attachments", data, ".png"), uploaded_by=leader)
print("seeded project", p.code)
