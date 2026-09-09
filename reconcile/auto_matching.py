from collections import defaultdict
from .audit import identifier, timestamp, record_event
from .ledger_model import party_key
from .ledger_progress import progress


def auto_match(ledger, config):
    banks, invoices = progress(ledger, config)
    bg, ig = defaultdict(list), defaultdict(list)
    for b in banks:
        if b["remaining_cents"] > 0 and not b["hold_reasons"] and not b["excluded_reason"] and b["party"]:
            bg[(party_key(b, ledger["settings"], True), b["currency"], b["remaining_cents"])].append(b)
    for i in invoices:
        if i["remaining_cents"] > 0 and not i["hold_reasons"] and i["status"] != "review":
            ig[(party_key(i, ledger["settings"]), i["currency"], i["remaining_cents"])].append(i)
    blocked = {tuple(pair) for pair in ledger["blocked_pairs"]}
    created = []
    for key, bs in bg.items():
        ins = ig.get(key, [])
        if len(bs) != 1 or len(ins) != 1:
            continue
        b, i = bs[0], ins[0]
        if (b["id"], i["id"]) in blocked:
            continue
        a = {"id": identifier("A"), "bank_id": b["id"], "invoice_id": i["id"], "amount_cents": b["remaining_cents"],
             "state": "active", "kind": "auto", "at": timestamp(), "note": "同名或已确认名称映射、同币种、剩余金额相等且双方唯一对应", "revision": ledger["revision"] + 1}
        ledger["allocations"].append(a)
        created.append(a)
    if created:
        record_event(ledger, "auto_match", "跨月累计自动匹配", allocation_ids=[a["id"] for a in created])
    return created
