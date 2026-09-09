import argparse
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "tests"))
from reconcile.config import load_config
from capacity_verification import run_capacity_checks


def main():
    parser = argparse.ArgumentParser(description="使用固定模板验证可变行数读取")
    parser.add_argument("--bank", required=True)
    parser.add_argument("--invoices", required=True)
    parser.add_argument("--libreoffice", required=True)
    parser.add_argument("--temp-dir", required=True)
    args = parser.parse_args()
    result = run_capacity_checks(args.bank, args.invoices, args.libreoffice, args.temp_dir, load_config(root))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
