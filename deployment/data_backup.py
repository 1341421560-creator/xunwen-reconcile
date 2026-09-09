import json
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from reconcile.config import load_config
from reconcile.ledger_storage import LedgerStore
from .archive_validation import DATA_FOLDERS, LEDGER_PATH, check_member, digest, read_archive


def backup_data(source_root, destination):
    root, destination = Path(source_root).resolve(), Path(destination).resolve()
    config = load_config(root)
    store = LedgerStore(root / config["ledger_dir"], root / config["temp_dir"], config["company_name"])
    if not store.path.exists():
        raise ValueError("当前目录还没有正式账本，无需备份")
    content = {}
    with store.exclusive():
        ledger = store.load()
        content[LEDGER_PATH] = store.path.read_bytes()
        for folder, key in (("sessions", "session_dir"), ("reports", "report_dir")):
            source = root / config[key]
            if source.is_symlink():
                raise ValueError("数据目录不能是符号链接")
            for file in sorted(source.rglob("*")):
                if file.is_symlink() or (hasattr(file, "is_junction") and file.is_junction()):
                    raise ValueError("数据目录不能包含符号链接或联接点")
                if file.is_file():
                    name = folder + "/" + file.relative_to(source).as_posix()
                    check_member(name)
                    content[name] = file.read_bytes()
    manifest = {"schema_version": 1, "kind": "xunwen-data-transfer", "created_at": datetime.now().astimezone().isoformat(),
                "company_name": ledger["company"]["name"], "revision": ledger["revision"],
                "files": [{"path": name, "size": len(value), "sha256": digest(value)} for name, value in sorted(content.items())]}
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / ("循文账本迁移包-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8] + ".zip")
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, value in content.items():
            archive.writestr(name, value)
    read_archive(path, config["company_name"])
    return {"archive": str(path), "sha256": digest(path.read_bytes()), "revision": ledger["revision"],
            "files": len(content), "bank_count": len(ledger["bank"]), "invoice_count": len(ledger["invoices"])}
