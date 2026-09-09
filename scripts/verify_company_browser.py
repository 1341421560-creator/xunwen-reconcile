import base64
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
    directory = root / "temp" / ("company-browser-" + uuid4().hex[:8])
    (directory / "config").mkdir(parents=True)
    config = load_config(root)
    config["temp_dir"] = "temp"
    (directory / "config/defaults.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    shutil.copytree(root / "web", directory / "web")
    registry = CompanyRegistry(directory, config)
    names = {"moderate": config["company_name"], "haisi": "隔离浏览器测试海思有限公司"}
    uploads = {}
    for key, name in names.items():
        rows = [bank_row(f"R{n}", 122400 + n, date="2026-08-03" if n < 20 else "2026-09-03") for n in range(31)]
        for kind, records in (("bank", rows), ("invoice", [invoice_row(amount=155000)])):
            item = upload(records, kind, name)
            path = directory / "temp" / f"{key}-{kind}.xlsx"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(item["content"]))
            uploads[f"{key}_{kind}"] = str(path)
            if key == "moderate" and kind == "bank":
                service = registry.get(key)
                service.import_files({"revision": 0, "bank": item})
            if key == "moderate" and kind == "invoice":
                service.import_files({"revision": 1, "invoice": item})
    changed = upload([invoice_row(amount=160000)], "invoice", names["moderate"])
    changed_path = directory / "temp/moderate-changed.xlsx"
    changed_path.write_bytes(base64.b64decode(changed["content"]))
    uploads["moderate_changed"] = str(changed_path)
    (directory / "fixtures.json").write_text(json.dumps(uploads), encoding="utf-8")
    server = make_server(registry, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ, TEMP=str(directory / "temp"), TMP=str(directory / "temp"))
    print("隔离浏览器验证目录：" + str(directory), flush=True)
    try:
        with (directory / "http-server.log").open("w", encoding="utf-8") as log, contextlib.redirect_stderr(log):
            result = subprocess.run(["node", str(root / "tests/browser_companies.cjs"), f"http://127.0.0.1:{server.server_port}", str(directory)], env=env, timeout=300, capture_output=True, encoding="utf-8")
        (directory / "browser-run.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        print(result.stdout + result.stderr, flush=True)
        return result.returncode
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


if __name__ == "__main__":
    raise SystemExit(main())
