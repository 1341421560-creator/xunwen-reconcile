import os
from uuid import uuid4
from reconcile.atomic_write import replace_snapshot
from reconcile.company_profiles import safe_company_path
from .archive_validation import LEDGER_PATH, check_member


FOLDERS = {"ledger": "ledger_dir", "sessions": "session_dir", "reports": "report_dir"}


def target_path(service, name):
    path = check_member(name)
    return safe_company_path(service.root, service.root / service.config[FOLDERS[path.parts[0]]] / str(path.relative_to(path.parts[0])))


def existing_files(service):
    result = {}
    for folder, field in FOLDERS.items():
        base = safe_company_path(service.root, service.root / service.config[field])
        for file in sorted(base.rglob("*")):
            safe_company_path(service.root, file)
            if file.is_file():
                name = folder + "/" + file.relative_to(base).as_posix()
                check_member(name)
                result[name] = file
    return result


def collect(service):
    ledger = service.store.load()
    content = {name: file.read_bytes() for name, file in existing_files(service).items()}
    if LEDGER_PATH not in content:
        raise ValueError("当前目录还没有正式账本，无需备份")
    return ledger, content


def preflight(service, content):
    for name, value in content.items():
        target = target_path(service, name)
        if target.exists() and (not target.is_file() or target.read_bytes() != value):
            raise ValueError("目标已有不同的数据，已停止恢复且不覆盖原记录：" + name)
        if any(parent.exists() and not parent.is_dir() for parent in target.parents if parent != service.root):
            raise ValueError("恢复目标的父路径不是目录：" + name)
    for name, file in existing_files(service).items():
        if name not in content:
            raise ValueError("目标目录已有迁移包之外的数据，请恢复到全新的软件目录：" + name)


def stage_content(service, content):
    stage = service.store.temp / ("restore-" + uuid4().hex[:12])
    stage.mkdir(parents=True)
    result = {}
    for index, (name, value) in enumerate(content.items()):
        path = stage / str(index)
        with path.open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        result[name] = path
    return result


def publish(service, content, staged, publisher=replace_snapshot):
    # 每家公司先发布历史与导出，账本最后原子发布；相同文件跳过，支持中断后重试。
    names = [name for name in content if name != LEDGER_PATH] + [LEDGER_PATH]
    for name in names:
        target = target_path(service, name)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            publisher(staged[name], target)
    return service.store.load()
