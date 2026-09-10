import argparse
import base64
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import threading
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from reconcile.company_registry import CompanyRegistry
from reconcile.config import load_config
from reconcile.excel_reader import read_sheets
from reconcile.http_server import make_server
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
    directory = root / "temp" / ("mybank-browser-" + uuid4().hex[:8])
    (directory / "temp").mkdir(parents=True, exist_ok=False)
    config["company_name"] = "浏览器隔离对照测试有限公司"
    config["temp_dir"] = "temp"
    (directory / "config").mkdir()
    (directory / "config/defaults.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    shutil.copytree(root / "web", directory / "web")
    registry = CompanyRegistry(directory, config)
    service = registry.activate({"company_key": "haisi", "legal_name": args.company_name})
    copied = directory / "temp" / "mybank-source.xlsx"
    shutil.copyfile(source, copied)
    expected["upload"] = "temp/mybank-source.xlsx"
    (directory / "fixtures.json").write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")
    server = make_server(registry, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ, TEMP=str(directory / "temp"), TMP=str(directory / "temp"))
    print("网商银行原文件隔离验收目录：" + str(directory), flush=True)
    try:
        with (directory / "http-server.log").open("w", encoding="utf-8") as log, contextlib.redirect_stderr(log):
            result = subprocess.run(["node", str(root / "tests/browser_mybank.cjs"), f"http://127.0.0.1:{server.server_port}", str(directory)], env=env, timeout=300, capture_output=True, encoding="utf-8")
        (directory / "browser-run.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        print(result.stdout + result.stderr, flush=True)
        if result.returncode:
            return result.returncode
        # 新建服务实例模拟重启，校验已提交账本及重复来源仍可读取。
        previous = service.ledger()
        restarted = CompanyRegistry(directory, config).get("haisi").ledger()
        assert previous == restarted
        assert len(restarted["bank"]) == expected["count"]
        assert len(restarted["batches"]) == 2
        assert not registry.ledger_path("moderate").exists()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected["source_sha256"]
        (directory / "source-result.json").write_text(json.dumps({"passed": True, "restart_verified": True, "expected": expected}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("原文件逐行核算、浏览器导入、重复导入及重启读取均通过", flush=True)
        return 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


if __name__ == "__main__":
    raise SystemExit(main())
