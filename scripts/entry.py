import runpy
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    targets = {"server": root / "run.py", "launch": root / "scripts" / "launch_service.py", "check": root / "scripts" / "self_check.py",
               "test": root / "scripts" / "run_tests.py", "data": root / "scripts" / "data_transfer.py",
               "import": root / "scripts" / "import_files.py", "migrate": root / "scripts" / "migrate_ledger.py"}
    if len(sys.argv) < 2 or sys.argv[1] not in targets:
        raise SystemExit("请选择 server、check、test、data、import 或 migrate")
    target = targets[sys.argv[1]]
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(target.parent), str(root)]
    sys.argv = [str(target), *sys.argv[2:]]
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
