import argparse
import concurrent.futures
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="在临时目录全新 clone，验证离线运行与私有数据恢复")
    parser.add_argument("--directory", required=True)
    parser.add_argument("--data-archive", required=True)
    parser.add_argument("--port", type=int, default=8781)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = Path(args.directory).resolve()
    clone = directory / "clone 中文"
    directory.mkdir(parents=True, exist_ok=True)
    if clone.exists():
        raise SystemExit("验证目录已经存在，请指定新的临时目录")
    subprocess.run(["git", "clone", "--no-local", str(root), str(clone)], check=True, timeout=300)
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    environment = dict(os.environ)
    environment.update(PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
                       PYTHONHOME=str(directory / "unavailable-python"), PYTHONPATH=str(directory / "unavailable-packages"))
    records = []

    def execute(script, *arguments, expected=0, label=None):
        key = label or script
        log_path = directory / (key.replace(".ps1", "") + ".log")
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run([str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(clone / "scripts" / script), *map(str, arguments)],
                                       cwd=clone, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=300,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
        success = completed.returncode == 0 if expected == 0 else completed.returncode != 0
        records.append({"scenario": key, "passed": success, "exit_code": completed.returncode})
        if not success:
            raise RuntimeError(key + "\n" + log_path.read_text(encoding="utf-8"))
        return completed

    def get_bootstrap():
        with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/api/bootstrap", timeout=5) as response:
            return json.load(response)

    try:
        execute("check.ps1", label="01-no-system-python-check")
        check = json.loads((clone / "temp" / "self-check.json").read_text(encoding="utf-8"))
        if check["bank_count"] != 0 or check["invoice_count"] != 0:
            raise RuntimeError("Git clone 不应携带真实账本")
        wheel = clone / "downloads" / "xlrd-2.0.2-py2.py3-none-any.whl"
        original = wheel.read_bytes()
        try:
            wheel.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            execute("check.ps1", expected=1, label="02-corrupted-offline-archive-rejected")
        finally:
            wheel.write_bytes(original)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(execute, "start.ps1", "-Port", args.port, "-NoBrowser", label=f"03-concurrent-start-{i}") for i in range(3)]
            for future in futures:
                future.result()
        if get_bootstrap()["result"]["stats"]["bank_count"] != 0:
            raise RuntimeError("空账本启动不正确")
        logs = [p for p in (clone / "temp").glob("server-*.log") if not p.name.endswith(".error.log")]
        if len(logs) != 1:
            raise RuntimeError("重复启动产生了多个服务进程")
        execute("stop.ps1", "-Port", args.port, label="04-stop-owned-server")
        execute("restore.ps1", "-Archive", Path(args.data_archive).resolve(), label="05-restore-private-data")
        ledger_bytes = (clone / "ledger" / "company-ledger.v1.json").read_bytes()
        execute("restore.ps1", "-Archive", Path(args.data_archive).resolve(), label="06-repeat-restore")
        if (clone / "ledger" / "company-ledger.v1.json").read_bytes() != ledger_bytes:
            raise RuntimeError("重复恢复改变了账本内容")
        execute("start.ps1", "-Port", args.port, "-NoBrowser", label="07-start-restored-ledger")
        boot = get_bootstrap()
        records.append({"scenario": "08-restored-ledger-counts", "passed": True,
                        "revision": boot["result"]["revision"], "stats": boot["result"]["stats"]})
        execute("test.ps1", label="09-all-tests-from-clone")
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=clone, timeout=300).decode("utf-8")
        if status:
            raise RuntimeError("运行产生了未忽略的文件：" + status)
        records.append({"scenario": "10-git-worktree-clean-after-running", "passed": True})
        # 浏览器烟雾检查可在此命令完成后针对保留的隔离服务执行。
        print(json.dumps({"passed": True, "clone": str(clone), "port": args.port}, ensure_ascii=False))
    finally:
        (directory / "clone-validation.json").write_text(json.dumps({"records": records, "all_recorded_checks_passed": all(r["passed"] for r in records)}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
