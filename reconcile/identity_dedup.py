import hashlib
import json
from copy import deepcopy
from .invoice_offset_model import blocking_invalid
from .audit import identifier, timestamp
from .normalize import name_key


def identity(row, kind):
    if kind == "bank":
        return (row.get("account", ""), row.get("reference", "")) if row.get("reference") else None
    if row.get("number"):
        return (row.get("code", ""), row["number"])
    # 缺号异常票仅按同一原件的同一行识别重复，不把不同原件的疑似记录合并。
    return ("missing_number", row.get("file_hash"), row.get("sheet"), row.get("row"))


def signature(row, kind):
    fields = ("date", "party", "currency", "amount_cents", "direction", "debit_cents", "credit_cents", "summary", "type") if kind == "bank" else ("date", "party", "currency", "amount_cents", "red", "invalid", "source_status", "summary")
    return tuple(name_key(row.get(f, "")) if f == "party" else blocking_invalid(row) if f == "invalid" else row.get(f, "") for f in fields)


def source(row, batch_id):
    return {"batch_id": batch_id, "file": row.get("source", ""), "sha256": row.get("file_hash", ""),
            "sheet": row.get("sheet", ""), "row": row.get("row"), "sequence": row.get("source_sequence", "")}


def add_record(ledger, row, kind, batch_id, preserve_id=False):
    item = deepcopy(row)
    item["id"] = row["id"] if preserve_id else identifier("B" if kind == "bank" else "I")
    item.update(company_id=ledger["company"]["id"], first_batch_id=batch_id, sources=[source(row, batch_id)], created_at=timestamp())
    if kind == "bank":
        item["duplicate"] = False
        if item.get("account") and item["account"] not in ledger["company"]["accounts"]:
            ledger["company"]["accounts"].append(item["account"])
    ledger[kind].append(item)
    return item


def ingest(ledger, parsed, batch_id, preserve_ids=False):
    stats = {kind: {"new": 0, "duplicate": 0, "conflict": 0} for kind in ("bank", "invoices")}
    conflicts = {c["fingerprint"]: c for c in ledger["conflicts"]}
    for kind in ("bank", "invoices"):
        index = {identity(r, kind): r for r in ledger[kind] if identity(r, kind)}
        no_ref = {}
        if kind == "bank":
            for r in ledger[kind]:
                no_ref.setdefault((r.get("account"), signature(r, kind)), []).append(r)
        for row in parsed[kind]:
            key = identity(row, kind)
            existing = index.get(key) if key else None
            suspected = no_ref.get((row.get("account"), signature(row, kind)), []) if kind == "bank" and not key else []
            if existing and signature(existing, kind) == signature(row, kind):
                existing["sources"].append(source(row, batch_id))
                stats[kind]["duplicate"] += 1
                continue
            if existing or suspected:
                targets = [existing["id"]] if existing else [r["id"] for r in suspected]
                provenance_key = None if key else [row.get("file_hash"), row.get("sheet"), row.get("row")]
                baseline_version = existing.get("record_version", 0) if existing else None
                fingerprint = hashlib.sha256(json.dumps([kind, key, provenance_key, signature(row, kind), baseline_version], ensure_ascii=False).encode("utf-8")).hexdigest()
                conflict = conflicts.get(fingerprint)
                if conflict:
                    conflict["sources"].append(source(row, batch_id))
                    # 已核对的同一版本再次出现，仅补充来源，保留核对决定。
                    stats[kind]["conflict" if conflict["state"] == "pending" else "duplicate"] += 1
                    continue
                conflict = {"id": identifier("K"), "kind": kind, "record_ids": targets,
                            "type": "version" if existing else "missing_reference", "incoming": deepcopy(row),
                            "fingerprint": fingerprint, "state": "pending", "created_at": timestamp(),
                            "sources": [source(row, batch_id)], "batch_id": batch_id}
                ledger["conflicts"].append(conflict)
                conflicts[fingerprint] = conflict
                stats[kind]["conflict"] += 1
                continue
            item = add_record(ledger, row, kind, batch_id, preserve_ids)
            stats[kind]["new"] += 1
            if key:
                index[key] = item
            if kind == "bank":
                no_ref.setdefault((row.get("account"), signature(row, kind)), []).append(item)
    return stats
