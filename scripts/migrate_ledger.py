import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reconcile.config import load_config
from reconcile.service import ReconciliationService
from reconcile.migration import migrate_legacy


def main():
    parser = argparse.ArgumentParser(description="备份并迁入当前公司累计账本，重复执行不会重复入账")
    parser.add_argument("--bank", required=True)
    parser.add_argument("--invoices", required=True)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    result = migrate_legacy(ReconciliationService(root, load_config(root)), args.bank, args.invoices)
    print(json.dumps({"revision": result["revision"], "stats": result["stats"], "migration_skipped": result.get("migration_skipped", False)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
