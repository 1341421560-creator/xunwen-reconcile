import argparse
import json
from pathlib import Path
from deployment.data_backup import backup_data
from deployment.data_restore import restore_data


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="本机账本备份与新电脑恢复")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--source-root", default=str(root))
    backup.add_argument("--destination", default=str(root / "data-transfer"))
    restore = commands.add_parser("restore")
    restore.add_argument("--archive", required=True)
    args = parser.parse_args()
    try:
        result = backup_data(args.source_root, args.destination) if args.command == "backup" else restore_data(root, args.archive)
    except (OSError, ValueError) as exc:
        raise SystemExit("操作未完成：" + str(exc)) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
