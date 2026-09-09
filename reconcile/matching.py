from collections import defaultdict
from itertools import combinations
from .normalize import name_key


def party_key(record, aliases):
    key = name_key(record["party"])
    return aliases.get(key, key), record["currency"]


def build_result(bank, invoices, settings, config, decisions=None):
    aliases = {name_key(a): name_key(b) for a, b in settings.get("aliases", {}).items() if name_key(a) and name_key(b)}
    bank = [dict(r, status="unmatched", reason="在本次导入的发票中未找到对应记录", invoice_ids=[], suggestions=[]) for r in bank]
    invoices = [dict(r, bank_ids=[], reconciliation_status="未关联流水") for r in invoices]
    by_bank, by_invoice = {r["id"]: r for r in bank}, {r["id"]: r for r in invoices}
    used_bank, used_invoice = set(), set()
    red_parties = {party_key(r, aliases) for r in invoices if r["red"]}

    def allocate(bank_ids, invoice_ids, reason):
        for bid in bank_ids:
            by_bank[bid].update(status="matched", reason=reason, invoice_ids=invoice_ids[:])
        for iid in invoice_ids:
            by_invoice[iid].update(bank_ids=bank_ids[:], reconciliation_status="已关联流水")
        used_bank.update(bank_ids)
        used_invoice.update(invoice_ids)

    for decision in decisions or []:
        bids = decision["bank_ids"]
        if decision["kind"] == "exclude":
            for bid in bids:
                by_bank[bid].update(status="excluded", reason="人工确认：" + decision["note"])
            used_bank.update(bids)
        else:
            allocate(bids, decision["invoice_ids"], "人工核对：" + decision["note"])

    for record in bank:
        if record["id"] in used_bank:
            continue
        excluded_keyword = next((word for word in config["exclude_keywords"] if word in record["summary"]), None)
        if record["direction"] == "收入":
            record.update(status="excluded", reason="收入流水不使用进项发票判断是否开票")
        elif settings.get("exclude_special", True) and excluded_keyword:
            record.update(status="excluded", reason=f"摘要命中“{excluded_keyword}”，按规则不参与进项比对")
        elif record.get("duplicate"):
            record.update(status="review", reason="银行流水号重复，需核对源表")
        elif party_key(record, aliases) in red_parties:
            record.update(status="review", reason="同一销方存在红字发票，需核对原票及冲红关系")

    eligible_bank = [r for r in bank if r["id"] not in used_bank and r["status"] == "unmatched"]
    eligible_invoice = [r for r in invoices if not r["red"] and not r["invalid"] and r["id"] not in used_invoice and party_key(r, aliases) not in red_parties]
    bank_exact, invoice_exact = defaultdict(list), defaultdict(list)
    for r in eligible_bank:
        if r["party"]:
            bank_exact[(*party_key(r, aliases), r["amount_cents"])].append(r)
    for r in eligible_invoice:
        invoice_exact[(*party_key(r, aliases), r["amount_cents"])].append(r)
    for key, transactions in bank_exact.items():
        candidates = invoice_exact[key]
        if len(transactions) == 1 and len(candidates) == 1:
            record, invoice = transactions[0], candidates[0]
            reason = "对方名称、币种与价税合计一致，双方唯一对应"
            if name_key(record["party"]) != name_key(invoice["party"]):
                reason = "已确认的名称映射、币种与价税合计一致，双方唯一对应"
            allocate([record["id"]], [invoice["id"]], reason)

    available = [r for r in eligible_invoice if r["id"] not in used_invoice]
    pending = [r for r in bank if r["id"] not in used_bank and r["status"] in ("unmatched", "review")]
    available_by_party, all_by_party, available_by_amount = defaultdict(list), defaultdict(list), defaultdict(list)
    for invoice in invoices:
        all_by_party[party_key(invoice, aliases)].append(invoice)
    for invoice in available:
        available_by_party[party_key(invoice, aliases)].append(invoice)
        available_by_amount[(invoice["currency"], invoice["amount_cents"])].append(invoice)
    for r in pending:
        same_party = available_by_party[party_key(r, aliases)] if r["party"] else []
        all_party = all_by_party[party_key(r, aliases)] if r["party"] else []
        if r["status"] != "review" and all_party:
            r.update(status="review", reason="存在同名发票，但金额、可用状态或唯一对应关系不满足自动匹配")
        for i in same_party[:config["max_suggestions"]]:
            r["suggestions"].append({"bank_ids": [r["id"]], "invoice_ids": [i["id"]], "difference_cents": i["amount_cents"] - r["amount_cents"], "label": "同名发票"})
        if 1 < len(same_party) <= config["max_subset_items"]:
            for size in range(2, len(same_party) + 1):
                for group in combinations(same_party, size):
                    if sum(i["amount_cents"] for i in group) == r["amount_cents"]:
                        r["suggestions"].insert(0, {"bank_ids": [r["id"]], "invoice_ids": [i["id"] for i in group], "difference_cents": 0, "label": "多张发票合计相等，需核对"})
                if len(r["suggestions"]) >= config["max_suggestions"]:
                    break
        if not all_party:
            equal_amount = available_by_amount[(r["currency"], r["amount_cents"])]
            for i in equal_amount[:5]:
                r["suggestions"].append({"bank_ids": [r["id"]], "invoice_ids": [i["id"]], "difference_cents": 0, "label": "金额相等、名称不同，仅供核查"})
            if equal_amount and r["status"] != "review":
                r.update(status="review", reason="存在同额但不同名的发票，需要核对实际收款方和销售方关系")
        r["suggestions"] = r["suggestions"][:config["max_suggestions"]]
    grouped = defaultdict(list)
    for r in pending:
        if r["party"]:
            grouped[party_key(r, aliases)].append(r)
    for key, group in grouped.items():
        if not 1 < len(group) <= config["max_subset_items"]:
            continue
        for invoice in available_by_party[key]:
            found = 0
            for size in range(2, len(group) + 1):
                for subset in combinations(group, size):
                    if sum(r["amount_cents"] for r in subset) == invoice["amount_cents"]:
                        proposal = {"bank_ids": [r["id"] for r in subset], "invoice_ids": [invoice["id"]], "difference_cents": 0, "label": "多笔付款合计相等，需核对"}
                        for r in subset:
                            r["suggestions"] = [proposal] + r["suggestions"][:config["max_suggestions"] - 1]
                        found += 1
                if found >= config["max_suggestions"]:
                    break
    for invoice in invoices:
        if invoice["red"]:
            invoice["reconciliation_status"] = "红字发票待核对"
        elif invoice["invalid"]:
            invoice["reconciliation_status"] = invoice["invalid"]
    stats = {}
    for state in config["statuses"]:
        items = [r for r in bank if r["status"] == state]
        stats[state] = {"count": len(items), "debit_cents": sum(r["debit_cents"] for r in items), "credit_cents": sum(r["credit_cents"] for r in items)}
    stats.update(bank_count=len(bank), invoice_count=len(invoices), debit_cents=sum(r["debit_cents"] for r in bank), credit_cents=sum(r["credit_cents"] for r in bank), invoice_net_cents=sum(r["amount_cents"] for r in invoices), red_count=sum(r["red"] for r in invoices))
    return {"bank": bank, "invoices": invoices, "stats": stats, "decisions": decisions or []}
