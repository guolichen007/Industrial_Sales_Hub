"""金额计算：只信任后端 Decimal。

口径（待甲方确认事项 D4 的默认值，已在决策记录中标注 BLOCKED）：
  - 单价为未税单价；
  - 行未税额 = round2(数量 × 单价 × (1 − 折扣率))；
  - 行税额   = round2(行未税额 × 税率)；
  - 行含税额 = 行未税额 + 行税额；
  - 单据合计 = 各行金额求和（逐行舍入后求和）；
  - 舍入方式 ROUND_HALF_UP，人民币两位小数。
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


def D(v) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, float):
        # 禁止二进制浮点直接进入金额计算
        v = repr(v)
    try:
        return Decimal(str(v).replace(",", "").strip() or "0")
    except InvalidOperation as e:
        raise ValueError(f"非法数值: {v!r}") from e


def round2(v) -> Decimal:
    return D(v).quantize(CENT, rounding=ROUND_HALF_UP)


def line_amounts(qty, unit_price, tax_rate, discount_rate=0, price_basis="NET"):
    """price_basis=NET：单价未税，逐行计税；GROSS：单价含税，金额倒算未税与税额（税额 = 含税 − 未税）。"""
    qty, unit_price, tax_rate, discount_rate = D(qty), D(unit_price), D(tax_rate), D(discount_rate)
    for v in (qty, unit_price, tax_rate, discount_rate):
        if not v.is_finite():
            raise ValueError("数值无效")
    if qty < 0 or unit_price < 0:
        raise ValueError("数量和单价不能为负数")
    if not (Decimal(0) <= tax_rate < Decimal(1)):
        raise ValueError("税率应在 0～1 之间，例如 0.13")
    if not (Decimal(0) <= discount_rate < Decimal(1)):
        raise ValueError("折扣率应在 0～1 之间，例如 0.05 表示优惠 5%")
    if price_basis == "GROSS":
        gross = round2(qty * unit_price * (Decimal(1) - discount_rate))
        net = round2(gross / (Decimal(1) + tax_rate))
        return net, gross - net, gross
    net = round2(qty * unit_price * (Decimal(1) - discount_rate))
    tax = round2(net * tax_rate)
    return net, tax, net + tax


def totals(lines):
    net = sum((D(l["line_net"]) for l in lines), Decimal("0.00"))
    tax = sum((D(l["line_tax"]) for l in lines), Decimal("0.00"))
    return round2(net), round2(tax), round2(net + tax)


_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_UNITS = ["", "拾", "佰", "仟"]
_SECTIONS = ["", "万", "亿", "万亿"]


def _section(n):
    s, zero = "", False
    for i in range(3, -1, -1):
        d = n // (10 ** i) % 10
        if d == 0:
            if s:
                zero = True
        else:
            if zero:
                s += "零"
                zero = False
            s += _DIGITS[d] + _UNITS[i]
    return s


def rmb_upper(amount) -> str:
    """人民币金额大写，例如 88140.00 → 捌万捌仟壹佰肆拾元整"""
    a = round2(amount)
    if a < 0:
        return "负" + rmb_upper(-a)
    yuan = int(a)
    cents = int((a - yuan) * 100)
    jiao, fen = cents // 10, cents % 10
    if yuan == 0:
        s = ""
    else:
        parts, idx, need_zero = [], 0, False
        n = yuan
        while n > 0:
            sec = n % 10000
            if sec:
                txt = _section(sec) + _SECTIONS[idx]
                if need_zero:
                    txt += "零"
                parts.insert(0, txt)
                need_zero = sec < 1000
            else:
                need_zero = bool(parts)
            n //= 10000
            idx += 1
        s = "".join(parts)
        s = s.replace("零零", "零").rstrip("零")
        s += "元"
    if jiao == 0 and fen == 0:
        return (s or "零元") + "整"
    if jiao:
        s += _DIGITS[jiao] + "角"
    elif s:
        s += "零"
    if fen:
        s += _DIGITS[fen] + "分"
    else:
        s += "整"
    return s
