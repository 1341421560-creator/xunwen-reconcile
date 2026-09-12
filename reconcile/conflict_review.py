from copy import deepcopy
from .audit import timestamp, record_event
from .allocations import require_note
from .identity_dedup import add_record
from .invoice_selection import preserve_invoice_selection
from .invoice_offset_model import require_no_offsets, blocking_invalid


def resolve_conflict(ledger, payload):
    note = require_note(payload)
    conflict = next((c for c in ledger["conflicts"] if c["id"] == payload.get("conflict_id")), None)
    if not conflict or conflict["state"] != "pending":
        raise ValueError("待核对冲突不存在或已处理")
    action = payload.get("action")
    records = {r["id"]: r for r in ledger[conflict["kind"]]}
    if action == "keep":
        # 来源新版本完整保存在冲突记录中，人工选择保留旧记录不丢失证据。
        pass
    elif action == "accept" and conflict["type"] == "version":
        if conflict["kind"] == "invoices":
            require_no_offsets(ledger, conflict["record_ids"])
        field = "bank_id" if conflict["kind"] == "bank" else "invoice_id"
        if any(a["state"] == "active" and a[field] in conflict["record_ids"] for a in ledger["allocations"]):
            raise ValueError("替换记录前必须先撤回受影响记录的全部关联")
        old = records[conflict["record_ids"][0]]
        conflict["previous_record"] = deepcopy(old)
        replacement = deepcopy(conflict["incoming"])
        replacement.update({k: old[k] for k in ("id", "company_id", "first_batch_id", "created_at")})
        replacement["sources"] = old["sources"] + conflict["sources"]
        replacement["record_version"] = old.get("record_version", 0) + 1
        if conflict["kind"] == "bank":
            for field in ("manual_note", "manual_note_updated_at", "expense_classification"):
                if field in old:
                    replacement[field] = old[field]
        else:
            preserve_invoice_selection(old, replacement)
        old.clear()
        old.update(replacement)
        conflict["resolved_record_version"] = replacement["record_version"]
    elif action == "new" and conflict["type"] == "missing_reference":
        previous = next((h for h in reversed(conflict.get("reversal_history", [])) if h.get("withdrawn_record")), None)
        if previous:
            item = deepcopy(previous["withdrawn_record"])
            ledger["bank"].append(item)
            ledger["allocations"].extend(deepcopy(previous["withdrawn_allocations"]))
            ledger["blocked_pairs"].extend(deepcopy(previous["withdrawn_pairs"]))
        else:
            item = add_record(ledger, conflict["incoming"], conflict["kind"], conflict["batch_id"])
            item["sources"] = deepcopy(conflict["sources"])
        conflict["accepted_record_id"] = item["id"]
    else:
        raise ValueError("核对操作不适用于当前冲突")
    conflict.update(state="resolved", resolution=action, note=note, resolved_at=timestamp())
    record_event(ledger, "resolve_conflict", note, conflict_id=conflict["id"], resolution=action)


def review_exception(ledger, payload):
    note = require_note(payload)
    invoice = next((i for i in ledger["invoices"] if i["id"] == payload.get("invoice_id")), None)
    if not invoice or not (invoice.get("red") or blocking_invalid(invoice)):
        raise ValueError("该发票没有可单独复核的红字或异常标记")
    require_no_offsets(ledger, [invoice["id"]])
    if any(a["state"] == "active" and a["invoice_id"] == invoice["id"] for a in ledger["allocations"]):
        raise ValueError("请先撤回这张异常发票的全部关联")
    invoice["exception_review"] = {"at": timestamp(), "note": note}
    record_event(ledger, "review_exception", note, invoice_id=invoice["id"])
