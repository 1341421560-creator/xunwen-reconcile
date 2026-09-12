from collections import defaultdict
from copy import deepcopy
from .audit import record_event, timestamp
from .invoice_offset_model import net_capacity, offset_totals


EDITABLE_STATUSES = ("pending", "carry_forward", "amount_error")
BLOCKED_STATUSES = ("pending", "amount_error")
AUTO_NOTE = "自动标注：发票部分关联后仍有余额，差额原因待确认。"
REOPEN_NOTE = "关联撤回后未分配余额增加，需重新确认差额原因。"


def invoice_totals(ledger):
    totals = defaultdict(int)
    for allocation in ledger["allocations"]:
        if allocation["state"] == "active":
            totals[allocation["invoice_id"]] += allocation["amount_cents"]
    return totals


def difference_record(invoice, allocated, at=None, capacity=None, cause=None):
    remaining = (net_capacity(invoice) if capacity is None else capacity) - allocated
    previous = invoice.get("difference")
    if not previous and not (allocated > 0 and remaining > 0):
        return None
    if not previous or remaining > previous["remaining_cents"]:
        note = "撤回冲红后未分配余额增加，需重新确认差额原因。" if cause == "offset_undo" else REOPEN_NOTE
        return {"status": "pending", "note": note if previous else AUTO_NOTE,
                "updated_at": at, "remaining_cents": remaining}
    current = dict(previous, remaining_cents=remaining)
    if cause == "offset" and remaining == 0 and previous["remaining_cents"] > 0:
        current.update(settled_by="offset", updated_at=at)
    return current


def synchronize_differences(ledger, audit=False, cause=None):
    # 读取旧账本时只补齐内存视图；正常提交时才随账本一起保存。
    totals = invoice_totals(ledger)
    offsets = offset_totals(ledger)
    at = timestamp() if audit else ledger.get("saved_at")
    for invoice in ledger["invoices"]:
        previous = invoice.get("difference")
        current = difference_record(invoice, totals[invoice["id"]], at, net_capacity(invoice, offsets), cause)
        if current is None:
            continue
        invoice["difference"] = current
        changed = (previous is None or current["status"] != previous["status"]
                   or current["note"] != previous["note"] or current["remaining_cents"] > previous["remaining_cents"]
                   or current.get("settled_by") != previous.get("settled_by"))
        if audit and changed:
            record_event(ledger, "invoice_difference_auto", current["note"], invoice_id=invoice["id"],
                         previous=deepcopy(previous), current=deepcopy(current), remaining_cents=current["remaining_cents"])


def describe_difference(invoice, allocated, at=None, capacity=None):
    record = difference_record(invoice, allocated, at, capacity)
    remaining = (net_capacity(invoice) if capacity is None else capacity) - allocated
    status = "none" if record is None else "cleared" if remaining == 0 else record["status"]
    if status == "cleared" and record.get("settled_by") == "offset":
        status = "offset_cleared"
    blocked = status in BLOCKED_STATUSES
    return {"difference_status": status, "difference_note": record["note"] if record else "",
            "difference_updated_at": record["updated_at"] if record else None,
            "difference_blocked": blocked, "difference_cents": remaining if record and remaining > 0 else 0,
            "distributable_cents": 0 if blocked or invoice.get("hold_reasons") else max(remaining, 0)}


def update_difference(ledger, payload):
    status, note = payload.get("status"), payload.get("note")
    if not isinstance(status, str) or status not in EDITABLE_STATUSES:
        raise ValueError("请选择有效的差额状态")
    if not isinstance(note, str) or not note.strip() or len(note.strip()) > 1000:
        raise ValueError("请填写 1–1000 字的差额处理依据")
    invoice = next((i for i in ledger["invoices"] if i["id"] == payload.get("invoice_id")), None)
    if invoice is None:
        raise ValueError("发票编号不存在")
    allocated = invoice_totals(ledger)[invoice["id"]]
    previous = difference_record(invoice, allocated, ledger.get("saved_at"), net_capacity(invoice, offset_totals(ledger)))
    if previous is None or previous["remaining_cents"] <= 0:
        raise ValueError("这张发票没有需要处理的剩余差额")
    current = {"status": status, "note": note.strip(), "updated_at": timestamp(),
               "remaining_cents": previous["remaining_cents"]}
    invoice["difference"] = current
    record_event(ledger, "invoice_difference", current["note"], invoice_id=invoice["id"],
                 previous=deepcopy(previous), current=deepcopy(current), remaining_cents=current["remaining_cents"])


def validate_difference(record):
    if not isinstance(record, dict) or not isinstance(record.get("status"), str) or record["status"] not in EDITABLE_STATUSES:
        raise ValueError("发票差额状态无效")
    if not isinstance(record.get("note"), str) or not record["note"].strip() or len(record["note"]) > 1000:
        raise ValueError("发票差额依据无效")
    if record.get("updated_at") is not None and not isinstance(record["updated_at"], str):
        raise ValueError("发票差额更新时间无效")
    if type(record.get("remaining_cents")) is not int or record["remaining_cents"] < 0:
        raise ValueError("发票差额余额无效")
    if "settled_by" in record and record["settled_by"] != "offset":
        raise ValueError("发票差额结清来源无效")
