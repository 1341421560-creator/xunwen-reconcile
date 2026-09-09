from collections import defaultdict
from .audit import identifier, timestamp, record_event
from .ledger_progress import progress
from .ledger_model import validate_ledger
from .input_validation import boolean_field


def require_note(payload):
    note = payload.get("note", "")
    if not isinstance(note, str) or not note.strip():
        raise ValueError("请填写本次核对依据")
    return note.strip()


def allocate(ledger, payload, config):
    note = require_note(payload)
    rows = payload.get("allocations")
    if not isinstance(rows, list) or not rows:
        raise ValueError("请至少填写一条付款、发票与本次分配金额")
    banks, invoices = progress(ledger, config)
    bm, im = {r["id"]: r for r in banks}, {r["id"]: r for r in invoices}
    bt, it = defaultdict(int), defaultdict(int)
    pairs = set()
    for a in rows:
        if not isinstance(a, dict):
            raise ValueError("关联明细格式错误")
        b, i, amount = bm.get(a.get("bank_id")), im.get(a.get("invoice_id")), a.get("amount_cents")
        if not b or not i:
            raise ValueError("付款或发票编号不存在")
        if type(amount) is not int or amount <= 0:
            raise ValueError("本次分配金额必须是大于零的整数分")
        if b["direction"] != "支出" or b["excluded_reason"]:
            raise ValueError("该流水不参与比对，请先调整分类")
        if b["hold_reasons"] or i["hold_reasons"] or i["status"] == "review":
            raise ValueError("存在红字、异常或冲突待复核，须先核对；已有关系需先撤回再重新分配")
        if b["currency"] != i["currency"]:
            raise ValueError("币种不同，不能分配")
        pair = (b["id"], i["id"])
        if pair in pairs:
            raise ValueError("同一付款与发票在本次明细重复填写，请合为一条分配金额")
        pairs.add(pair)
        bt[b["id"]] += amount
        it[i["id"]] += amount
    if any(v > bm[k]["remaining_cents"] for k, v in bt.items()):
        raise ValueError("本次分配合计超过付款剩余金额")
    if any(v > im[k]["remaining_cents"] for k, v in it.items()):
        raise ValueError("本次分配合计超过发票可用余额")
    ids = []
    for row in rows:
        a = {"id": identifier("A"), **{k: row[k] for k in ("bank_id", "invoice_id", "amount_cents")},
             "state": "active", "kind": "manual", "at": timestamp(), "note": note, "revision": ledger["revision"] + 1}
        ledger["allocations"].append(a)
        ids.append(a["id"])
        ledger["blocked_pairs"] = [p for p in ledger["blocked_pairs"] if p != [a["bank_id"], a["invoice_id"]]]
    validate_ledger(ledger)
    record_event(ledger, "allocate", note, allocation_ids=ids)


def revoke(ledger, payload):
    note = require_note(payload)
    a = next((a for a in ledger["allocations"] if a["id"] == payload.get("allocation_id")), None)
    if not a or a["state"] != "active":
        raise ValueError("该关联不存在或已经撤回")
    a.update(state="revoked", revoked_at=timestamp(), revoke_note=note)
    pair = [a["bank_id"], a["invoice_id"]]
    if pair not in ledger["blocked_pairs"]:
        ledger["blocked_pairs"].append(pair)
    record_event(ledger, "revoke", note, allocation_ids=[a["id"]])


def set_exclusion(ledger, payload):
    note = require_note(payload)
    excluded = boolean_field(payload, "excluded", True)
    bank = next((b for b in ledger["bank"] if b["id"] == payload.get("bank_id")), None)
    if not bank:
        raise ValueError("流水不存在")
    if any(a["state"] == "active" and a["bank_id"] == bank["id"] for a in ledger["allocations"]):
        raise ValueError("请先撤回已有金额关联，再调整分类")
    bank["manual_exclusion"] = note if excluded else ""
    record_event(ledger, "classification", note, bank_id=bank["id"], excluded=bool(bank["manual_exclusion"]))
