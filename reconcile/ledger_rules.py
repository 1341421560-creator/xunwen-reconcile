from .normalize import name_key
from .audit import record_event
from .input_validation import boolean_field
from .summary_exclusions import normalize_keywords


def update_rules(ledger, payload):
    custom_keywords = normalize_keywords(payload.get("custom_exclude_keywords", ledger["settings"].get("custom_exclude_keywords", [])))
    exclude_special = boolean_field(payload, "exclude_special", True)
    aliases = payload.get("aliases", {})
    if not isinstance(aliases, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not k.strip() or not v.strip() for k, v in aliases.items()):
        raise ValueError("名称映射必须填写完整名称")
    normalized = {}
    for k, v in aliases.items():
        nk, nv = name_key(k), name_key(v)
        if nk in normalized and normalized[nk] != nv:
            raise ValueError("同一银行名称不能映射到多个销方")
        normalized[nk] = nv
    if any(v in normalized and normalized[v] != v for v in normalized.values()):
        raise ValueError("请直接映射到最终销方名称，不使用链式或循环映射")
    old = ledger["settings"]
    ledger["settings"] = {"aliases": aliases, "exclude_special": exclude_special,
                          "custom_exclude_keywords": custom_keywords}
    record_event(ledger, "settings", "更新未关联记录的分类与后续匹配规则；既有关联保留", previous=old, current=ledger["settings"])
