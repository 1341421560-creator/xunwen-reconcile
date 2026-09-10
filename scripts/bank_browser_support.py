import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import threading
from uuid import uuid4
from reconcile.company_registry import CompanyRegistry
from reconcile.config import load_config
from reconcile.http_server import make_server


def verify_bank_browser(root, source, company_name, expected):
    directory = root / "temp" / ("bank-browser-" + uuid4().hex[:8])
    (directory / "temp").mkdir(parents=True, exist_ok=False)
    config = load_config(root)
    config["company_name"] = "浏览器隔离对照测试有限公司"
    config["temp_dir"] = "temp"
    (directory / "config").mkdir()
    (directory / "config/defaults.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    shutil.copytree(root / "web", directory / "web")
    registry = CompanyRegistry(directory, config)
    service = registry.activate({"company_key": "haisi", "legal_name": company_name})
    expected = dict(expected, upload="temp/bank-source" + source.suffix.lower(), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    shutil.copyfile(source, directory / expected["upload"])
    (directory / "fixtures.json").write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")
    server = make_server(registry, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ, TEMP=str(directory / "temp"), TMP=str(directory / "temp"))
    print("银行原文件隔离验收目录：" + str(directory), flush=True)
    try:
        with (directory / "http-server.log").open("w", encoding="utf-8") as log, contextlib.redirect_stderr(log):
            result = subprocess.run(["node", str(root / "tests/browser_mybank.cjs"), f"http://127.0.0.1:{server.server_port}", str(directory)], env=env, timeout=300, capture_output=True, encoding="utf-8")
        (directory / "browser-run.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        print(result.stdout + result.stderr, flush=True)
        if result.returncode:
            return result.returncode
        # 新建服务实例模拟重启，验证已提交账本及重复来源。
        previous = service.ledger()
        restarted = CompanyRegistry(directory, config).get("haisi").ledger()
        assert previous == restarted
        assert len(restarted["bank"]) == expected["count"]
        assert len(restarted["batches"]) == 2
        assert not registry.ledger_path("moderate").exists()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected["source_sha256"]
        (directory / "source-result.json").write_text(json.dumps({"passed": True, "restart_verified": True, "expected": expected}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("原文件逐行核算、浏览器导入、重复导入及重启读取均通过", flush=True)
        return 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
