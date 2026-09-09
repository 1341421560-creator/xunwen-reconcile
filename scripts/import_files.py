import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reconcile.config import load_config
from reconcile.service import ReconciliationService


def main():
    parser = argparse.ArgumentParser(description="累计导入流水或发票并导出结果")
    parser.add_argument("--bank")
    parser.add_argument("--invoices")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    service = ReconciliationService(root, load_config(root))
    payload = {"revision": service.ledger()["revision"]}
    for key, value in (("bank", args.bank), ("invoice", args.invoices)):
        if value:
            path = Path(value)
            payload[key] = {"name": path.name, "content": base64.b64encode(path.read_bytes()).decode("ascii")}
    result = service.import_files(payload)
    exported = service.export({"revision": result["revision"]})
    print(json.dumps({"stats": result["stats"], "last_import": result["last_import"], "export": exported}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
