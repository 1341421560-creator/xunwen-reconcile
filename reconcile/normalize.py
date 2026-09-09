import re
import unicodedata
from datetime import datetime, date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def name_key(value):
    # 仅消除空白与全半角差异，保留公司名称的法律主体部分。
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text(value))).casefold()


def cents(value, blank_zero=False):
    raw = unicodedata.normalize("NFKC", text(value))
    if not raw:
        if blank_zero:
            return 0
        raise ValueError("金额为空")
    raw = raw.replace(",", "").replace("￥", "").replace("¥", "").replace("元", "").strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = "-" + raw[1:-1]
    try:
        number = Decimal(raw)
        if not number.is_finite() or abs(number) > Decimal("9999999999999"):
            raise ValueError("金额超出支持范围")
        scaled = number * 100
        rounded = scaled.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        # 容忍浮点计算的微小尾差，但不能把真实厘金额静默改成另一笔分金额。
        if abs(scaled - rounded) > Decimal("0.000001"):
            raise ValueError("金额包含不足一分的小数，请核对源文件金额，不能自动四舍五入")
        return int(rounded)
    except InvalidOperation:
        raise ValueError(f"无法识别金额：{raw}") from None


def date_text(value):
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    raw = text(value)
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"无法识别日期：{raw or '空值'}")


def currency_key(value):
    raw = name_key(value)
    return "CNY" if raw in ("", "人民币", "cny", "rmb", "元", "156") else raw.upper()
