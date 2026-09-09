from copy import deepcopy
from pathlib import Path
import re


DEFAULT_COMPANY = "moderate"


def company_profiles(config):
    return config["companies"]


def company_profile(config, key):
    if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", key):
        raise ValueError("请选择有效的公司账本")
    item = next((item for item in company_profiles(config) if item["key"] == key), None)
    if item is None:
        raise ValueError("公司账本不存在")
    return item


def data_root(root, key):
    return Path(root) if key == DEFAULT_COMPANY else Path(root) / "company-data" / key


def scoped_config(config, key, legal_name):
    company_profile(config, key)
    scoped = deepcopy(config)
    scoped.update(company_key=key, company_name=legal_name, report_url_prefix=f"/reports/{key}/")
    if key != DEFAULT_COMPANY:
        for field, folder in (("ledger_dir", "ledger"), ("session_dir", "sessions"), ("report_dir", "reports")):
            scoped[field] = f"company-data/{key}/{folder}"
    scoped["temp_dir"] = str(Path(config["temp_dir"]) / "companies" / key)
    return scoped


def safe_company_path(root, path):
    root, path = Path(root).resolve(), Path(path)
    if not path.resolve().is_relative_to(root):
        raise ValueError("公司数据目录超出软件目录")
    for item in (path, *path.parents):
        if item == root:
            break
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("公司数据目录不能包含符号链接或联接点")
    return path
