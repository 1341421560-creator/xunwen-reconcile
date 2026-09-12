from copy import deepcopy
from .audit import identifier, timestamp, record_event
from .normalize import name_key
from .invoice_offset_model import offset_totals, net_capacity, blocking_invalid, source_offset_hold
from .invoice_difference import invoice_totals, synchronize_differences, describe_difference


def require_offset_note(payload):
    note = payload.get("note")
    if not isinstance(note, str) or not note.strip() or len(note.strip()) > 1000:
        raise ValueError("请填写 1–1000 字的冲红处理依据")
    return note.strip()


def checked_pair(ledger, payload):
    ids = [payload.get("red_invoice_id"), payload.get("blue_invoice_id")]
    if not all(isinstance(value, str) and value for value in ids):
        raise ValueError("请选择红票和对应原票")
    indexed = {i["id"]: i for i in ledger["invoices"]}
    if any(iid not in indexed for iid in ids):
        raise ValueError("发票不存在或不属于当前公司")
    red, blue = [indexed[iid] for iid in ids]
    if not red["red"] or red["amount_cents"] >= 0 or blue["red"] or blue["amount_cents"] <= 0:
        raise ValueError("必须选择负金额红票和正金额原蓝票")
    if red["company_id"] != blue["company_id"] or red["currency"] != blue["currency"] or not name_key(red["party"]) or name_key(red["party"]) != name_key(blue["party"]):
        raise ValueError("红蓝票必须属于同一账本、同一销方及同币种")
    if blocking_invalid(red) or blocking_invalid(blue):
        raise ValueError("发票存在作废、失控或其他异常，不能冲红")
    if any(c["state"] == "pending" and c["kind"] == "invoices" and set(c["record_ids"]) & set(ids) for c in ledger["conflicts"]):
        raise ValueError("请先核对红票或原票的来源版本冲突")
    if red.get("exception_review"):
        raise ValueError("请先撤回该红票的原异常复核结论，再确认对应原票")
    if any(r["state"] == "active" and r["red_invoice_id"] == red["id"] for r in ledger.get("invoice_offsets", [])):
        raise ValueError("这张红票已对应原票，请先撤回原红冲关系")
    return red, blue


def preview_offset(ledger, payload):
    red, blue = checked_pair(ledger, payload)
    offsets, payments = offset_totals(ledger), invoice_totals(ledger)
    before = net_capacity(blue, offsets)
    amount, allocated = -red["amount_cents"], payments[blue["id"]]
    after = before - amount
    if after < 0:
        raise ValueError("本张红票金额超过原票尚可冲红金额")
    banks = {b["id"]: b for b in ledger["bank"]}
    relations = [dict(a, bank_date=banks[a["bank_id"]]["date"], bank_party=banks[a["bank_id"]]["party"],
                      bank_reference=banks[a["bank_id"]].get("reference", "")) for a in ledger["allocations"]
                 if a["state"] == "active" and a["invoice_id"] == blue["id"]]
    difference = describe_difference(blue, allocated, ledger.get("saved_at"), capacity=after)
    return dict(revision=ledger["revision"], red_invoice_id=red["id"], blue_invoice_id=blue["id"],
                red_number=red["number"], blue_number=blue["number"], amount_cents=amount,
                face_cents=blue["amount_cents"], previous_offset_cents=offsets[blue["id"]],
                previous_net_cents=before, net_amount_cents=after, allocated_cents=allocated,
                remaining_cents=after-allocated, excess_cents=max(allocated-after, 0), can_save=allocated <= after,
                allocations=relations, source_hold=source_offset_hold(blue, offsets[blue["id"]]+amount),
                difference_status="offset_cleared" if after == allocated and before > allocated and blue.get("difference") else difference["difference_status"])


def offset_snapshot(ledger, blue):
    capacity = net_capacity(blue, offset_totals(ledger))
    allocated = invoice_totals(ledger)[blue["id"]]
    difference = describe_difference(blue, allocated, ledger.get("saved_at"), capacity=capacity)
    return dict(net_amount_cents=capacity, allocated_cents=allocated, remaining_cents=capacity-allocated,
                difference_status=difference["difference_status"], difference=deepcopy(blue.get("difference")))


def save_offset(ledger, payload):
    note = require_offset_note(payload)
    preview = preview_offset(ledger, payload)
    if not preview["can_save"]:
        raise ValueError(f"冲红后已关联金额超出 {preview['excess_cents']/100:.2f} 元，请先撤回足够的付款关联")
    red, blue = checked_pair(ledger, payload)
    previous = offset_snapshot(ledger, blue)
    relation = dict(id=identifier("O"), red_invoice_id=red["id"], blue_invoice_id=blue["id"],
                    amount_cents=preview["amount_cents"], state="active", note=note, at=timestamp(),
                    revision=ledger["revision"]+1)
    ledger.setdefault("invoice_offsets", []).append(relation)
    ledger["schema_version"] = 2
    synchronize_differences(ledger, audit=True, cause="offset")
    record_event(ledger, "invoice_offset", note, offset_id=relation["id"], invoice_ids=[red["id"], blue["id"]],
                 previous=previous, current=offset_snapshot(ledger, blue))


def undo_offset(ledger, payload):
    note = require_offset_note(payload)
    relation = next((r for r in ledger.get("invoice_offsets", []) if r["id"] == payload.get("offset_id")), None)
    if not relation or relation["state"] != "active":
        raise ValueError("有效红冲关系不存在或已撤回")
    blue = next(i for i in ledger["invoices"] if i["id"] == relation["blue_invoice_id"])
    previous = offset_snapshot(ledger, blue)
    relation.update(state="revoked", revoked_at=timestamp(), revoke_note=note)
    synchronize_differences(ledger, audit=True, cause="offset_undo")
    record_event(ledger, "invoice_offset_undo", note, offset_id=relation["id"],
                 invoice_ids=[relation["red_invoice_id"], blue["id"]], previous=previous, current=offset_snapshot(ledger, blue))
