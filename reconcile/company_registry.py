import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock
from .audit import record_event
from .company_profiles import DEFAULT_COMPANY, company_profile, company_profiles, scoped_config, data_root, safe_company_path
from .ledger_model import new_ledger
from .ledger_validation import validate_ledger, LedgerCorruptionError
from .normalize import name_key
from .resource_lock import resource_mutex
from .service import ReconciliationService


class CompanyRegistry:
    def __init__(self, root, config, default_service=None):
        self.root, self.config = Path(root).resolve(), deepcopy(config)
        reserved = self.root / "company-data"
        directories = []
        for field in ("ledger_dir", "session_dir", "report_dir"):
            directory = safe_company_path(self.root, self.root / self.config[field]).resolve()
            if directory.is_relative_to(reserved) or reserved.is_relative_to(directory):
                raise ValueError("原公司数据目录不能与多公司目录重叠")
            if any(directory.is_relative_to(other) or other.is_relative_to(directory) for other in directories):
                raise ValueError("账本、历史与导出目录必须分别独立")
            directories.append(directory)
        self._services = {DEFAULT_COMPANY: default_service} if default_service else {}
        self._guard = RLock()

    def ledger_path(self, key):
        company_profile(self.config, key)
        folder = self.config["ledger_dir"] if key == DEFAULT_COMPANY else f"company-data/{key}/ledger"
        return safe_company_path(self.root, self.root / folder / "company-ledger.v1.json")

    def describe(self, key):
        profile = company_profile(self.config, key)
        path = self.ledger_path(key)
        name = self.config["company_name"] if key == DEFAULT_COMPANY else ""
        if path.exists():
            try:
                ledger = json.loads(path.read_text(encoding="utf-8"))
                validate_ledger(ledger)
                bound = ledger["company"].get("key")
                if bound is not None and bound != key:
                    raise ValueError("账本绑定的公司标识与目录不符")
                if name and name_key(name) != name_key(ledger["company"]["name"]):
                    raise ValueError("原有摩德瑞特账本公司名称与配置不符")
                name = ledger["company"]["name"]
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise LedgerCorruptionError(f"{profile['label']}账本校验失败，未改写文件：{exc}") from exc
        return {**profile, "legal_name": name, "initialized": bool(name), "has_ledger": path.exists()}

    def companies(self):
        result = []
        for profile in company_profiles(self.config):
            try:
                result.append(self.describe(profile["key"]))
            except (OSError, ValueError) as exc:
                result.append({**profile, "legal_name": "", "initialized": False, "error": str(exc)})
        return result

    @contextmanager
    def registration_lock(self):
        with self._guard, resource_mutex(self.root / self.config["temp_dir"] / "company-registration"):
            yield

    def get(self, key):
        company_profile(self.config, key)
        with self._guard:
            if key not in self._services:
                profile = self.describe(key)
                if not profile["initialized"]:
                    raise ValueError("该公司尚未启用，请先填写并确认完整公司名称")
                self._services[key] = self.prepare(key, profile["legal_name"])
            return self._services[key]

    def prepare(self, key, name):
        config = scoped_config(self.config, key, name)
        for field in ("ledger_dir", "session_dir", "report_dir", "temp_dir"):
            safe_company_path(self.root, self.root / config[field])
        return ReconciliationService(self.root, config)

    def invalidate(self, key):
        with self._guard:
            self._services.pop(key, None)

    def activate(self, payload):
        key, name = payload.get("company_key"), payload.get("legal_name")
        company_profile(self.config, key)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200 or any(ord(c) < 32 for c in name):
            raise ValueError("请填写有效的完整公司名称（1–200 字，不含换行）")
        name = name.strip()
        with self.registration_lock():
            existing = self.describe(key)
            if existing["initialized"]:
                if name_key(existing["legal_name"]) != name_key(name):
                    raise ValueError("该公司已绑定完整名称，不能覆盖或更名")
                return self.get(key)
            for other in self.companies():
                if other.get("error"):
                    raise ValueError("有公司账本尚未通过校验，暂不能初始化新公司")
                if other["initialized"] and name_key(other["legal_name"]) == name_key(name):
                    raise ValueError("该完整名称已经绑定其他公司账本")
            target = safe_company_path(self.root, data_root(self.root, key))
            if any(file.is_file() for folder in ("ledger", "sessions", "reports") for file in (target / folder).rglob("*")):
                raise ValueError("该公司目录已有数据，请完成原备份恢复，不能另建空账本")
            service = self.prepare(key, name)
            ledger = new_ledger(name)
            ledger["company"]["key"] = key
            record_event(ledger, "activate_company", "首次确认公司全称并启用独立账本", company_key=key)
            service.store.commit(ledger, 0)
            self._services[key] = service
            return service
