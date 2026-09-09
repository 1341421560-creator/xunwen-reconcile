import argparse
import json
from pathlib import Path
from deployment.company_transfer import backup_companies, restore_companies


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="独立公司账本备份与恢复")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--source-root", default=str(root))
    backup.add_argument("--destination", default=str(root / "data-transfer"))
    restore = commands.add_parser("restore")
    restore.add_argument("--root", default=str(root))
    restore.add_argument("--archive", required=True)
    for command in (backup, restore):
        scope = command.add_mutually_exclusive_group()
        scope.add_argument("--company", help="公司标识：haisi / moderate / dongguan_xunwen / huachuangxing；缺省为摩德瑞特")
        scope.add_argument("--all", action="store_true", help="备份全部公司，或恢复包中全部公司")
    args = parser.parse_args()
    try:
        options = dict(company_key=args.company, all_companies=args.all)
        result = backup_companies(args.source_root, args.destination, **options) if args.command == "backup" else restore_companies(args.root, args.archive, **options)
    except (OSError, ValueError) as exc:
        raise SystemExit("操作未完成：" + str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
