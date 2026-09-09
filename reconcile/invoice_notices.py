from collections import defaultdict
from decimal import Decimal


def money(cents):
    return format(Decimal(cents).scaleb(-2), ",.2f")


def notice(invoice, source, labels, revoked=None):
    result = {
        "invoice_id": invoice["id"], "invoice_number": invoice["number"],
        "invoice_date": invoice["date"], "party": invoice["party"],
        "amount_cents": invoice["amount_cents"], "remaining_cents": invoice["remaining_cents"],
        "difference_status": invoice["difference_status"], "relation_source": source,
        "source_label": labels["invoice_notice_sources"][source],
        "difference_label": labels["difference_statuses"][invoice["difference_status"]],
        "revoked_at": revoked.get("revoked_at", "") if revoked else "",
        "revoke_note": revoked.get("revoke_note", "") if revoked else "",
    }
    result["summary"] = f"{result['source_label']}：{money(result['amount_cents'])} 元，{result['difference_label']}，暂停分配。"
    return result


def notice_text(item, bank):
    parts = [item["summary"], f"销方：{item['party']}；发票：{item['invoice_number']}；开票日期：{item['invoice_date']}。"]
    if item["relation_source"] == "revoked":
        parts.append(f"原关联已撤回，依据：{item['revoke_note'] or '未记录'}；撤回时间：{item['revoked_at'] or '未记录'}。")
    elif item["relation_source"] == "same_party":
        parts.append("仅为同公司发票，尚未确认与本笔付款的对应关系。")
    parts.append(f"付款未核销：{money(bank['remaining_cents'])} 元；发票未分配余额：{money(item['remaining_cents'])} 元（勿跨流水重复汇总）。")
    return " ".join(parts)


def annotate_invoice_notices(banks, invoices, allocations, audit, config):
    # 只补充账本视图；发票分配资格、付款状态和原始关联均由现有业务模块决定。
    restricted = {invoice["id"]: invoice for invoice in invoices if invoice["difference_blocked"]}
    by_party = defaultdict(list)
    for invoice in restricted.values():
        if invoice["party_match_key"]:
            by_party[(invoice["party_match_key"], invoice["currency"])].append(invoice["id"])
    active, revoked = defaultdict(dict), defaultdict(dict)
    revoke_order = {aid: index for index, event in enumerate(audit) if event["action"] == "revoke" for aid in event.get("allocation_ids", [])}
    for index, allocation in enumerate(allocations):
        iid, bid = allocation["invoice_id"], allocation["bank_id"]
        if iid not in restricted:
            continue
        if allocation["state"] == "active":
            active[bid][iid] = None
        elif allocation["state"] == "revoked":
            order = (allocation.get("revoked_at", ""), revoke_order.get(allocation["id"], index))
            previous = revoked[bid].get(iid)
            if previous is None or order >= previous[0]:
                revoked[bid][iid] = (order, allocation)
    for bank in banks:
        bid, chosen = bank["id"], {}
        for iid in active.get(bid, {}):
            chosen[iid] = notice(restricted[iid], "active", config)
        for iid, (_, allocation) in revoked.get(bid, {}).items():
            if iid not in chosen:
                chosen[iid] = notice(restricted[iid], "revoked", config, allocation)
        if bank["direction"] == "支出" and bank["remaining_cents"] > 0 and not bank["excluded_reason"]:
            for iid in by_party.get((bank["party_match_key"], bank["currency"]), []):
                if iid not in chosen:
                    chosen[iid] = notice(restricted[iid], "same_party", config)
        bank["related_invoice_notices"] = list(chosen.values())
        bank["related_invoice_hint"] = "\n".join(notice_text(item, bank) for item in chosen.values())
        if chosen and bank["status"] == "unmatched":
            bank["reason"] = config["invoice_notice_blocked_reason"]
