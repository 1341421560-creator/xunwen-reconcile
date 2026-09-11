import hashlib
import os
from .audit import identifier
from .atomic_write import replace_snapshot
from .company_profiles import safe_company_path


def backup_snapshot(root, store, content):
    # 更正前的快照归入账本目录，随既有账本备份与迁移功能一起保留。
    directory = safe_company_path(root, store.directory / "name-correction-backups")
    directory.mkdir(parents=True, exist_ok=True)
    filename = identifier("before-") + ".json"
    target = safe_company_path(root, directory / filename)
    stage = safe_company_path(root, store.temp / filename)
    with stage.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    if stage.read_bytes() != content:
        raise ValueError("更正前备份校验失败，原账本未修改")
    replace_snapshot(stage, target)
    if target.read_bytes() != content:
        raise ValueError("更正前备份校验失败，原账本未修改")
    return {"path": target.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest()}
