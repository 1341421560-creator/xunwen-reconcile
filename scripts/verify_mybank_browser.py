import argparse
import base64
import hashlib
from decimal import Decimal
from pathlib import Path
from bank_browser_support import verify_bank_browser
from reconcile.config import load_config
from reconcile.excel_reader import read_sheets
from reconcile.import_reader import read_uploads


def inspect_source(source, config):
    content = source.read_bytes()
    sheets = read_sheets(content, source.name, config["max_rows"])
    if len(sheets) != 1:
        raise ValueError("原文件浏览器验收使用单工作表网商银行标准样例")
    raw = sheets[0]["rows"]
    if raw[4][:6] != ["账务流水号", "提交时间", "交易时间", "交易名称", "借方金额(收)", "贷方金额(支)"]:
        raise ValueError("验收样例表头与独立核算使用的标准列不符")
    transactions = [row for row in raw[5:] if len(row) > 2 and row[2]]
    # 独立按原始列核算，不使用解析器计算的收支合计作为预期值。
    expected = {"count": len(transactions), "references": [str(row[0]) for row in transactions], "months": sorted({str(row[2])[:7] for row in transactions})}
    for field, column in (("credit", 4), ("debit", 5)):
        amounts = [Decimal(str(row[column] or 0)) for row in transactions]
        expected[field + "_cents"] = int(sum(amounts) * 100)
        expected[field + "_count"] = sum(amount > 0 for amount in amounts)
    item = {"name": source.name, "content": base64.b64encode(content).decode("ascii")}
    parsed = read_uploads({"bank": item}, config)
    assert [row["reference"] for row in parsed["bank"]] == expected["references"]
    for row, raw_row in zip(parsed["bank"], transactions, strict=True):
        assert row["date"] == str(raw_row[2])[:10]
        assert row["credit_cents"] == int(Decimal(str(raw_row[4] or 0)) * 100)
        assert row["debit_cents"] == int(Decimal(str(raw_row[5] or 0)) * 100)
    expected["source_sha256"] = hashlib.sha256(content).hexdigest()
    return expected



def main():
    parser = argparse.ArgumentParser(description="在新建临时账本中验证网商银行原文件和浏览器导入")
    parser.add_argument("--source", required=True)
    parser.add_argument("--company-name", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = Path(args.source).resolve(strict=True)
    config = load_config(root)
    expected = inspect_source(source, dict(config, company_name=args.company_name))
    expected["format_label"] = "网商银行"
    return verify_bank_browser(root, source, args.company_name, expected)


if __name__ == "__main__":
    raise SystemExit(main())
