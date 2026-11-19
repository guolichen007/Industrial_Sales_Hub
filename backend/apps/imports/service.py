"""Excel 标准模板导入：先预检（不写库）→ 再提交（事务、防重复）。不执行任何宏/公式。"""
import hashlib
import io
from decimal import Decimal, InvalidOperation

from openpyxl import Workbook, load_workbook

TEMPLATES = {
    "products": ["型号编码*", "型号*", "名称*", "类别*", "品牌", "单位", "税率", "参考未税单价", "技术说明", "参数(名称=值;…)"],
    "accounts": ["客户名称*", "业务身份(用、分隔)", "行业", "地区", "地址", "联系人", "部门", "职务", "手机"],
    "performances": ["业绩名称*", "年份*", "地区", "行业", "供货产品", "主机类型", "主机厂", "业主", "投资方", "内部金额", "公开简介", "合同/订单号"],
}


def template_xlsx(kind) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = kind
    ws.append(TEMPLATES[kind])
    for i, _ in enumerate(TEMPLATES[kind], 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = 18
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def read_rows(kind, data: bytes):
    # data_only=True：只读缓存值，不计算公式；read_only 模式下不加载 VBA
    import zipfile

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError, TypeError) as e:
        raise ValueError(f"无法读取该 xlsx 文件（文件损坏或不是标准 Excel）：{type(e).__name__}")
    if not wb.worksheets:
        raise ValueError("工作簿中没有工作表")
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    header = [_cell(x) for x in next(it, [])]
    expected = TEMPLATES[kind]
    if [h.rstrip("*") for h in header[: len(expected)]] != [h.rstrip("*") for h in expected]:
        raise ValueError("表头与标准模板不一致，请下载最新模板。期望：" + " | ".join(expected))
    rows = []
    for idx, r in enumerate(it, start=2):
        vals = [_cell(x) for x in list(r)[: len(expected)]]
        vals += [""] * (len(expected) - len(vals))
        if not any(vals):
            continue
        rows.append({"row": idx, "values": vals})
        if len(rows) > 5000:
            raise ValueError("单次最多导入 5000 行")
    return rows


def _dec(s, field, errs, default=None, max_value=Decimal("999999999999.99")):
    if s == "":
        return default
    try:
        d = Decimal(s.replace(",", "").replace("，", ""))
    except InvalidOperation:
        errs.append(f"{field} 不是有效数字")
        return default
    if not d.is_finite() or d < 0 or d > max_value:
        errs.append(f"{field} 数值无效或超出范围")
        return default
    return d


def validate(kind, rows):
    """返回带行号的预检结果：status ∈ new / duplicate / error"""
    from apps.organizations.models import Account, normalize_name
    from apps.performances.models import Performance
    from apps.products.models import Product

    seen = set()
    out = []
    for r in rows:
        v, errs, key = r["values"], [], None
        if kind == "products":
            sku, model, name, cat = v[0], v[1], v[2], v[3]
            for f, x in [("型号编码", sku), ("型号", model), ("名称", name), ("类别", cat)]:
                if not x:
                    errs.append(f"{f} 必填")
            tax = _dec(v[6], "税率", errs, Decimal("0.13"), max_value=Decimal("100"))
            if tax is not None and tax >= 1:
                tax = tax / 100  # 允许填写 13 表示 13%
            if tax is not None and tax >= 1:
                errs.append("税率 超出范围")
            _dec(v[7], "参考未税单价", errs, Decimal("0"))
            for i, lim in ((0, 60), (1, 80), (2, 100), (3, 40), (4, 40), (5, 10)):
                if len(v[i]) > lim:
                    errs.append(f"第 {i + 1} 列超过 {lim} 字")
            key = sku.lower()
            dup = Product.objects.filter(sku__iexact=sku).exists() if sku else False
        elif kind == "accounts":
            if not v[0]:
                errs.append("客户名称 必填")
            if len(v[0]) > 150:
                errs.append("客户名称 超过 150 字")
            key = normalize_name(v[0])
            dup = Account.objects.filter(normalized_name=key).exists() if key else False
        else:
            if not v[0]:
                errs.append("业绩名称 必填")
            if not v[1].isdigit() or not (1990 <= int(v[1] or 0) <= 2100):
                errs.append("年份 应为四位数字")
            _dec(v[9], "内部金额", errs)
            if len(v[0]) > 150:
                errs.append("业绩名称 超过 150 字")
            key = "|".join([v[0], v[1], v[11]]).lower()
            dup = Performance.objects.filter(import_key=hashlib.sha1(key.encode()).hexdigest()).exists()
        if key and key in seen:
            errs.append("与本文件前面的行重复")
        seen.add(key)
        status = "error" if errs else ("duplicate" if dup else "new")
        out.append({**r, "status": status, "errors": errs})
    summary = {s: sum(1 for x in out if x["status"] == s) for s in ("new", "duplicate", "error")}
    summary["total"] = len(out)
    return out, summary


def commit(kind, rows, user):
    from apps.organizations.models import Account, Contact
    from apps.performances.models import Performance
    from apps.products.models import Product, ProductCategory

    created = skipped = 0
    for r in rows:
        if r["status"] != "new":
            skipped += 1
            continue
        v = r["values"]
        if kind == "products":
            if Product.objects.filter(sku__iexact=v[0]).exists():
                skipped += 1
                continue
            cat, _ = ProductCategory.objects.get_or_create(name=v[3])
            tax = Decimal(v[6].replace(",", "")) if v[6] else Decimal("0.13")
            if tax >= 1:
                tax = tax / 100
            params = []
            for i, kv in enumerate(x for x in v[9].replace("；", ";").split(";") if x.strip()):
                k, _, val = kv.partition("=")
                params.append({"key": f"p{i + 1}", "label": k.strip()[:40], "value": val.strip()[:120], "unit": ""})
            Product.objects.create(sku=v[0], model=v[1], name=v[2], category=cat, brand=v[4], unit=v[5] or "台", tax_rate=tax,
                                   base_price=Decimal(v[7].replace(",", "")) if v[7] else Decimal("0"), technical_desc=v[8], params=params, created_by=user)
        elif kind == "accounts":
            from apps.organizations.models import normalize_name

            if Account.objects.filter(normalized_name=normalize_name(v[0])).exists():
                skipped += 1
                continue
            a = Account.objects.create(name=v[0], kinds=[x for x in v[1].replace("/", "、").split("、") if x], industry=v[2], region=v[3], address=v[4], created_by=user)
            if v[5]:
                Contact.objects.create(account=a, name=v[5], department=v[6], position=v[7], mobile=v[8])
        else:
            ik = hashlib.sha1("|".join([v[0], v[1], v[11]]).lower().encode()).hexdigest()
            if Performance.objects.filter(import_key=ik).exists():
                skipped += 1
                continue
            Performance.objects.create(source="IMPORT", status="PENDING", title=v[0], year=int(v[1]), region=v[2], industry=v[3], product_summary=v[4], machine_type=v[5],
                                       host_maker=v[6], owner_org=v[7], investor=v[8], internal_amount=Decimal(v[9].replace(",", "")) if v[9] else None,
                                       public_summary=v[10], remark=f"导入；合同/订单号：{v[11]}" if v[11] else "导入", import_key=ik, created_by=user)
        created += 1
    return {"created": created, "skipped": skipped}
