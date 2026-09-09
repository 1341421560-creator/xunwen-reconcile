import io
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from reconcile.config import load_config
from reconcile.company_profiles import DEFAULT_COMPANY, company_profile, scoped_config
from reconcile.company_registry import CompanyRegistry
from reconcile.normalize import name_key
from reconcile.atomic_write import replace_snapshot
from reconcile.resource_lock import resource_mutex
from .archive_validation import digest
from .company_archive import encode_single, encode_zip, read_companies
from .company_snapshot import collect, preflight, stage_content, publish, existing_files


def selected_keys(config, company_key, all_companies):
    if all_companies and company_key is not None:
        raise ValueError("请选择单家公司或全部公司，不能同时指定")
    if all_companies:
        return [profile["key"] for profile in config["companies"]]
    key = company_key or DEFAULT_COMPANY
    company_profile(config, key)
    return [key]


def empty_target(registry, key, name):
    return SimpleNamespace(root=registry.root, config=scoped_config(registry.config, key, name))


def backup_companies(root, destination, company_key=None, all_companies=False):
    registry = CompanyRegistry(root, load_config(root))
    keys = selected_keys(registry.config, company_key, all_companies)
    records, archives, summaries = [], {}, []
    with registry.registration_lock(), ExitStack() as locks:
        services = {}
        for key in keys:
            profile = registry.describe(key)
            if profile["initialized"]:
                services[key] = registry.get(key)
                locks.enter_context(services[key].store.exclusive())
        for key in keys:
            profile = registry.describe(key)
            record = {field: profile[field] for field in ("key", "legal_name", "initialized", "has_ledger")}
            record["revision"] = None
            if not profile["has_ledger"] and existing_files(empty_target(registry, key, profile["legal_name"])):
                raise ValueError("该公司尚无正式账本但已有历史或未完成恢复的数据，请先完成迁移或恢复：" + key)
            if profile["has_ledger"]:
                ledger, content = collect(services[key])
                value = encode_single(key, ledger, content)
                filename = f"companies/{key}.zip"
                archives[filename] = value
                record.update(archive=filename, size=len(value), sha256=digest(value), revision=ledger["revision"])
                summaries.append({"company_key": key, "company_name": profile["legal_name"], "revision": ledger["revision"],
                                  "files": len(content), "bank_count": len(ledger["bank"]), "invoice_count": len(ledger["invoices"])})
            records.append(record)
    if not all_companies and not archives:
        raise ValueError("该公司还没有正式账本，无需备份")
    value = encode_zip({"schema_version": 2, "kind": "xunwen-companies-transfer", "created_at": datetime.now().astimezone().isoformat(),
                        "companies": records}, archives) if all_companies else next(iter(archives.values()))
    read_companies(io.BytesIO(value), registry.config)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / ("循文账本迁移包-" + ("全部公司" if all_companies else keys[0]) + "-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8] + ".zip")
    with path.open("xb") as stream:
        stream.write(value)
    read_companies(path, registry.config)
    result = {"archive": str(path), "sha256": digest(value), "scope": "all" if all_companies else keys[0], "companies": records}
    if len(summaries) == 1 and not all_companies:
        result.update(summaries[0])
    return result


def restore_companies(root, archive, company_key=None, all_companies=False, publisher=replace_snapshot):
    registry = CompanyRegistry(root, load_config(root))
    records = read_companies(archive, registry.config)
    requested = selected_keys(registry.config, company_key, all_companies)
    indexed = {record["key"]: record for record in records}
    keys = [key for key in requested if key in indexed] if all_companies else requested
    if any(key not in indexed for key in keys):
        raise ValueError("迁移包中没有所选公司的账本，不能跨公司恢复")
    completed, services, stages = [], {}, {}
    with registry.registration_lock(), ExitStack() as locks:
        names = {}
        for profile in registry.companies():
            if profile.get("error"):
                raise ValueError("现有公司账本校验失败，已停止全部恢复：" + profile["error"])
            if profile["initialized"]:
                names[profile["key"]] = name_key(profile["legal_name"])
        for key in keys:
            record = indexed[key]
            if record["initialized"]:
                full_name = name_key(record["legal_name"])
                if key in names and names[key] != full_name:
                    raise ValueError("恢复公司名称与已绑定名称不一致：" + key)
                if any(other != key and name == full_name for other, name in names.items()):
                    raise ValueError("恢复名称已经绑定其他公司，不能跨公司恢复")
                names[key] = full_name
            if record["has_ledger"]:
                service = registry.prepare(key, record["legal_name"])
                services[key] = service
                locks.enter_context(service.store.exclusive())
            else:
                locks.enter_context(resource_mutex(registry.ledger_path(key)))
                if existing_files(empty_target(registry, key, record.get("legal_name", ""))):
                    raise ValueError("目标已有数据，不能用未启用或空公司记录覆盖：" + key)
        # 所有公司和文件预检通过后才开始暂存与发布。
        for key, service in services.items():
            preflight(service, indexed[key]["content"])
        try:
            for key, service in services.items():
                stages[key] = stage_content(service, indexed[key]["content"])
            for key, service in services.items():
                ledger = publish(service, indexed[key]["content"], stages[key], publisher)
                completed.append({"company_key": key, "company_name": ledger["company"]["name"], "revision": ledger["revision"],
                                  "files": len(indexed[key]["content"]), "bank_count": len(ledger["bank"]), "invoice_count": len(ledger["invoices"])})
        except OSError as exc:
            done = "、".join(record["company_key"] for record in completed) or "无"
            raise OSError(f"恢复未完成，已完成公司：{done}。请保留暂存数据并重试同一备份。原因：{exc}") from exc
    result = {"restored": True, "scope": "all" if all_companies else keys[0], "companies": completed,
              "without_ledger": [key for key in keys if not indexed[key]["has_ledger"]]}
    if not all_companies and completed:
        result.update(completed[0])
    return result
