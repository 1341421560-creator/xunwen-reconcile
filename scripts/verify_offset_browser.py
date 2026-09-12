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
    directory = root / "temp" / ("offset-browser-" + uuid4().hex[:8])
    (directory / "config").mkdir(parents=True)
    (directory / "temp").mkdir()
    config = load_config(root)
    (directory / "config/defaults.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    shutil.copytree(root / "web", directory / "web")
    registry = CompanyRegistry(directory, config)
    banks = [bank_row("富芯通付款", 122400, party="深圳市富芯通电子有限公司"), bank_row("超额付款", 100000, party="超额测试公司")]
    blue = [invoice_row("富芯通原票", 155000, party="深圳市富芯通电子有限公司"), invoice_row("超额原票", 100000, party="超额测试公司"),
            invoice_row("全额原票", 100000, party="全额测试公司"), invoice_row("多票原票1", 100000, party="多票测试公司"), invoice_row("多票原票2", 100000, party="多票测试公司")]
    red = [dict(invoice_row(number, -amount, party=party, date="2026-10-03"), red=True) for number, amount, party in
           [("富芯通红票", 32600, "深圳市富芯通电子有限公司"), ("超额红票", 20000, "超额测试公司"), ("全额红票", 100000, "全额测试公司"), ("多票红票", 20000, "多票测试公司")]]
    for key, name in {"moderate": config["company_name"], "haisi": "隔离验证海思有限公司"}.items():
        service = registry.get(key) if key == "moderate" else registry.activate(dict(company_key=key, legal_name=name))
        view = service.import_files(dict(revision=service.ledger()["revision"], bank=upload(banks, "bank", name), invoice=upload(blue, "invoice", name)))
        bid = next(b["id"] for b in view["bank"] if b["reference"] == "富芯通付款")
        iid = next(i["id"] for i in view["invoices"] if i["number"] == "富芯通原票")
        service.review(dict(revision=view["revision"], allocations=[dict(bank_id=bid, invoice_id=iid, amount_cents=122400)], note="多开"))
        service.import_files(dict(revision=service.ledger()["revision"], invoice=upload(red, "invoice", name)))
    server = make_server(registry, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ, TEMP=str(directory / "temp"), TMP=str(directory / "temp"))
    print("隔离浏览器验证目录：" + str(directory), flush=True)
    try:
        with (directory / "http-server.log").open("w", encoding="utf-8") as log, contextlib.redirect_stderr(log):
            result = subprocess.run(["node", str(root / "tests/browser_invoice_offsets.cjs"), f"http://127.0.0.1:{server.server_port}", str(directory)], env=env, timeout=300, capture_output=True, encoding="utf-8")
        (directory / "browser-run.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        print(result.stdout + result.stderr, flush=True)
        return result.returncode
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


if __name__ == "__main__":
    raise SystemExit(main())
