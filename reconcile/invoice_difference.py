from collections import defaultdict
from copy import deepcopy
from .audit import record_event, timestamp


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


def difference_record(invoice, allocated, at=None):
    remaining = max(invoice["amount_cents"], 0) - allocated
    previous = invoice.get("difference")
    if not previous and not (allocated > 0 and remaining > 0):
        return None
    if not previous or remaining > previous["remaining_cents"]:
        return {"status": "pending", "note": REOPEN_NOTE if previous else AUTO_NOTE,
                "updated_at": at, "remaining_cents": remaining}
    return dict(previous, remaining_cents=remaining)


def synchronize_differences(ledger, audit=False):
    # 读取旧账本时只补齐内存视图；正常提交时才随账本一起保存。
    totals = invoice_totals(ledger)
    at = timestamp() if audit else ledger.get("saved_at")
    for invoice in ledger["invoices"]:
        previous = invoice.get("difference")
        current = difference_record(invoice, totals[invoice["id"]], at)
        if current is None:
            continue
        invoice["difference"] = current
        changed = (previous is None or current["status"] != previous["status"]
                   or current["note"] != previous["note"] or current["remaining_cents"] > previous["remaining_cents"])
        if audit and changed:
            record_event(ledger, "invoice_difference_auto", current["note"], invoice_id=invoice["id"],
                         previous=deepcopy(previous), current=deepcopy(current), remaining_cents=current["remaining_cents"])


def describe_difference(invoice, allocated, at=None):
    record = difference_record(invoice, allocated, at)
    remaining = max(invoice["amount_cents"], 0) - allocated
    status = "none" if record is None else "cleared" if remaining == 0 else record["status"]
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
    previous = difference_record(invoice, allocated, ledger.get("saved_at"))
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
