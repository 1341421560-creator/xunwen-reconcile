import argparse
import json
from pathlib import Path
from bank_browser_support import verify_bank_browser
from reconcile.bocom_pdf_import import read_bank_pdf
from reconcile.config import load_config


def main():
    parser = argparse.ArgumentParser(description="使用独立核对的逐笔预期结果验收 PDF 原件，全部数据写入临时账本")
    parser.add_argument("--source", required=True)
    parser.add_argument("--expected", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = Path(args.source).resolve(strict=True)
    expected = json.loads(Path(args.expected).read_text(encoding="utf-8-sig"))
    config = dict(load_config(root), company_name=expected["company_name"])
    rows, _, _ = read_bank_pdf(source.read_bytes(), source.name, config)
    assert len(rows) == expected["count"] == len(expected["rows"])
    for actual, wanted in zip(rows, expected["rows"], strict=True):
        for field, value in wanted.items():
            assert actual[field] == value, (actual["reference"], field, actual[field], value)
    assert sum(row["debit_cents"] for row in rows) == expected["debit_cents"]
    assert sum(row["credit_cents"] for row in rows) == expected["credit_cents"]
    expected["references"] = [row["reference"] for row in expected["rows"]]
    expected["months"] = sorted({row["date"][:7] for row in expected["rows"]})
    expected["format_label"] = "交通银行电子 PDF"
    return verify_bank_browser(root, source, expected["company_name"], expected)


if __name__ == "__main__":
    raise SystemExit(main())
