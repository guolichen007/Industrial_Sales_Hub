"""写入虚构演示数据（不含任何真实客户、金额或印章）。

用法：python manage.py seed_demo [--password 演示密码]
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.organizations.models import Account, Contact, LegalEntity
from apps.products.models import ParameterDefinition, Product, ProductCategory
from apps.projects.models import Project, ProjectParty, ProjectUpdate

TERMS = "价格含 13% 增值税专用发票。\n运输：送货至项目现场，卸货由需方负责。\n质保期：设备验收合格之日起 12 个月。\n本报价未尽事宜以双方签订的合同为准。"


class Command(BaseCommand):
    help = "写入演示数据（虚构）"

    def add_arguments(self, parser):
        parser.add_argument("--password", default="Demo@2026")

    @transaction.atomic
    def handle(self, *args, password, **kw):
        if User.objects.filter(username="leader").exists():
            self.stdout.write("演示数据已存在，跳过。")
            return
        users = {}
        for un, name, role in [("admin", "系统管理员", "ADMIN"), ("leader", "张经理", "LEADER"), ("assistant", "商务·刘敏", "ASSISTANT"),
                               ("sales", "李工", "SALES"), ("sales2", "王工", "SALES"), ("finance", "财务·陈静", "FINANCE")]:
            u = User(username=un, display_name=name, role=role, is_staff=(role == "ADMIN"))
            u.set_password(password)
            u.save()
            users[un] = u

        ents = [
            LegalEntity.objects.create(code="A", short_name="甲公司", name="演示甲机电设备有限公司", template_key="A", address="示例省示例市高新区创新路 1 号", phone="0000-1234567", email="sales-a@example.com",
                                       tax_no="91000000MA0000000A", bank_name="示例银行高新支行", bank_account="6200 0000 0000 0001", default_terms=TERMS, footer_note="演示数据 · 非正式报价"),
            LegalEntity.objects.create(code="B", short_name="乙公司", name="演示乙工业技术有限公司", template_key="B", address="示例省示例市经开区工业大道 88 号", phone="0000-7654321", email="sales-b@example.com",
                                       default_terms=TERMS, footer_note="演示数据 · 非正式报价"),
            LegalEntity.objects.create(code="C", short_name="丙公司", name="演示丙电气科技有限公司", template_key="C", address="示例省示例市港区物流园 6 号", phone="0000-6666888",
                                       default_terms=TERMS, footer_note="演示数据 · 非正式报价"),
        ]

        cats = {}
        for i, (cname, params) in enumerate([
            ("润滑系统", [("capacity", "储油量", "L"), ("control", "控制方式", ""), ("pressure", "工作压力", "MPa"), ("power", "油泵功率", "kW")]),
            ("制动器", [("torque", "制动力矩", "N·m"), ("release", "释放方式", ""), ("env", "适用环境", "")]),
            ("电缆卷筒", [("diameter", "卷筒直径", "mm"), ("length", "电缆长度", "m"), ("drive", "驱动形式", "")]),
            ("电缆", [("voltage", "额定电压", "kV"), ("section", "截面", "mm²"), ("sheath", "护套", "")]),
        ]):
            c = ProductCategory.objects.create(name=cname, sort=i)
            for j, (k, l, unit) in enumerate(params):
                ParameterDefinition.objects.create(category=c, key=k, label=l, unit=unit, sort=j)
            cats[cname] = c

        def P(k, l, v, u=""):
            return {"key": k, "label": l, "value": v, "unit": u}

        prods = [
            Product.objects.create(sku="LUB-320", model="LUB-320", name="集中润滑系统", category=cats["润滑系统"], brand="A品牌", unit="套", base_price=Decimal("39000"),
                                   params=[P("capacity", "储油量", "320", "L"), P("control", "控制方式", "PLC"), P("pressure", "工作压力", "25", "MPa"), P("power", "油泵功率", "2.2", "kW")], technical_desc="含主站、分配器及管路附件。"),
            Product.objects.create(sku="BRK-600", model="BRK-600", name="安全制动器", category=cats["制动器"], brand="B品牌", unit="台", base_price=Decimal("18200"),
                                   params=[P("torque", "制动力矩", "6000", "N·m"), P("release", "释放方式", "液压释放"), P("env", "适用环境", "-20~60℃")]),
            Product.objects.create(sku="CRL-80", model="CRL-80", name="电缆卷筒", category=cats["电缆卷筒"], brand="C品牌", unit="套", base_price=Decimal("28600"),
                                   params=[P("diameter", "卷筒直径", "800", "mm"), P("length", "电缆长度", "120", "m"), P("drive", "驱动形式", "磁滞")]),
            Product.objects.create(sku="CAB-3x35", model="CAB-3×35", name="工业拖令电缆", category=cats["电缆"], brand="C品牌", unit="米", base_price=Decimal("126"),
                                   params=[P("voltage", "额定电压", "0.6/1", "kV"), P("section", "截面", "3×35", "mm²"), P("sheath", "护套", "耐油耐寒")]),
        ]

        accs = {}
        for name, kinds, region, industry, contacts in [
            ("北方重工设备有限公司", ["主机厂"], "辽宁", "重型机械", [("陈工", "采购部", "采购经理", "138****0101"), ("刘工", "技术部", "工程师", "139****0212")]),
            ("华东港口产业集团", ["业主"], "江苏", "港口", [("赵经理", "设备部", "项目经理", "137****0880")]),
            ("远达能源工程有限公司", ["总包单位", "投资方"], "山西", "能源", [("王工", "项目部", "负责人", "135****0122")]),
            ("科联工业装备贸易", ["贸易商", "代理商"], "湖北", "贸易", [("周经理", "业务部", "经理", "136****0809")]),
            ("南方港机制造股份有限公司", ["主机厂"], "广东", "重型机械", [("黄工", "技术中心", "主任工程师", "133****0456")]),
        ]:
            a = Account.objects.create(name=name, kinds=kinds, region=region, industry=industry, created_by=users["assistant"])
            for cn, d, pos, m in contacts:
                Contact.objects.create(account=a, name=cn, department=d, position=pos, mobile=m)
            accs[name] = a

        now = timezone.now()
        specs = [
            ("PRJ-26001", "滨海煤码头堆取料机改造", "港口", "斗轮机", "华东", "投标中", "sales", [("北方重工设备有限公司", "主机厂"), ("南方港机制造股份有限公司", "主机厂"), ("华东港口产业集团", "业主")], "港口能源集团"),
            ("PRJ-26002", "东部电厂输煤系统更新", "电厂", "皮带机", "华北", "报价中", "sales2", [("远达能源工程有限公司", "总包单位"), ("远达能源工程有限公司", "投资方")], "能源集团"),
            ("PRJ-26003", "沿江皮带机节能升级", "钢厂", "皮带机", "华中", "技术交流", "sales", [("科联工业装备贸易", "代理商")], ""),
            ("PRJ-26004", "西北园区圆形料场润滑系统", "煤化工", "圆堆", "西北", "初步沟通", "sales2", [], ""),
        ]
        projects = []
        for code, name, ind, mach, reg, phase, owner, parties, inv in specs:
            p = Project.objects.create(code=code, name=name, industry=ind, machine_type=mach, region=reg, presale_phase=phase, sales_owner=users[owner], investor=inv,
                                       description=f"{name}（演示数据）", created_by=users[owner])
            for an, role in parties:
                ProjectParty.objects.create(project=p, account=accs[an], party_role=role, is_primary=(role == "主机厂"))
            projects.append(p)
        ProjectUpdate.objects.create(project=projects[0], stream="PRESALE", title="主机厂技术澄清", content="技术答疑已提交，等待明确接口尺寸。", phase="投标中", contact_name="陈工",
                                     next_action="确认接口尺寸后出第二版报价", next_action_date=timezone.localdate() + timedelta(days=3), happened_at=now - timedelta(days=1), created_by=users["sales"])
        ProjectUpdate.objects.create(project=projects[0], stream="PRESALE", title="现场踏勘", content="完成设备现场测量。", phase="技术交流", happened_at=now - timedelta(days=6), created_by=users["assistant"])
        ProjectUpdate.objects.create(project=projects[2], stream="PRESALE", title="需求收集完成", content="现场测量资料已整理。", phase="技术交流", happened_at=now - timedelta(days=2), created_by=users["sales"])
        self.stdout.write(self.style.SUCCESS(f"演示数据已写入。账号：admin/leader/assistant/sales/sales2/finance，密码：{password}"))
