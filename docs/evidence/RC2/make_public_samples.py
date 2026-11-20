"""生成 r3 三模板的虚构数据样张（公开证据）：正式（已签收）+ 试用（未签收）+ 长清单。
用法：ISH_MODE=DEMO DATA_DIR=<临时> python -I make_public_samples.py <输出目录>（在 backend 目录运行）"""
import datetime
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()
from django.core.management import call_command  # noqa: E402

call_command("migrate", verbosity=0)
from apps.accounts.models import User  # noqa: E402
from apps.common.pdfkit import read_private  # noqa: E402
from apps.organizations.models import Account, Contact, LegalEntity, TemplateSignoff  # noqa: E402
from apps.projects.models import Project  # noqa: E402
from apps.quotations import services  # noqa: E402
from apps.quotations.models import Quote, QuoteLine  # noqa: E402
from apps.quotations.pdf import TEMPLATE_VERSION  # noqa: E402

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
leader, _ = User.objects.get_or_create(username="demo_leader", defaults={"role": "LEADER", "display_name": "演示负责人"})
sender, _ = User.objects.get_or_create(username="demo_asst", defaults={"role": "ASSISTANT", "display_name": "演示商务", "phone": "000-0000-0000", "email": "sales@example.com"})
ENTS = {
    "A": dict(short_name="演示甲", name="演示甲机电设备（示例）有限公司", template_key="A", price_basis="NET", name_en="Demo Alpha Electromechanical (Example) Co., Ltd.",
              address_en="No. 0 Example Road, Example City", currency_label="人民币 CNY",
              default_terms="1. General\nThis offer is subject to the general terms and conditions (demo).\n2. Metal Price\nFormula to apply: P1=P0+(C1-C0) x copper weight+(A1-A0) x aluminium weight (reference only)."),
    "B": dict(short_name="演示乙", name="演示乙传动技术（示例）有限公司", template_key="B", price_basis="GROSS", show_tax_columns=False, name_en="Demo Beta Transmission (Example) Co., Ltd",
              address="示例市示例区示例路 0 号", website="www.example.com", email="info@example.com", intro_text="感谢您对我公司产品的关注，有关报价如下：", currency_label="人民币（元）",
              default_terms="报价产品为演示品牌。\n以上报价为含增值税专用发票（税率待财务确认），用户指定地点交货价。"),
    "C": dict(short_name="演示丙", name="演示丙工业设备（示例）有限公司", template_key="C", price_basis="GROSS", show_tax_columns=False, name_en="Demo Gamma Industrial (Example) Co.,Ltd.",
              address_en="Room 000, Example Building\nExample District, 000000", address="示例市示例区示例路\n0 号 0 幢", website="www.example.com", email="info@example.com",
              phone="000-0000000", fax="000-0000000-1", quote_title="报 价 单", default_terms="以上报价含增值税 13% 专用发票（演示）。"),
}
acc = Account.objects.create(name="示例重工设备有限公司")
ct = Contact.objects.create(account=acc, name="示例联系人", position="经理", mobile="000-0000-0000")
prj = Project.objects.create(code="PRJ-DEMO", name="示例港口装卸系统改造项目", created_by=leader)


def quote(code, lines, signed=True, **rev_kw):
    e, _ = LegalEntity.objects.update_or_create(code=code, defaults=ENTS[code])
    TemplateSignoff.objects.filter(entity=e).delete()
    if signed:
        TemplateSignoff.objects.create(entity=e, template_key=e.template_key, template_version=TEMPLATE_VERSION, config_sha256=e.signoff_config_sha256(), config={}, note="演示签收", signed_by=leader)
    n = Quote.objects.count() + 1
    q = Quote.objects.create(code=f"QT-DEMO-{n:04d}", project=prj, recipient=acc, recipient_contact=ct, legal_entity=e, owner=leader)
    rev = q.revisions.create(version=1, quote_date=datetime.date(2026, 11, 20), price_basis=e.price_basis, terms=e.default_terms, created_by=sender, **rev_kw)
    for i, ln in enumerate(lines):
        QuoteLine.objects.create(revision=rev, sort=i, tax_rate=Decimal("0.13"), **ln)
    rev = services.generate_revision(rev.id, sender, trial=not signed)
    return read_private(rev.pdf_storage_key)


A = [dict(model=m, name="演示拖令电缆", brand="演示牌", description=d, unit="米", qty=Decimal(q), unit_price=Decimal(p)) for m, d, q, p in
     [("3x50mm²", "动力电缆 C级阻燃", "100", "100.00"), ("16x1.5mm²C", "控制屏蔽电缆", "200", "50.50")]]
B = [dict(model=m, name="演示制动器", unit="台", qty=Decimal(q), unit_price=Decimal(p), params=[{"key": "t", "label": "制动力矩", "value": t, "unit": "Nm"}], visible_param_keys=["t"]) for m, q, p, t in
     [("DB-100", "2", "10000", "1000"), ("DB-200", "1", "20000", "2000")]]
C = [dict(model=part, name=n, unit="套", qty=Decimal(1), unit_price=Decimal(p), params=[{"key": "p", "label": "润滑点数", "value": pts, "unit": "个润滑点"}], visible_param_keys=["p"]) for n, part, p, pts in
     [("演示电动润滑系统", "行走机构", "10000", "20"), ("演示电动润滑系统", "回转机构", "8000", "12"), ("演示手动润滑系统", "斗轮机构", "2000", "2")]]
LONG = [dict(model=f"M-{i:03d}-长型号ABCDEFGHIJ", name=f"长清单项目{i}", unit="件", qty=Decimal(i), unit_price=Decimal("12.5"), description="说明：µm、℃、N·m、mm²、3×35", params=[{"key": "a", "label": "参数", "value": str(i), "unit": "mm²"}], visible_param_keys=["a"]) for i in range(1, 46)]
for code, lines, kw in [("A", A, dict(ref_copper_price="(演示) 80000", delivery_terms="DDP", payment_terms="预付30%，发货前付清", warranty="12个月")), ("B", B, dict(valid_days=90)), ("C", C, dict(host_qty=2, host_label="台斗轮"))]:
    open(f"{OUT}/r3-{code}-正式-虚构.pdf", "wb").write(quote(code, lines, True, **kw))
    open(f"{OUT}/r3-{code}-试用-虚构.pdf", "wb").write(quote(code, lines, False, **kw))
    open(f"{OUT}/r3-{code}-长清单-虚构.pdf", "wb").write(quote(code, LONG, True, host_qty=3 if code == "C" else 1))
print("ok")
