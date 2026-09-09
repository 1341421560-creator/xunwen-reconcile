from copy import deepcopy
from .audit import timestamp, record_event
from .allocations import require_note
from .invoice_selection import preserve_invoice_selection


def accepted_version(conflict):
    previous = conflict.get("previous_record")
    return conflict.get("resolved_record_version", previous.get("record_version", 0) + 1 if previous else None)


def require_unallocated(ledger, kind, record_ids):
    field = "bank_id" if kind == "bank" else "invoice_id"
    if any(a["state"] == "active" and a[field] in record_ids for a in ledger["allocations"]):
        raise ValueError("撤回该核对结论前，请先撤回受影响记录的全部有效金额关联")


def reverse_conflict(ledger, payload):
    note = require_note(payload)
    conflict = next((c for c in ledger["conflicts"] if c["id"] == payload.get("conflict_id")), None)
    if not conflict or conflict["state"] != "resolved":
        raise ValueError("该冲突没有可撤回的已保存核对结论")
    resolution = conflict.get("resolution")
    history = {"at": timestamp(), "note": note, "resolution": resolution,
               "previous_note": conflict.get("note", ""), "previous_resolved_at": conflict.get("resolved_at", "")}
    if resolution == "accept":
        require_unallocated(ledger, conflict["kind"], conflict["record_ids"])
        old = conflict.get("previous_record")
        current = next(r for r in ledger[conflict["kind"]] if r["id"] == conflict["record_ids"][0])
        expected_version = accepted_version(conflict)
        if not old or current.get("record_version", 0) != expected_version:
            raise ValueError("该记录在本次核对后又接受过其他版本，请先撤回后续版本核对")
        history["replaced_record"] = deepcopy(current)
        restored = deepcopy(old)
        restored["sources"] = deepcopy(current["sources"])
        restored["record_version"] = current.get("record_version", 0) + 1
        if conflict["kind"] == "bank":
            for field in ("manual_note", "manual_note_updated_at", "expense_classification"):
                if field in current:
                    restored[field] = deepcopy(current[field])
        else:
            preserve_invoice_selection(current, restored)
        current.clear()
        current.update(restored)
        # 后续版本已撤回时，使前一项决定仍可按倒序撤回。
        prior = [c for c in ledger["conflicts"] if c is not conflict and c["state"] == "resolved" and c.get("resolution") == "accept"
                 and c["kind"] == conflict["kind"] and c["record_ids"] == conflict["record_ids"]
                 and accepted_version(c) == old.get("record_version", 0)]
        for previous in prior:
            previous["resolved_record_version"] = restored["record_version"]
    elif resolution == "new":
        rid = conflict.get("accepted_record_id")
        current = next((r for r in ledger["bank"] if r["id"] == rid), None)
        if not current:
            raise ValueError("新增流水不存在，无法撤回该核对结论")
        require_unallocated(ledger, "bank", [rid])
        if any(c is not conflict and rid in c["record_ids"] for c in ledger["conflicts"]):
            raise ValueError("这笔新增流水已被其他来源冲突引用，需先核对相关冲突，暂不能撤回新增入账")
        # 已撤回关联与新增流水一并存入冲突历史，避免重复计入支出，也不丢失证据。
        history["withdrawn_record"] = deepcopy(current)
        history["withdrawn_allocations"] = [deepcopy(a) for a in ledger["allocations"] if a["bank_id"] == rid]
        history["withdrawn_pairs"] = [deepcopy(pair) for pair in ledger["blocked_pairs"] if pair[0] == rid]
        ledger["bank"] = [r for r in ledger["bank"] if r["id"] != rid]
        ledger["allocations"] = [a for a in ledger["allocations"] if a["bank_id"] != rid]
        ledger["blocked_pairs"] = [pair for pair in ledger["blocked_pairs"] if pair[0] != rid]
    elif resolution != "keep":
        raise ValueError("该核对类型不支持撤回")
    conflict.setdefault("reversal_history", []).append(history)
    conflict.update(state="pending", resolution="", note="", resolved_at="")
    record_event(ledger, "reverse_conflict", note, conflict_id=conflict["id"], previous=deepcopy(history))


def reverse_exception(ledger, payload):
    note = require_note(payload)
    invoice = next((i for i in ledger["invoices"] if i["id"] == payload.get("invoice_id")), None)
    if invoice is None or not invoice.get("exception_review"):
        raise ValueError("该异常票没有可撤回的复核结论")
    previous = invoice["exception_review"]
    invoice.setdefault("exception_review_history", []).append({"previous": deepcopy(previous), "at": timestamp(), "note": note})
    invoice["exception_review"] = None
    record_event(ledger, "reverse_exception", note, invoice_id=invoice["id"], previous=deepcopy(previous))
