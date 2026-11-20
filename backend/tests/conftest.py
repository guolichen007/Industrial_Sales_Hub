import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.organizations.models import Account, Contact, LegalEntity
from apps.products.models import Product, ProductCategory
from apps.projects.models import Project


@pytest.fixture(autouse=True)
def private_dir(settings):
    d = Path(tempfile.mkdtemp(prefix="ish-test-"))
    settings.PRIVATE_STORAGE_DIR = d
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def users(db):
    out = {}
    for name, role in [("admin", "ADMIN"), ("leader", "LEADER"), ("assistant", "ASSISTANT"), ("sales", "SALES"), ("sales2", "SALES"), ("finance", "FINANCE")]:
        u = User(username=name, role=role, display_name=name)
        u.set_password("Test@12345")
        u.save()
        out[name] = u
    return out


@pytest.fixture
def client_for(users):
    def make(name):
        c = APIClient()
        c.force_authenticate(users[name])
        return c

    return make


def sign_template(entity, leader, note="测试：已按虚构样张核对"):
    from apps.organizations.models import TemplateSignoff
    from apps.quotations.pdf import TEMPLATE_VERSION

    return TemplateSignoff.objects.create(entity=entity, template_key=entity.template_key, template_version=TEMPLATE_VERSION, config_sha256=entity.signoff_config_sha256(),
                                          config={k: getattr(entity, k) for k in entity.SIGNOFF_FIELDS}, note=note, signed_by=leader)


@pytest.fixture
def base(db, users):
    ents = [LegalEntity.objects.create(code=c, short_name=f"{c}公司", name=f"测试{c}有限公司", template_key=c, address="测试地址", phone="000-1", default_terms="条款一\n条款二") for c in "ABC"]
    for e in ents:  # 既有用例代表“模板已签收”的正式状态；未签收（试用）路径见 tests/test_rc2_templates.py
        sign_template(e, users["leader"])
    cat = ProductCategory.objects.create(name="润滑系统")
    prods = [
        Product.objects.create(sku="LUB-320", model="LUB-320", name="集中润滑系统", category=cat, unit="套", base_price=Decimal("39000"), tax_rate=Decimal("0.13"),
                               params=[{"key": "cap", "label": "储油量", "value": "320", "unit": "L"}, {"key": "pr", "label": "工作压力", "value": "25", "unit": "MPa"}]),
        Product.objects.create(sku="BRK-600", model="BRK-600", name="安全制动器", category=cat, unit="台", base_price=Decimal("18200"), tax_rate=Decimal("0.13"), params=[]),
    ]
    accs = [Account.objects.create(name=n, kinds=k) for n, k in [("北方重工设备有限公司", ["主机厂"]), ("南方港机制造有限公司", ["主机厂"]), ("能源投资集团", ["投资方"])]]
    Contact.objects.create(account=accs[0], name="陈工", position="采购经理", mobile="138")
    p_sales = Project.objects.create(code="PRJ-T001", name="销售1的项目", sales_owner=users["sales"], created_by=users["sales"])
    p_other = Project.objects.create(code="PRJ-T002", name="销售2的项目", sales_owner=users["sales2"], created_by=users["sales2"])
    return {"ents": ents, "prods": prods, "accs": accs, "p_sales": p_sales, "p_other": p_other}


@pytest.fixture
def make_quote(client_for, base):
    def make(project=None, recipient=None, entity=None, lines=((0, "2", "0"),), render=True, who="assistant"):
        c = client_for(who)
        project = project or base["p_sales"]
        recipient = recipient or base["accs"][0]
        entity = entity or base["ents"][0]
        r = c.post("/api/quotations/", {"project": project.id, "recipient": recipient.id, "legal_entity": entity.id}, format="json")
        assert r.status_code == 201, r.content
        q = r.json()
        rid = q["latest"]["id"]
        for pidx, qty, disc in lines:
            r = c.post(f"/api/quote-revisions/{rid}/lines/", {"product": base["prods"][pidx].id, "qty": qty, "discount_rate": disc}, format="json")
            assert r.status_code == 201, r.content
        if render:
            r = c.post(f"/api/quote-revisions/{rid}/render/")
            assert r.status_code == 200, r.content
        return q["id"], rid

    return make
