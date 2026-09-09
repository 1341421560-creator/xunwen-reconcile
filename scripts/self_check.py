import argparse
import json
import struct
import sys
from pathlib import Path
from uuid import uuid4
from deployment.runtime_probe import main as check_dependencies
from reconcile.config import load_config
from reconcile.company_registry import CompanyRegistry


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="检查指定公司的运行环境与账本")
    parser.add_argument("--company", default="moderate")
    args = parser.parse_args()
    config = load_config(root)
    check_dependencies()
    service = CompanyRegistry(root, config).get(args.company)
    config = service.config
    view = service.ledger()
    probe = root / config["temp_dir"] / ("write-check-" + uuid4().hex + ".txt")
    probe.write_text("目录写入检查通过", encoding="utf-8")
    result = {"passed": True, "python_version": sys.version.split()[0], "python_bits": struct.calcsize("P") * 8,
              "interpreter": sys.executable, "company": config["company_name"], "ledger_revision": view["revision"],
              "bank_count": view["stats"]["bank_count"], "invoice_count": view["stats"]["invoice_count"],
              "offline": True, "project_directory": str(root)}
    (root / config["temp_dir"] / "self-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
