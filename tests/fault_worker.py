import argparse
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fault_support import CONFIG, ReconciliationService, invoice_row, upload


def main():
    parser = argparse.ArgumentParser(description="仅对临时账本模拟进程异常退出")
    parser.add_argument("directory")
    parser.add_argument("mode", choices=("lock", "before_replace"))
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    service = ReconciliationService(directory, dict(CONFIG, temp_dir="temp"))
    if args.mode == "lock":
        with service.store.exclusive():
            (service.store.temp / "worker-ready.txt").write_text("locked", encoding="utf-8")
            time.sleep(60)
    else:
        import reconcile.ledger_storage as storage
        storage.os.replace = lambda *_: os._exit(91)
        service.import_files({"revision": service.ledger()["revision"], "invoice": upload([invoice_row("AFTER_CRASH", 500)], "invoice", CONFIG["company_name"])})


if __name__ == "__main__":
    main()
