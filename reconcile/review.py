from datetime import datetime, timezone


def validate_decision(result, payload):
    kind = payload.get("kind")
    bids, iids = payload.get("bank_ids", []), payload.get("invoice_ids", [])
    note = str(payload.get("note", "")).strip()
    if kind not in ("match", "exclude") or not note or len(note) > 1000:
        raise ValueError("请选择核对方式，并填写 1–1000 字的核对依据")
    if not isinstance(bids, list) or not bids or len(bids) != len(set(bids)):
        raise ValueError("请选择有效且不重复的流水")
    bank = {r["id"]: r for r in result["bank"]}
    invoices = {r["id"]: r for r in result["invoices"]}
    if any(b not in bank for b in bids):
        raise ValueError("流水不存在")
    selected = [bank[b] for b in bids]
    if any(r["status"] == "matched" for r in selected):
        raise ValueError("流水已经匹配，不能重复使用")
    prior = {b for d in result["decisions"] for b in d["bank_ids"]}
    if prior.intersection(bids):
        raise ValueError("该流水已有人工确认，请先撤回该条确认")
    if kind == "match":
        if not isinstance(iids, list) or not iids or len(iids) != len(set(iids)) or any(i not in invoices for i in iids):
            raise ValueError("请选择有效且不重复的发票")
        choices = [invoices[i] for i in iids]
        if any(i["bank_ids"] or i["red"] or i["invalid"] for i in choices):
            raise ValueError("所选发票已关联、为红字或状态异常，不能用于本次确认")
        if any(r["direction"] != "支出" for r in selected):
            raise ValueError("收入不能与进项发票确认匹配")
        if len({r["currency"] for r in selected + choices}) != 1:
            raise ValueError("币种不同，不能直接确认匹配")
        if sum(r["amount_cents"] for r in selected) != sum(i["amount_cents"] for i in choices):
            raise ValueError("所选流水与发票合计不相等，请保留待确认；不支持强行抹平差额")
    return {"kind": kind, "bank_ids": bids, "invoice_ids": iids if kind == "match" else [], "note": note, "at": datetime.now(timezone.utc).isoformat()}
