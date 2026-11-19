"""真实报价样张只读分析工具（实施端在授权本地路径运行，结果不入 Git）。

用法（在 backend 目录）：
    python manage.py analyze_quote_sample <样张.xlsx|.xls> --out <私有输出目录>

- .xlsx：openpyxl 只读打开两次：一次读公式、一次读缓存值（data_only），不执行宏、不访问外部链接；
- .xls：xlrd（只读解析 BIFF，不执行宏，不计算公式，只能读到缓存值）；
- 输出 <文件名>.mapping.md（字段映射草稿）与 <文件名>.cells.json（非空单元格、合并区域、公式、缓存值、打印区域、页面设置）。

报告中“建议映射”只是启发式提示（例如含“税”“合计”“大写”“铜价”“汇率”的单元格），必须由实施人员逐项核对，
不得据此猜测公司全称、银行账号或含税口径。
"""
import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

KEYWORDS = {
    "主体/抬头": ["有限公司", "公司"],
    "联系方式": ["电话", "传真", "邮箱", "地址", "手机", "TEL", "FAX"],
    "接收客户": ["致", "TO", "客户", "收件"],
    "联系人": ["联系人", "ATTN", "经办"],
    "日期/编号": ["日期", "编号", "NO"],
    "产品字段": ["名称", "型号", "规格", "参数", "单位", "数量", "品牌"],
    "价格口径": ["单价", "含税", "不含税", "未税", "税率", "税额", "增值税", "13%"],
    "合计/大写": ["合计", "总计", "小计", "大写", "人民币"],
    "铜价/汇率": ["铜价", "铜", "汇率", "美元", "USD"],
    "条款/页脚": ["交货", "付款", "质保", "有效期", "备注", "说明", "运输"],
}


def classify(text):
    t = str(text).upper()
    return [k for k, words in KEYWORDS.items() if any(w.upper() in t for w in words)]


def analyze_xlsx(path: Path):
    from openpyxl import load_workbook

    wb_f = load_workbook(path, read_only=False, data_only=False, keep_vba=False, keep_links=False)
    wb_v = load_workbook(path, read_only=False, data_only=True, keep_vba=False, keep_links=False)
    sheets = []
    for ws in wb_f.worksheets:
        wv = wb_v[ws.title]
        cells = []
        for row in ws.iter_rows():
            for c in row:
                if c.value is None:
                    continue
                v = wv[c.coordinate].value
                is_formula = isinstance(c.value, str) and c.value.startswith("=")
                cells.append({
                    "cell": c.coordinate, "value": None if is_formula else _j(c.value), "formula": c.value if is_formula else None,
                    "cached": _j(v), "number_format": c.number_format, "hints": classify(v if v is not None else c.value),
                })
        ps = ws.page_setup
        sheets.append({
            "name": ws.title, "dimensions": ws.dimensions, "merged": [str(r) for r in ws.merged_cells.ranges],
            "print_area": ws.print_area, "print_title_rows": ws.print_title_rows,
            "page_setup": {"orientation": ps.orientation, "paperSize": ps.paperSize, "fitToWidth": ps.fitToWidth, "fitToHeight": ps.fitToHeight, "scale": ps.scale},
            "col_widths": {k: v.width for k, v in ws.column_dimensions.items() if v.width},
            "images": len(getattr(ws, "_images", [])), "cells": cells,
        })
    return {"format": "xlsx", "sheets": sheets, "external_links": len(getattr(wb_f, "_external_links", []))}


def analyze_xls(path: Path):
    try:
        import xlrd
    except ImportError as e:
        raise CommandError("解析 .xls 需要 xlrd（pip install xlrd==2.0.2）；或用 Excel 另存为 .xlsx 后再分析") from e
    book = xlrd.open_workbook(str(path), formatting_info=True, on_demand=True)
    sheets = []
    for sh in book.sheets():
        cells = []
        for r in range(sh.nrows):
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    continue
                coord = f"{xlrd.formula.colname(c)}{r + 1}"
                v = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    v = str(xlrd.xldate_as_datetime(v, book.datemode))
                cells.append({"cell": coord, "cached": _j(v), "formula": None, "hints": classify(v)})
        merged = [f"{xlrd.formula.colname(c1)}{r1 + 1}:{xlrd.formula.colname(c2 - 1)}{r2}" for r1, r2, c1, c2 in sh.merged_cells]
        sheets.append({"name": sh.name, "dimensions": f"{sh.nrows}x{sh.ncols}", "merged": merged, "cells": cells,
                       "note": "xls 格式：xlrd 只能读取缓存结果，无法读取公式文本；公式需在 Excel 中人工核对或另存为 xlsx 后重新分析"})
    return {"format": "xls", "sheets": sheets}


def _j(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def to_markdown(name, data):
    lines = [f"# 样张字段映射草稿：{name}", "", "> 私有资料，禁止提交到公共仓库。以下“提示”仅为关键词启发式，必须逐项人工核对。", ""]
    for sh in data["sheets"]:
        lines += [f"## 工作表：{sh['name']}（{sh['dimensions']}）", ""]
        if sh.get("print_area"):
            lines.append(f"- 打印区域：{sh['print_area']}；页面：{sh.get('page_setup')}")
        if sh.get("merged"):
            lines.append(f"- 合并区域（{len(sh['merged'])}）：{', '.join(sh['merged'][:40])}{' …' if len(sh['merged']) > 40 else ''}")
        if sh.get("note"):
            lines.append(f"- 注意：{sh['note']}")
        formulas = [c for c in sh["cells"] if c.get("formula")]
        lines += ["", f"### 公式（{len(formulas)}）", "", "| 单元格 | 公式 | 缓存结果 |", "|---|---|---|"]
        lines += [f"| {c['cell']} | `{c['formula']}` | {c['cached']} |" for c in formulas[:200]]
        lines += ["", "### 带提示的单元格", "", "| 单元格 | 内容 | 提示 | 映射到系统字段（人工填写） | 核对人/日期 |", "|---|---|---|---|---|"]
        for c in sh["cells"]:
            if c["hints"]:
                txt = re.sub(r"\s+", " ", str(c.get("cached") if c.get("cached") is not None else c.get("value")))[:60]
                lines.append(f"| {c['cell']} | {txt} | {'、'.join(c['hints'])} |  |  |")
        lines.append("")
    lines += ["## 必须人工确认", "", "- [ ] 合法主体全称、Logo、抬头、地址电话", "- [ ] 单价含税/未税；税额舍入顺序（逐行/合计）；是否显示税率列",
              "- [ ] 数量×倍数（米/盘、台/套）规则", "- [ ] 铜价/汇率条款原文与有效期", "- [ ] 合计、人民币大写位置与格式", "- [ ] 页脚、分页、每页重复表头",
              "- [ ] 财务提供 ≥10 组黄金样例并签字"]
    return "\n".join(lines) + "\n"


class Command(BaseCommand):
    help = "只读分析真实报价样张，生成私有字段映射草稿"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--out", required=True)

    def handle(self, *args, path, out, **kw):
        p = Path(path)
        if not p.exists():
            raise CommandError(f"文件不存在：{p}")
        suffix = p.suffix.lower()
        if suffix in (".xlsm", ".xltm"):
            raise CommandError("不分析带宏文件；请在受控环境另存为 .xlsx（不启用宏）后再分析")
        data = analyze_xlsx(p) if suffix == ".xlsx" else analyze_xls(p) if suffix == ".xls" else None
        if data is None:
            raise CommandError("仅支持 .xlsx / .xls")
        o = Path(out)
        o.mkdir(parents=True, exist_ok=True)
        (o / f"{p.stem}.cells.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        (o / f"{p.stem}.mapping.md").write_text(to_markdown(p.name, data), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"已生成：{o / (p.stem + '.mapping.md')}（私有，勿提交 Git）"))
