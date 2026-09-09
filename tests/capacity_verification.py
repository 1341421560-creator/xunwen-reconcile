import json
import os
import subprocess
import time
from pathlib import Path
from reconcile.excel_reader import read_sheets
from reconcile.parsers import parse_bank, parse_invoices
from reconcile.matching import build_result
from capacity_fixtures import bank_rows, invoice_rows, write_fods, write_xlsx_fixture


def run_capacity_checks(bank_source, invoice_source, libreoffice, directory, config):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    bank_content, invoice_content = Path(bank_source).read_bytes(), Path(invoice_source).read_bytes()
    bank_template = read_sheets(bank_content, "source.xls")[0]["rows"]
    invoice_template = read_sheets(invoice_content, "source.xlsx")[0]["rows"]
    bank_cases, invoice_cases, results = [], [], []
    for count, page_size in ((1000, None), (10000, None), (29987, None), (1000, 25), (29988, None)):
        rows, expected = bank_rows(bank_template, count, page_size)
        stem = f"bank_{count}" + ("_paged" if page_size else "")
        write_fods(directory / (stem + ".fods"), [("CorpDetailSpecial", rows)])
        bank_cases.append((stem, expected, count == 29988))
    first, expected_first = bank_rows(bank_template, 500)
    second, expected_second = bank_rows(bank_template, 500)
    for row in second:
        if len(row) > 15 and str(row[15]).startswith("CAPACITY"):
            row[15] = "SECOND" + str(row[15])
    write_fods(directory / "bank_two_sheets.fods", [("Page1", first), ("Page2", second)])
    bank_cases.append(("bank_two_sheets", {"count": 1000, "debit_cents": expected_first["debit_cents"] + expected_second["debit_cents"], "credit_cents": expected_first["credit_cents"] + expected_second["credit_cents"], "last_sequence": "500", "physical_rows": len(first) + len(second)}, False))
    env = dict(os.environ, TEMP=str(directory), TMP=str(directory))
    conversion = subprocess.run([str(libreoffice), "-env:UserInstallation=" + (directory / "lo-profile").as_uri(), "--headless", "--convert-to", "xls:MS Excel 97", "--outdir", str(directory), *[str(directory / (stem + ".fods")) for stem, _, _ in bank_cases]], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=300)
    (directory / "conversion.log").write_text(conversion.stdout + conversion.stderr, encoding="utf-8")
    if conversion.returncode:
        raise RuntimeError("测试 XLS 转换失败，请检查 conversion.log")
    for count, repeated in ((1000, False), (10000, False), (29997, False), (1000, True), (29998, False)):
        rows, expected = invoice_rows(invoice_template, count, repeated)
        stem = f"invoice_{count}" + ("_paged" if repeated else "")
        write_xlsx_fixture(directory / (stem + ".xlsx"), invoice_content, rows)
        invoice_cases.append((stem, expected, count == 29998))
    parsed_bank = parsed_invoice = None
    for kind, cases, extension, parser in (("流水", bank_cases, ".xls", parse_bank), ("发票", invoice_cases, ".xlsx", parse_invoices)):
        for stem, expected, overflow in cases:
            path = directory / (stem + extension)
            content = path.read_bytes()
            started = time.perf_counter()
            if overflow:
                try:
                    read_sheets(content, path.name, config["max_rows"])
                except ValueError as exc:
                    assert "30,000" in str(exc) and "未截取" in str(exc), str(exc)
                    results.append({"case": stem, "kind": kind, "expected_rows": expected["physical_rows"], "result": "明确拒绝超限，未截断", "message": str(exc)})
                    continue
                raise AssertionError(f"超限文件未被拒绝：{stem}")
            sheets = read_sheets(content, path.name, config["max_rows"])
            parsed = parser(sheets, path.name, config)
            records = parsed[0]
            assert len(records) == expected["count"], (stem, len(records), expected)
            assert records[-1]["source_sequence"] == expected["last_sequence"], stem
            if kind == "流水":
                assert sum(r["debit_cents"] for r in records) == expected["debit_cents"], stem
                assert sum(r["credit_cents"] for r in records) == expected["credit_cents"], stem
                assert all(c["passed"] for c in parsed[2]), stem
                if stem == "bank_10000":
                    parsed_bank = records
            else:
                assert records[-1]["number"] == expected["last_number"], stem
                assert sum(r["amount_cents"] for r in records) == expected["net_cents"], stem
                if stem == "invoice_10000":
                    parsed_invoice = records
            results.append({"case": stem, "kind": kind, "expected_records": expected["count"], "read_records": len(records), "last_sequence": records[-1]["source_sequence"], "last_source_row": records[-1]["row"], "physical_rows": sum(len(s["rows"]) for s in sheets), "total_verified": True, "seconds": round(time.perf_counter() - started, 3), "file_bytes": len(content), "result": "通过"})
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    result = build_result(parsed_bank, parsed_invoice, {"exclude_special": True, "aliases": {}}, config)
    assert result["stats"]["bank_count"] == 10000 and result["stats"]["invoice_count"] == 10000
    assert result["stats"]["matched"]["count"] == 8000 and result["stats"]["excluded"]["count"] == 2000
    assert result["bank"][-1]["source_sequence"] == "10000"
    results.append({"case": "10000_by_10000_full_matching", "kind": "完整比对", "bank_records": 10000, "invoice_records": 10000, "matched": 8000, "income_excluded": 2000, "result": "通过"})
    # 检查存在序号的坏数据会报错，而不是悄悄减少记录数。
    for kind, rows, parser in (("流水", bank_rows(bank_template, 1000)[0], parse_bank), ("发票", invoice_rows(invoice_template, 1000)[0], parse_invoices)):
        if kind == "流水":
            rows[507][6] = rows[507][7] = ""
        else:
            for column in range(1, len(rows[501])):
                rows[501][column] = ""
        try:
            parser([{"name": "缺失金额验证", "rows": rows}], "fixture", config)
        except ValueError:
            results.append({"case": kind + "记录字段缺失", "kind": kind, "result": "明确报错，未跳过交易"})
        else:
            raise AssertionError("坏数据被静默跳过")
    summary = {"passed": True, "results": results, "fixtures_dir": str(directory), "limits": {"max_rows_per_sheet": config["max_rows"], "max_upload_mb": config["max_upload_mb"]}}
    (directory / "capacity-results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
