import re
from .normalize import text, name_key, currency_key
from .parsers import find_header


def bank_identity(sheets, config):
    identities = {}
    for sheet in sheets:
        header, _ = find_header(sheet["rows"], config["bank_columns"], ("date", "party", "debit", "credit"))
        if header is None:
            if any(any(text(c) for c in row) for row in sheet["rows"]):
                raise ValueError(f"流水工作表 {sheet['name']} 无法识别，整次导入取消")
            continue
        accounts, names, currencies = set(), set(), set()
        for row in sheet["rows"]:
            for j, cell in enumerate(row):
                label = text(cell).replace("：", ":").strip()
                for prefix, target in (("账号:", accounts), ("户名:", names), ("币种:", currencies)):
                    if label.startswith(prefix):
                        raw = label[len(prefix):].strip() or next((c for c in row[j + 1:] if text(c)), "")
                        found = text(raw)
                        if not found or ":" in found.replace("：", ":"):
                            raise ValueError(f"{sheet['name']} 页头 {prefix} 缺少有效内容")
                        if prefix == "账号:" and (not found.isascii() or not found.isdigit() or isinstance(raw, (int, float)) and len(found) > 15):
                            raise ValueError(f"{sheet['name']} 银行账号格式或精度异常，请使用原始文本账号")
                        if found:
                            target.add(found)
        if len(accounts) != 1 or len(names) != 1:
            raise ValueError(f"{sheet['name']} 无法唯一确定交通银行页头账号及户名，整次导入取消")
        if not currencies or {currency_key(c) for c in currencies} != {"CNY"}:
            raise ValueError(f"{sheet['name']} 页头币种不是明确的人民币，整次导入取消")
        company = next(iter(names))
        if name_key(company) != name_key(config["company_name"]):
            raise ValueError(f"流水户名 {company} 与当前公司不符，禁止混入账本")
        identities[sheet["name"]] = {"account": next(iter(accounts)), "company_name": company}
    return identities


def verify_invoice_company(sheets, config):
    for sheet in sheets:
        header, _ = find_header(sheet["rows"], config["invoice_columns"], ("party", "amount", "number", "date"))
        if header is None:
            if any(any(text(c) for c in row) for row in sheet["rows"]):
                raise ValueError(f"发票工作表 {sheet['name']} 无法识别，整次导入取消")
            continue
        titles = [text(c) for row in sheet["rows"] for c in row if "进项发票清单" in text(c)]
        names = {name_key(re.sub(r"进项发票清单.*$", "", title).strip()) for title in titles}
        if names != {name_key(config["company_name"])}:
            raise ValueError(f"{sheet['name']} 的进项发票清单公司标题不符或缺失，整次导入取消")
