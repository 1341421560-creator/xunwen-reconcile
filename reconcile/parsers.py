from collections import Counter
from .normalize import text, name_key, cents, date_text, currency_key


def find_header(rows, aliases, required):
    for index, row in enumerate(rows[:80]):
        lookup = {name_key(value): column for column, value in enumerate(row) if text(value)}
        mapping = {}
        for field, names in aliases.items():
            for name in names:
                if name_key(name) in lookup:
                    mapping[field] = lookup[name_key(name)]
                    break
        if all(field in mapping for field in required):
            return index, mapping
    return None, {}


def value(row, mapping, field):
    index = mapping.get(field)
    return row[index] if index is not None and index < len(row) else None


def is_bank_layout_row(row, config):
    # 打印分页中的标题、账户信息和页脚不属于交易，必须先于金额解析识别。
    first = next((text(cell) for cell in row if text(cell)), "")
    return not first or any(first.startswith(prefix) for prefix in config.get("bank_layout_prefixes", []))


def parse_bank(sheets, filename, config):
    records, notes, controls = [], [], []
    for sheet in sheets:
        rows = sheet["rows"]
        header, mapping = find_header(rows, config["bank_columns"], ("date", "party", "debit", "credit"))
        if header is None:
            notes.append(f"流水工作表 {sheet['name']} 未识别为交易表")
            continue
        context = " ".join(text(cell) for row in rows[:header] for cell in row)
        default_currency = "人民币" if "人民币" in context else ""
        if not default_currency:
            notes.append(f"{sheet['name']} 未提供表级人民币标识；无币种的记录按 CNY 处理")
        for index, row in enumerate(rows[header + 1:], header + 2):
            if any("本月累计借方发生额" in text(cell) for cell in row):
                for column, cell in enumerate(row):
                    if "本月累计借方发生额" in text(cell) or "本月累计贷方发生额" in text(cell):
                        amount = next((text(v) for v in row[column + 1:] if text(v)), "")
                        controls.append({"type": "debit" if "借方" in text(cell) else "credit", "cents": cents(amount), "sheet": sheet["name"]})
            if is_bank_layout_row(row, config):
                continue
            raw_date = value(row, mapping, "date")
            debit = value(row, mapping, "debit")
            credit = value(row, mapping, "credit")
            if name_key(raw_date) in [name_key(v) for v in config["bank_columns"]["date"]]:
                continue
            if not text(debit) and not text(credit):
                if any(text(value(row, mapping, field)) for field in ("sequence", "date", "party", "reference")):
                    raise ValueError(f"流水 {sheet['name']} 第 {index} 行：交易记录缺少借贷金额，请核对源表")
                continue
            try:
                reference = value(row, mapping, "reference")
                if isinstance(reference, (int, float)) and len(text(reference)) > 15:
                    raise ValueError("银行流水号是超过 15 位的数值，可能丢失精度，请恢复原始文本编号")
                debit_cents, credit_cents = cents(debit, True), cents(credit, True)
                record_date = date_text(raw_date)
                if debit_cents < 0 or credit_cents < 0 or (debit_cents and credit_cents):
                    raise ValueError("借贷金额方向异常，请先核对源表")
                if not debit_cents and not credit_cents:
                    raise ValueError("借贷金额均为零，无法确定交易方向，请核对源表")
            except ValueError as exc:
                raise ValueError(f"流水 {sheet['name']} 第 {index} 行：{exc}") from None
            records.append({
                "id": f"B{len(records) + 1:04d}", "date": record_date,
                "source_sequence": text(value(row, mapping, "sequence")),
                "party": text(value(row, mapping, "party")),
                "summary": text(value(row, mapping, "summary")),
                "reference": text(value(row, mapping, "reference")),
                "bank": text(value(row, mapping, "bank")),
                "type": text(value(row, mapping, "type")),
                "currency": currency_key(value(row, mapping, "currency") or default_currency),
                "debit_cents": debit_cents, "credit_cents": credit_cents,
                "amount_cents": debit_cents or credit_cents,
                "direction": "支出" if debit_cents else "收入",
                "source": filename, "sheet": sheet["name"], "row": index
            })
    if not records:
        raise ValueError("没有识别到流水。需要交易日期、对方户名、借方发生额、贷方发生额等字段；可在 config/defaults.json 配置列名")
    references = Counter(r["reference"] for r in records if r["reference"])
    for record in records:
        record["duplicate"] = bool(record["reference"] and references[record["reference"]] > 1)
    if any(r["duplicate"] for r in records):
        notes.append("发现重复银行流水号，相关交易转入待确认，未自动去重")
    for control in controls:
        actual = sum(r[control["type"] + "_cents"] for r in records if r["sheet"] == control["sheet"])
        control.update(actual_cents=actual, passed=actual == control["cents"])
        if not control["passed"]:
            notes.append(f"{control['sheet']} 的 {control['type']} 合计与银行控制数不一致，请核对是否缺页或重复导入")
    return records, notes, controls


def parse_invoices(sheets, filename, config, mark_duplicates=True):
    records, notes = [], []
    for sheet in sheets:
        header, mapping = find_header(sheet["rows"], config["invoice_columns"], ("party", "amount", "number", "date"))
        if header is None:
            notes.append(f"发票工作表 {sheet['name']} 未识别为发票表")
            continue
        for index, row in enumerate(sheet["rows"][header + 1:], header + 2):
            if not any(text(cell) for cell in row):
                continue
            first = next((text(cell) for cell in row if text(cell)), "")
            if first in ("合计", "总计", "小计") or "合计：" in first:
                continue
            number = text(value(row, mapping, "number"))
            if number in config["invoice_columns"]["number"]:
                continue
            party = text(value(row, mapping, "party"))
            if not number and not party and not text(value(row, mapping, "amount")):
                if text(value(row, mapping, "sequence")).isdigit():
                    raise ValueError(f"发票 {sheet['name']} 第 {index} 行：存在序号但缺少发票字段，请核对源表")
                continue
            try:
                amount = cents(value(row, mapping, "amount"))
                invoice_date = date_text(value(row, mapping, "date"))
            except ValueError as exc:
                raise ValueError(f"发票 {sheet['name']} 第 {index} 行：{exc}") from None
            red = "红" in text(value(row, mapping, "color")) or amount < 0
            if red and amount > 0:
                notes.append(f"第 {index} 行红字发票金额为正数，按红字负金额识别")
                amount = -amount
            state = text(value(row, mapping, "status"))
            invalid = ""
            if not number or not party:
                invalid = "缺少发票号码或销售方名称"
            elif any(word in state for word in ("作废", "失控", "异常")):
                invalid = f"发票状态：{state}"
            elif isinstance(value(row, mapping, "number"), (int, float)) and len(number) > 15:
                invalid = "长发票号码为数值格式，可能已丢失精度，请将源数据恢复为文本"
            elif amount == 0:
                invalid = "零金额发票"
            records.append({
                "id": f"I{len(records) + 1:04d}", "date": invoice_date, "party": party,
                "source_sequence": text(value(row, mapping, "sequence")),
                "number": number, "code": text(value(row, mapping, "code")),
                "amount_cents": amount, "summary": text(value(row, mapping, "summary")),
                "currency": currency_key(value(row, mapping, "currency")),
                "red": red, "invalid": invalid, "source_status": state,
                "source": filename, "sheet": sheet["name"], "row": index
            })
    if not records:
        raise ValueError("没有识别到发票。需要发票号码、开票日期、销方名称和价税合计；不会用不含税金额替代价税合计")
    keys = Counter((r["code"], r["number"]) for r in records if r["number"])
    for record in records:
        if mark_duplicates and keys[(record["code"], record["number"])] > 1:
            record["invalid"] = "发票号码重复，请核对重复导入或明细行"
    return records, notes
