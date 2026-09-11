import json
from .audit import record_event
from .company_profiles import DEFAULT_COMPANY, company_profile, safe_company_path
from .ledger_backup import backup_snapshot
from .normalize import name_key


DATA_FIELDS = ("bank", "invoices", "batches", "allocations", "conflicts", "blocked_pairs", "migrations")


def correction_availability(service, ledger):
    if service.config.get("company_key") == DEFAULT_COMPANY:
        return {"allowed": False, "reason": "默认公司由配置绑定，此入口用于更正首次手动启用的公司名称"}
    if any(ledger[field] for field in DATA_FIELDS) or ledger["company"]["accounts"]:
        return {"allowed": False, "reason": "此公司已有导入数据或历史关联，不能按空账本更正名称"}
    sessions = safe_company_path(service.root, service.root / service.config["session_dir"])
    if any(path.is_file() for path in sessions.rglob("*")):
        return {"allowed": False, "reason": "此公司存在旧版历史文件，不能按空账本更正名称"}
    return {"allowed": True, "reason": "尚未导入数据，可以更正首次填写的完整公司名称"}


def correct_company_name(registry, payload):
    key, name = payload.get("company_key"), payload.get("legal_name")
    company_profile(registry.config, key)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200 or any(ord(c) < 32 for c in name):
        raise ValueError("请填写有效的完整公司名称（1–200 字，不含换行）")
    if payload.get("confirmed") is not True:
        raise ValueError("请先确认完整公司名称与银行户名、发票抬头一致")
    name = name.strip()
    with registry.registration_lock():
        service = registry.get(key)
        with service.lock:
            ledger, expected = service.current(payload)
            if (payload.get("company_id") != ledger["company"]["id"]
                    or payload.get("original_name") != ledger["company"]["name"]):
                raise ValueError("公司绑定信息已变化，请刷新后重新打开更正窗口")
            availability = correction_availability(service, ledger)
            if not availability["allowed"]:
                raise ValueError(availability["reason"])
            for other in registry.companies():
                if other.get("error"):
                    raise ValueError("有公司账本尚未通过校验，暂不能更正公司名称")
                if other["key"] != key and other["initialized"] and name_key(other["legal_name"]) == name_key(name):
                    raise ValueError("该完整名称已经绑定其他公司账本")
            if name == ledger["company"]["name"]:
                return {"changed": False, "backup": None}
            original = service.store.path.read_bytes()
            if json.loads(original) != ledger:
                raise ValueError("账本已更新，请刷新后重试")
            backup = backup_snapshot(registry.root, service.store, original)
            old_name = ledger["company"]["name"]
            ledger["company"]["name"] = name
            record_event(ledger, "correct_company_name", "更正首次填写的公司全称；确认账本尚未导入数据",
                         company_key=key, company_id=ledger["company"]["id"],
                         old_name=old_name, new_name=name, backup=backup)
            # 正式提交仍核对版本；其他进程抢先写入时拒绝本次更正，保留原账本。
            service.store.commit(ledger, expected)
            registry.invalidate(key)
            return {"changed": True, "backup": backup}
