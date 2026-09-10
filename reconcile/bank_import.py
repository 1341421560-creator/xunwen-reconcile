from .bank_formats import bank_format
from .company_identity import bank_identity
from .mybank_headers import mybank_controls
from .parsers import find_header, parse_bank, value
from .normalize import name_key


def duplicate_columns(row, config):
    names = [name_key(cell) for cell in row]
    return any(sum(names.count(name_key(alias)) for alias in aliases) > 1 for aliases in config["bank_columns"].values())


def check_mybank_headers(sheet, config):
    header, mapping = find_header(sheet["rows"], config["bank_columns"], ("date", "party", "debit", "credit", "reference"))
    if duplicate_columns(sheet["rows"][header], config):
        raise ValueError(f"{sheet['name']} 网商银行交易表头存在重复列，无法确定导入依据")
    date_names = {name_key(value) for value in config["bank_columns"]["date"]}
    reference_names = {name_key(value) for value in config["bank_columns"]["reference"]}
    for index, row in enumerate(sheet["rows"][header + 1:], header + 2):
        if name_key(value(row, mapping, "date")) in date_names or name_key(value(row, mapping, "reference")) in reference_names:
            _, repeated = find_header([row], config["bank_columns"], ("date", "party", "debit", "credit", "reference"))
            if repeated != mapping or duplicate_columns(row, config):
                raise ValueError(f"{sheet['name']} 第 {index} 行分页表头不一致，请使用列顺序一致的网商银行明细")


def read_bank(sheets, filename, config):
    identities = bank_identity(sheets, config)
    rows, notes, controls = [], [], []
    for sheet in sheets:
        if sheet["name"] not in identities:
            continue
        format_id, active_config = bank_format(sheet, config)
        if format_id == "mybank":
            check_mybank_headers(sheet, active_config)
        parsed, warnings, checks = parse_bank([sheet], filename, active_config)
        if format_id == "mybank":
            checks = mybank_controls(sheet, parsed, active_config)
            warnings = [warning for warning in warnings if "未提供表级人民币标识" not in warning]
            warnings.append(f"{sheet['name']} 识别为网商银行：借方金额(收)计入收入，贷方金额(支)计入支出；金额和笔数已通过页头校验")
        for row in parsed:
            row.update(identities[sheet["name"]], duplicate=False)
            row["id"] = f"B{len(rows) + 1:04d}"
            rows.append(row)
        notes.extend(warnings)
        controls.extend(checks)
    if not rows:
        raise ValueError("没有识别到银行流水，请选择交通银行或网商银行的原始交易明细")
    if any(not control["passed"] for control in controls):
        raise ValueError("银行借贷合计与页脚控制数不符，请核对完整文件；整次导入取消")
    return rows, notes, controls
