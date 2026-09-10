from .bank_import import read_bank
from .bocom_pdf_controls import validate_statement
from .normalize import cents, name_key
from .pdf_reader import read_pdf_pages
from .pdf_table import extract_table, labeled_values


def page_statement(page, limits):
    profile, tolerance = limits["bocom"], limits["geometry_tolerance"]
    table = extract_table(page, profile["required_columns"], tolerance)
    header = [p for p in page["parts"] if p["y"] < table["top"]]
    if not any("交通银行" in p["text"] and "明细对账单" in p["text"] for p in header):
        raise ValueError("PDF 缺少交通银行明细对账单标题，暂不支持该银行或版式")
    identity = labeled_values(header, profile["identity_labels"], tolerance)
    if not all(label in identity for label in profile["identity_labels"]):
        raise ValueError("PDF 公司、账户、币种、账期或页码信息不完整")
    if name_key(identity["币种"]) not in ("人民币", "cny", "rmb"):
        raise ValueError("PDF 目前仅支持明确标注人民币的银行对账单")
    if not identity["账号"].isascii() or not identity["账号"].isdigit():
        raise ValueError("PDF 银行账号无法可靠识别")
    controls = labeled_values([p for p in page["parts"] if p["y"] > table["bottom"]], profile["control_labels"], tolerance)
    headers = table["headers"]
    sequence_column, balance_column = headers.index("序号"), headers.index("余额")
    rows, entries = [], []
    for table_row, row in enumerate(table["rows"], 2):
        if not any(row):
            continue
        sequence = row[sequence_column]
        balance = cents(row[balance_column])
        carry = sequence in profile["carry_labels"]
        if not carry and not sequence.isdigit():
            raise ValueError(f"PDF 第 {page['number']} 页含无法识别的交易行，整次导入取消")
        entries.append({"carry": carry, "balance_cents": balance, "table_row": table_row})
        if not carry:
            # 这些字段在 PDF 中换行只是排版，不属于日期、金额或流水号内容。
            for label in ("序号", "交易日期", "会计日期", "借方发生额", "贷方发生额", "余额", "流水号", "对方账号"):
                if label in headers:
                    column = headers.index(label)
                    row[column] = "".join(row[column].split())
            rows.append(row)
    return {"number": page["number"], "identity": identity, "controls": controls, "entries": entries,
            "sheet": {"name": f"PDF第{page['number']}页", "rows": [["交通银行明细对账单"],
                       ["账号：", identity["账号"], "户名：", identity["户名"], "币种：", identity["币种"]], headers, *rows]}}


def read_bank_pdf(content, filename, config):
    limits = config["pdf_import"]
    pages = [page_statement(page, limits) for page in read_pdf_pages(content, limits)]
    count = sum(sum(not entry["carry"] for entry in page["entries"]) for page in pages)
    if not 1 <= count <= config["max_rows"]:
        raise ValueError(f"PDF 必须包含 1 至 {config['max_rows']:,} 笔交易；不会只导入前部分数据")
    rows, notes, _ = read_bank([page["sheet"] for page in pages], filename, config)
    if len(rows) != count:
        raise ValueError("PDF 表格行数与解析笔数不一致，整次导入取消")
    controls = validate_statement(pages, rows, limits)
    notes.append(f"识别为交通银行电子 PDF，共 {len(pages)} 页、{len(rows)} 笔交易；页码、连续序号、逐笔余额及收支金额和笔数均已校验")
    return rows, notes, controls
