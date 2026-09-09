from .audit import identifier, timestamp
from .normalize import name_key
from .ledger_validation import validate_ledger


def new_ledger(company_name):
    return {"schema_version": 1, "revision": 0,
            "company": {"id": identifier("C"), "name": company_name, "accounts": []},
            "created_at": timestamp(), "saved_at": None, "bank": [], "invoices": [],
            "batches": [], "allocations": [], "conflicts": [], "blocked_pairs": [],
            "audit": [], "migrations": [], "settings": {"exclude_special": True, "aliases": {}}}


def party_key(record, settings, bank=False):
    key = name_key(record["party"])
    if bank:
        aliases = {name_key(k): name_key(v) for k, v in settings.get("aliases", {}).items()}
        key = aliases.get(key, key)
    return key
