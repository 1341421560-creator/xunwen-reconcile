from collections import defaultdict, deque
from copy import deepcopy
from .ledger_model import party_key


def progress(ledger, config):
    banks, invoices = deepcopy(ledger["bank"]), deepcopy(ledger["invoices"])
    bm, im = {r["id"]: r for r in banks}, {r["id"]: r for r in invoices}
    for row in banks + invoices:
        row.update(allocated_cents=0, allocation_ids=[], hold_reasons=[], candidate_ids=[])
    for a in ledger["allocations"]:
        if a["state"] == "active":
            for row in (bm[a["bank_id"]], im[a["invoice_id"]]):
                row["allocated_cents"] += a["amount_cents"]
                row["allocation_ids"].append(a["id"])
    for c in ledger["conflicts"]:
        if c["state"] == "pending":
            for rid in c["record_ids"]:
                (bm if c["kind"] == "bank" else im)[rid]["hold_reasons"].append("来源版本冲突待核对" if c["type"] == "version" else "缺少银行流水号，疑似重复待核对")
    exceptions = defaultdict(list)
    for i in invoices:
        i["remaining_cents"] = max(i["amount_cents"], 0) - i["allocated_cents"]
        has_conflict = bool(i["hold_reasons"])
        if i.get("red") or i.get("invalid"):
            i["hold_reasons"].append("红字发票" if i.get("red") else i["invalid"])
        if has_conflict or (i["hold_reasons"] and not i.get("exception_review")):
            exceptions[(party_key(i, ledger["settings"]), i["currency"])].append(i["id"])
        i["bank_ids"] = []
        i["status"] = "review" if i["hold_reasons"] else ("matched" if i["remaining_cents"] == 0 else "partial" if i["allocated_cents"] else "unmatched")
    for b in banks:
        b["remaining_cents"] = b["amount_cents"] - b["allocated_cents"]
        b["invoice_ids"] = []
        b["excluded_reason"] = b.get("manual_exclusion", "")
        if b["direction"] == "收入":
            b["excluded_reason"] = "收入不参与进项发票比对"
        elif not b["allocated_cents"] and ledger["settings"].get("exclude_special", True):
            hit = next((w for w in config["exclude_keywords"] if w in b["summary"]), None)
            if hit:
                b["excluded_reason"] = "摘要命中：" + hit
        if not b["excluded_reason"]:
            exception_ids = exceptions.get((party_key(b, ledger["settings"], True), b["currency"]), [])
            if exception_ids:
                b["hold_reasons"].append(f"同一销方有 {len(exception_ids)} 张红字、异常或冲突发票，请先复核：" + "、".join(exception_ids[:config["max_suggestions"]]))
    graph = defaultdict(list)
    for a in ledger["allocations"]:
        if a["state"] != "active":
            continue
        b, i = bm[a["bank_id"]], im[a["invoice_id"]]
        graph[("bank", b["id"])].append(("invoice", i["id"]))
        graph[("invoice", i["id"])].append(("bank", b["id"]))
        if i["id"] not in b["invoice_ids"]:
            b["invoice_ids"].append(i["id"])
        if b["id"] not in i["bank_ids"]:
            i["bank_ids"].append(b["id"])
    # 按关联连通关系传播复核状态，避免同一张票分给多笔付款时遗漏受影响记录。
    lookup = {("bank", r["id"]): r for r in banks} | {("invoice", r["id"]): r for r in invoices}
    held = {key for key, row in lookup.items() if row["hold_reasons"]}
    queue = deque(held)
    while queue:
        current = queue.popleft()
        for neighbor in graph[current]:
            if neighbor not in held:
                held.add(neighbor)
                lookup[neighbor]["hold_reasons"].append("已关联记录待复核，原核销金额仍保留占用")
                queue.append(neighbor)
    for i in invoices:
        if i["hold_reasons"]:
            i["status"] = "review"
    eligible_by_party, eligible_by_amount = defaultdict(list), defaultdict(list)
    for i in invoices:
        if i["remaining_cents"] > 0 and not i["hold_reasons"] and i["status"] != "review":
            eligible_by_party[(party_key(i, ledger["settings"]), i["currency"])].append(i["id"])
            eligible_by_amount[(i["currency"], i["remaining_cents"])].append(i["id"])
    for b in banks:
        same = eligible_by_party.get((party_key(b, ledger["settings"], True), b["currency"]), []) if b["party"] else []
        equal = eligible_by_amount.get((b["currency"], b["remaining_cents"]), [])
        b["candidate_ids"] = list(dict.fromkeys(same[:config["max_suggestions"]] + equal[:config["max_suggestions"]]))[:config["max_suggestions"]]
        b["candidate_count"] = len(same) if same else len(equal)
        if b["hold_reasons"]:
            b.update(status="review", reason="；".join(dict.fromkeys(b["hold_reasons"])))
        elif b["excluded_reason"]:
            b.update(status="excluded", reason=b["excluded_reason"])
        elif b["remaining_cents"] == 0:
            b.update(status="matched", reason="累计关联金额已覆盖付款，可查看实际开票日期")
        elif b["allocated_cents"]:
            b.update(status="partial", reason="已分批关联部分金额，剩余款项继续查找历史及新增发票")
        elif same or equal:
            b.update(status="review", reason="存在候选，金额不等或有多笔同额记录，需要人工确认")
        else:
            b.update(status="unmatched", reason="累计账本暂未找到对应发票")
    return banks, invoices


def statistics(banks, invoices, config):
    stats = {key: {"count": 0, "debit_cents": 0, "credit_cents": 0} for key in config["statuses"]}
    for b in banks:
        item = stats[b["status"]]
        item["count"] += 1
        item["debit_cents"] += b["debit_cents"]
        item["credit_cents"] += b["credit_cents"]
    stats.update(bank_count=len(banks), invoice_count=len(invoices),
                 debit_cents=sum(b["debit_cents"] for b in banks), credit_cents=sum(b["credit_cents"] for b in banks),
                 allocated_cents=sum(b["allocated_cents"] for b in banks),
                 outstanding_cents=sum(b["remaining_cents"] for b in banks if b["status"] != "excluded"),
                 invoice_net_cents=sum(i["amount_cents"] for i in invoices), red_count=sum(bool(i.get("red")) for i in invoices))
    return stats


def ledger_view(ledger, config, month="", unfinished=False):
    banks, invoices = progress(ledger, config)
    filtered = [b for b in banks if (not month or b["date"].startswith(month)) and (not unfinished or b["status"] in ("unmatched", "partial", "review"))]
    return dict(company=ledger["company"], revision=ledger["revision"], bank=banks, invoices=invoices,
                filtered_bank_ids=[b["id"] for b in filtered], month=month, unfinished=unfinished,
                months=sorted({b["date"][:7] for b in banks}, reverse=True), stats=statistics(banks, invoices, config),
                filtered_stats=statistics(filtered, invoices, config), batches=ledger["batches"],
                allocations=ledger["allocations"], conflicts=ledger["conflicts"], audit=ledger["audit"],
                settings=ledger["settings"], saved_at=ledger["saved_at"], schema_version=ledger["schema_version"])
