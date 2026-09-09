from copy import deepcopy
from .audit import timestamp, record_event
from .allocations import require_note
from .identity_dedup import add_record


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
        field = "bank_id" if conflict["kind"] == "bank" else "invoice_id"
        if any(a["state"] == "active" and a[field] in conflict["record_ids"] for a in ledger["allocations"]):
            raise ValueError("替换记录前必须先撤回受影响记录的全部关联")
        old = records[conflict["record_ids"][0]]
        conflict["previous_record"] = deepcopy(old)
        replacement = deepcopy(conflict["incoming"])
        replacement.update({k: old[k] for k in ("id", "company_id", "first_batch_id", "created_at")})
        replacement["sources"] = old["sources"] + conflict["sources"]
        replacement["record_version"] = old.get("record_version", 0) + 1
        old.clear()
        old.update(replacement)
    elif action == "new" and conflict["type"] == "missing_reference":
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
    if not invoice or not (invoice.get("red") or invoice.get("invalid")):
        raise ValueError("该发票没有可单独复核的红字或异常标记")
    if any(a["state"] == "active" and a["invoice_id"] == invoice["id"] for a in ledger["allocations"]):
        raise ValueError("请先撤回这张异常发票的全部关联")
    invoice["exception_review"] = {"at": timestamp(), "note": note}
    record_event(ledger, "review_exception", note, invoice_id=invoice["id"])
