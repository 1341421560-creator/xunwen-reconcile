import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4
from reconcile.config import load_config
from reconcile.company_registry import CompanyRegistry
from reconcile.http_server import make_server


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "tests"))
    from ledger_fixtures import bank_row, invoice_row, upload
    directory = root / "temp" / ("invoice-browser-" + uuid4().hex[:8])
    (directory / "config").mkdir(parents=True)
    (directory / "temp").mkdir()
    config = load_config(root)
    config["temp_dir"] = "temp"
    (directory / "config/defaults.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    shutil.copytree(root / "web", directory / "web")
    registry = CompanyRegistry(directory, config)
    rows = [invoice_row(f"普通{n:03}", 10001 + n, party="合计测试供应商", date="2026-08-03" if n < 120 else "2026-09-03") for n in range(205)]
    rows += [dict(invoice_row("红字测试", -12345), red=True),
             dict(invoice_row("作废测试", 10001), source_status="作废"),
             invoice_row("差额测试", 155000, party="深圳市富芯通电子有限公司")]
    banks = [bank_row(f"B{n}", 20000 + n, party="支出统计对象") for n in range(20)]
    for row, summary in zip(banks, ["8月缴税", "代运营服务货款", "报销款", "租金水电"]):
        row["summary"] = summary
    banks.append(bank_row("差额付款", 122400, party="深圳市富芯通电子有限公司"))
    names = {"moderate": config["company_name"], "haisi": "隔离验证海思有限公司"}
    for key, name in names.items():
        service = registry.get(key) if key == "moderate" else registry.activate(dict(company_key=key, legal_name=name))
        view = service.import_files(dict(revision=service.ledger()["revision"], bank=upload(banks, "bank", name), invoice=upload(rows, "invoice", name)))
        bid = next(row["id"] for row in view["bank"] if row["reference"] == "差额付款")
        iid = next(row["id"] for row in view["invoices"] if row["number"] == "差额测试")
        service.review(dict(revision=view["revision"], allocations=[dict(bank_id=bid, invoice_id=iid, amount_cents=122400)], note="验证差额保持限制"))
    server = make_server(registry, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ, TEMP=str(directory / "temp"), TMP=str(directory / "temp"))
    print("隔离浏览器验证目录：" + str(directory), flush=True)
    try:
        with (directory / "http-server.log").open("w", encoding="utf-8") as log, contextlib.redirect_stderr(log):
            result = subprocess.run(["node", str(root / "tests/browser_invoice_selection.cjs"), f"http://127.0.0.1:{server.server_port}", str(directory)], env=env, timeout=300, capture_output=True, encoding="utf-8")
        (directory / "browser-run.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        print(result.stdout + result.stderr, flush=True)
        return result.returncode
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


if __name__ == "__main__":
    raise SystemExit(main())
