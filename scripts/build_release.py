import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="仅打包已提交的 Git 文件，数据目录不进入软件包")
    parser.add_argument("--destination")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = Path(args.destination).resolve() if args.destination else root / "output"
    destination.mkdir(parents=True, exist_ok=True)
    status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root)
    if status:
        raise SystemExit("请先提交需交付的文件，再构建软件包；业务数据应保持被忽略")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"], cwd=root).decode("utf-8").strip("\x00").split("\x00")
    forbidden = ("ledger/", "sessions/", "reports/", "backups/", "temp/", "data-transfer/", "output/")
    if any(name.startswith(forbidden) or name.endswith((".xls", ".xlsx", ".csv")) for name in names):
        raise SystemExit("发现业务数据被 Git 跟踪，停止生成软件包")
    archive_path = destination / ("xunwen-reconcile-windows-offline-" + commit[:8] + ".zip")
    manifest = {"commit": commit, "files": []}
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            content = (root / name).read_bytes()
            archive.writestr("xunwen-reconcile/" + name, content)
            manifest["files"].append({"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    manifest["archive"] = archive_path.name
    manifest["archive_sha256"] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    manifest_path = destination / ("software-manifest-" + commit[:8] + ".json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"archive": str(archive_path), "manifest": str(manifest_path), "files": len(names), "bytes": archive_path.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
