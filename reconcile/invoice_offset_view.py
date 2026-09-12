from collections import defaultdict
from .invoice_offset_model import net_capacity, offset_totals, blocking_invalid, source_offset_hold


def annotate_offsets(invoices, ledger):
    totals = offset_totals(ledger)
    indexed = {invoice["id"]: invoice for invoice in invoices}
    relations = defaultdict(list)
    for relation in ledger.get("invoice_offsets", []):
        relations[relation["red_invoice_id"]].append(relation)
        relations[relation["blue_invoice_id"]].append(relation)
    conflicts = {iid for c in ledger["conflicts"] if c["kind"] == "invoices" and c["state"] == "pending" for iid in c["record_ids"]}
    for invoice in invoices:
        active = [r for r in relations[invoice["id"]] if r["state"] == "active"]
        counterpart_ids = [r["blue_invoice_id"] if invoice["red"] else r["red_invoice_id"] for r in active]
        offset = totals[invoice["id"]]
        status = ("linked" if active else "reviewed" if invoice.get("exception_review") else "pending") if invoice["red"] else (
            "full" if offset and offset == invoice["amount_cents"] else "partial" if offset else "none")
        invoice.update(offset_cents=offset, net_amount_cents=net_capacity(invoice, totals), offset_status=status,
                       offset_relation_ids=[r["id"] for r in relations[invoice["id"]]],
                       offset_invoice_ids=counterpart_ids, offset_invoice_numbers=[indexed[iid]["number"] for iid in counterpart_ids],
                       offset_source_hold=source_offset_hold(invoice, offset), blocking_invalid=blocking_invalid(invoice),
                       offset_conflict=invoice["id"] in conflicts)


def annotate_payment_offsets(banks, invoices, allocations, labels):
    indexed = {i["id"]: i for i in invoices}
    linked = defaultdict(dict)
    for allocation in allocations:
        if indexed[allocation["invoice_id"]]["offset_cents"]:
            entries = linked[allocation["bank_id"]]
            iid = allocation["invoice_id"]
            if entries.get(iid) != "active":
                entries[iid] = allocation["state"]
    for bank in banks:
        notices = [dict(invoice_id=iid, number=indexed[iid]["number"], offset_cents=indexed[iid]["offset_cents"],
                        net_amount_cents=indexed[iid]["net_amount_cents"], status=indexed[iid]["offset_status"],
                        relation_source=linked[bank["id"]][iid], source_label="已关联发票" if linked[bank["id"]][iid] == "active" else "曾关联发票")
                   for iid in sorted(linked[bank["id"]])]
        bank["invoice_offset_notices"] = notices
        bank["invoice_offset_hint"] = "；".join(
            f"{n['source_label']} {n['number']}：{labels[n['status']]}，累计冲红 {n['offset_cents']/100:.2f} 元，净额 {n['net_amount_cents']/100:.2f} 元"
            for n in notices)
