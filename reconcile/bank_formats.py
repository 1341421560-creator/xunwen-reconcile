from .normalize import name_key, text
from .parsers import find_header


def bank_format(sheet, config):
    profile = config["bank_formats"]["mybank"]
    cells = [name_key(cell) for row in sheet["rows"][:80] for cell in row if text(cell)]
    markers = {name_key(value) for value in profile["title_markers"]}
    direction_columns = {name_key(value) for field in ("debit", "credit") for value in profile["columns"][field]}
    if any(cell in markers or cell in direction_columns for cell in cells):
        header, _ = find_header(sheet["rows"], profile["columns"], ("date", "party", "debit", "credit", "reference"))
        if header is None or not any(name_key(cell) in markers for row in sheet["rows"][:header] for cell in row):
            # 金额带收支标记时必须使用网商模板，禁止降级为交通银行的借贷方向。
            raise ValueError(f"{sheet['name']} 网商银行标题或交易表头不完整，需包含借方金额(收)、贷方金额(支)、交易时间及账务流水号")
        return "mybank", dict(config, bank_columns=profile["columns"], bank_layout_prefixes=profile["layout_prefixes"])
    return "bocom", config
