import os
from pathlib import Path
from uuid import uuid4
from reconcile.config import load_config
from reconcile.ledger_storage import LedgerStore
from reconcile.atomic_write import replace_snapshot
from .archive_validation import LEDGER_PATH, read_archive


def safe_target(root, name):
    target = root / name
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("恢复目录存在越界链接")
    for parent in [target, *target.parents]:
        if parent == root:
            break
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ValueError("恢复路径不能包含符号链接或联接点")
    return target


def restore_data(root, archive):
    root = Path(root).resolve()
    config = load_config(root)
    manifest, content = read_archive(archive, config["company_name"])
    if any(config[key] != name for key, name in (("ledger_dir", "ledger"), ("session_dir", "sessions"), ("report_dir", "reports"))):
        raise ValueError("自动恢复要求使用默认数据目录")
    for name in content:
        safe_target(root, name)
    store = LedgerStore(root / config["ledger_dir"], root / config["temp_dir"], config["company_name"])
    with store.exclusive():
        for name, value in content.items():
            target = safe_target(root, name)
            if target.exists() and (not target.is_file() or target.read_bytes() != value):
                raise ValueError("目标已有不同的数据，已停止恢复且不覆盖原记录：" + name)
        if not store.path.exists():
            for folder in ("ledger", "sessions", "reports"):
                for file in (root / folder).rglob("*"):
                    if file.is_file() and file.relative_to(root).as_posix() not in content:
                        raise ValueError("目标目录已有迁移包之外的数据，请恢复到全新的软件目录")
        # 暂存文件使用短名称，避免中文长目录加上历史报表名超出 Windows 路径限制。
        stage = root / config["temp_dir"] / ("restore-" + uuid4().hex[:12])
        stage.mkdir(parents=True)
        staged = {}
        for index, (name, value) in enumerate(content.items()):
            target = stage / str(index)
            with target.open("xb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            staged[name] = target
        # 中断后可再次恢复同一个包；逐个相同文件跳过，账本最后原子发布。
        for name, value in content.items():
            if name == LEDGER_PATH:
                continue
            target = safe_target(root, name)
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                replace_snapshot(staged[name], target)
        if not store.path.exists():
            replace_snapshot(staged[LEDGER_PATH], store.path)
        ledger = store.load()
    return {"restored": True, "revision": ledger["revision"], "files": len(content),
            "bank_count": len(ledger["bank"]), "invoice_count": len(ledger["invoices"])}
