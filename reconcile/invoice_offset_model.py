from collections import defaultdict
from .normalize import name_key


def source_offset_state(invoice):
    state = invoice.get("source_status", "")
    if "部分" in state and any(word in state for word in ("红冲", "冲红")):
        return "partial"
    return "full" if any(word in state for word in ("全额红冲", "已红冲", "全额冲红", "已冲红")) else ""


def blocking_invalid(invoice):
    state, invalid = invoice.get("source_status", ""), invoice.get("invalid", "")
    if any(word in state for word in ("作废", "失控", "异常")):
        return invalid or "发票状态：" + state
    # 旧账本曾将来源红冲状态写入异常字段，仅兼容该标记，其他异常继续保留。
    if source_offset_state(invoice) and invalid == "发票状态：" + state:
        return ""
    return invalid


def offset_totals(ledger):
    totals = defaultdict(int)
    for relation in ledger.get("invoice_offsets", []):
        if relation["state"] == "active":
            totals[relation["blue_invoice_id"]] += relation["amount_cents"]
    return totals


def net_capacity(invoice, totals=None):
    return max(invoice["amount_cents"], 0) - (totals or {}).get(invoice["id"], 0)


def source_offset_hold(invoice, offset):
    if invoice.get("red"):
        return ""
    state, face = source_offset_state(invoice), invoice["amount_cents"]
    if state == "full" and offset != face:
        return "来源显示已全额红冲，需补齐并确认对应红票；当前暂停分配"
    if state == "partial" and not 0 < offset < face:
        return "来源部分红冲状态与已确认金额不一致，需核对对应红票"
    return ""


def require_no_offsets(ledger, invoice_ids):
    ids = set(invoice_ids)
    if any(r["state"] == "active" and (r["red_invoice_id"] in ids or r["blue_invoice_id"] in ids)
           for r in ledger.get("invoice_offsets", [])):
        raise ValueError("请先撤回相关红冲关系，再更正发票版本或修改异常复核结论")


def validate_offsets(ledger, invoices):
    records = ledger.get("invoice_offsets", [])
    if not isinstance(records, list) or records and ledger["schema_version"] != 2:
        raise ValueError("红冲关系需要新版账本格式")
    if ledger["schema_version"] == 2 and "invoice_offsets" not in ledger:
        raise ValueError("新版账本缺少红冲关系记录")
    seen, used, totals = set(), set(), defaultdict(int)
    for relation in records:
        if not isinstance(relation, dict):
            raise ValueError("红冲关系格式无效")
        rid, red_id, blue_id = (relation.get(k) for k in ("id", "red_invoice_id", "blue_invoice_id"))
        if not all(isinstance(v, str) and v for v in (rid, red_id, blue_id)) or rid in seen:
            raise ValueError("红冲关系编号无效或重复")
        seen.add(rid)
        if red_id not in invoices or blue_id not in invoices or red_id == blue_id:
            raise ValueError("红冲关系引用不存在或相同发票")
        amount, state = relation.get("amount_cents"), relation.get("state")
        if type(amount) is not int or amount <= 0 or state not in ("active", "revoked"):
            raise ValueError("红冲金额或关系状态无效")
        if any(not isinstance(relation.get(k), str) or not relation[k].strip() for k in ("note", "at")):
            raise ValueError("红冲关系缺少依据或时间")
        if state == "revoked":
            if any(not isinstance(relation.get(k), str) or not relation[k].strip() for k in ("revoke_note", "revoked_at")):
                raise ValueError("撤回红冲缺少依据或时间")
            continue
        red, blue = invoices[red_id], invoices[blue_id]
        if red_id in used or not red.get("red") or blue.get("red") or red["amount_cents"] >= 0 or blue["amount_cents"] <= 0:
            raise ValueError("红票重复使用或红蓝票方向无效")
        if amount != -red["amount_cents"] or red.get("exception_review"):
            raise ValueError("红冲金额与红票不符，或同时存在异常复核结论")
        if red["company_id"] != blue["company_id"] or red["currency"] != blue["currency"] or not name_key(red["party"]) or name_key(red["party"]) != name_key(blue["party"]):
            raise ValueError("红蓝票公司、销方或币种不一致")
        if blocking_invalid(red) or blocking_invalid(blue):
            raise ValueError("真正异常发票不能建立有效红冲关系")
        used.add(red_id)
        totals[blue_id] += amount
    if any(total > invoices[iid]["amount_cents"] for iid, total in totals.items()):
        raise ValueError("累计冲红超过原票金额")
    return totals
