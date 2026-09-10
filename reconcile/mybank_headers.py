import re
import unicodedata
from .normalize import text, name_key, currency_key, cents


def header_fields(sheet, profile):
    labels = set(profile["identity_labels"].values())
    labels.update(value[field] for value in profile["controls"] for field in ("amount_label", "count_label"))
    values = {label: [] for label in labels}
    for row in sheet["rows"]:
        first = next((text(cell) for cell in row if text(cell)), "")
        first_label = first.replace("：", ":").split(":", 1)[0].strip()
        if first_label not in labels:
            continue
        for column, cell in enumerate(row):
            parts = text(cell).replace("：", ":").split(":", 1)
            label = parts[0].strip()
            if label not in labels:
                continue
            raw = parts[1].strip() if len(parts) == 2 and parts[1].strip() else next((c for c in row[column + 1:] if text(c)), "")
            if not text(raw) or text(raw).replace("：", ":").split(":", 1)[0].strip() in labels:
                raise ValueError(f"{sheet['name']} 网商银行页头 {label} 缺少有效内容")
            values[label].append(raw)
    return values


def unique_field(values, label, sheet_name, convert=text):
    try:
        candidates = {convert(raw) for raw in values[label]}
    except ValueError as exc:
        raise ValueError(f"{sheet_name} 网商银行页头 {label}：{exc}") from None
    if len(candidates) != 1:
        raise ValueError(f"{sheet_name} 网商银行页头 {label} 缺失或不一致，整次导入取消")
    return next(iter(candidates))


def account_currency(raw):
    if not isinstance(raw, str):
        raise ValueError("企业账号须为带币种的文本，数值账号可能丢失精度")
    value = unicodedata.normalize("NFKC", raw).strip()
    found = re.fullmatch(r"([0-9]+)\s*\(\s*([^()]+?)\s*\)", value)
    if not found:
        raise ValueError("企业账号需明确标注币种，例如 8888000000000001(人民币)")
    account, currency = found.groups()
    if currency_key(currency) != "CNY":
        raise ValueError("企业账号币种必须为人民币")
    return account


def mybank_identity(sheet, config):
    profile = config["bank_formats"]["mybank"]
    fields = header_fields(sheet, profile)
    company = unique_field(fields, profile["identity_labels"]["company"], sheet["name"])
    account = unique_field(fields, profile["identity_labels"]["account"], sheet["name"], account_currency)
    if name_key(company) != name_key(config["company_name"]):
        raise ValueError(f"流水企业名称 {company} 与当前公司不符，禁止混入账本")
    return {"account": account, "company_name": company, "source_bank": profile["label"], "bank_format": "mybank"}


def transaction_count(raw):
    value = unicodedata.normalize("NFKC", text(raw))
    found = re.fullmatch(r"([0-9]+)\s*笔?", value)
    if not found:
        raise ValueError("交易笔数必须为非负整数")
    return int(found.group(1))


def mybank_controls(sheet, records, config):
    profile = config["bank_formats"]["mybank"]
    fields = header_fields(sheet, profile)
    controls = []
    for rule in profile["controls"]:
        amount = unique_field(fields, rule["amount_label"], sheet["name"], cents)
        count = unique_field(fields, rule["count_label"], sheet["name"], transaction_count)
        actual = sum(row[rule["type"] + "_cents"] for row in records)
        actual_count = sum(row[rule["type"] + "_cents"] > 0 for row in records)
        if amount < 0 or amount != actual or count != actual_count:
            raise ValueError(f"{sheet['name']} 网商银行{rule['label']}金额或笔数与页头控制数不符：页头 {amount / 100:.2f} 元、{count} 笔，实际 {actual / 100:.2f} 元、{actual_count} 笔；整次导入取消")
        controls.append({"type": rule["type"], "cents": amount, "actual_cents": actual, "sheet": sheet["name"], "passed": True,
                         "label": rule["label"], "expected_count": count, "actual_count": actual_count})
    return controls
