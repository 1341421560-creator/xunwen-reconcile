import re
from .normalize import cents


def integer(value, label):
    if not re.fullmatch(r"[0-9]+", value or ""):
        raise ValueError(f"PDF {label} 必须为完整的非负整数")
    return int(value)


def validate_statement(pages, records, limits):
    first = pages[0]["identity"]
    bill = None
    for index, page in enumerate(pages, 1):
        identity = page["identity"]
        if any(identity[key] != first[key] for key in ("账号", "户名", "币种", "年份", "月份")):
            raise ValueError("PDF 中公司、账号、币种或账期不一致，请按公司和月份分别导出")
        numbering = re.fullmatch(r"本月第([0-9]+)份-第([0-9]+)页", identity["页码"])
        if not numbering or int(numbering[2]) != index or bill is not None and bill != numbering[1]:
            raise ValueError("PDF 页码缺失、重复或顺序不连续，请导入从第 1 页开始的完整对账单")
        bill = numbering[1]
    year, month = integer(first["年份"], "年份"), integer(first["月份"], "月份")
    if not 1 <= month <= 12 or not 1900 <= year <= 9999:
        raise ValueError("PDF 账期年份或月份无效")
    period = f"{year:04d}-{month:02d}"
    previous = None
    sequence = 0
    for page in pages:
        for row in page["entries"]:
            if row["carry"]:
                if previous is not None and previous != row["balance_cents"]:
                    raise ValueError("PDF 承前或结转余额与上一笔不一致，请核对缺页或重复页")
                previous = row["balance_cents"]
                continue
            sequence += 1
            record = records[sequence - 1]
            if integer(record["source_sequence"], "交易序号") != sequence:
                raise ValueError("PDF 交易序号缺失、重复或不连续，整次导入取消")
            if not record["date"].startswith(period + "-"):
                raise ValueError("PDF 交易日期与页头账期不符")
            if previous is None:
                raise ValueError("PDF 缺少首笔交易之前的承前余额，无法完整校验")
            if previous + record["credit_cents"] - record["debit_cents"] != row["balance_cents"]:
                raise ValueError(f"PDF 第 {page['number']} 页第 {sequence} 笔余额与收支不一致，请核对原件")
            previous = row["balance_cents"]
            record.update(balance_cents=previous, row=row["table_row"], pdf_page=page["number"], source_bank="交通银行", bank_format="bocom_pdf")
    labels = limits["bocom"]["control_labels"]
    if not all(label in pages[-1]["controls"] for label in labels):
        raise ValueError("PDF 最后一页缺少完整金额及笔数控制信息，请导入完整银行电子对账单")
    expected = {}
    for label in labels:
        values = [page["controls"][label] for page in pages if label in page["controls"]]
        numbers = {cents(v) if label.endswith("额") else integer(v, label) for v in values}
        if len(numbers) != 1:
            raise ValueError(f"PDF 各页的{label}不一致")
        expected[label] = next(iter(numbers))
    controls = []
    for side, kind, label in (("借方", "debit", "支出"), ("贷方", "credit", "收入")):
        actual = sum(row[kind + "_cents"] for row in records)
        count = sum(row[kind + "_cents"] > 0 for row in records)
        amount = expected[f"本月累计{side}发生额"]
        counts = [expected[f"{scope}{side}发生数"] for scope in ("当前账单", "本月累计")]
        if amount != actual or any(value != count for value in counts):
            raise ValueError(f"PDF {label}金额或笔数与整份月度账单控制数不符，请核对是否缺页或仅导出了部分交易")
        controls.append({"type": kind, "label": label, "sheet": "PDF整份账单", "cents": amount, "actual_cents": actual,
                         "expected_count": count, "actual_count": count, "passed": True})
    return controls
