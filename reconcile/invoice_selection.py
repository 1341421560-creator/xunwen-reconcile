from .audit import record_event


def validate_invoice_selection(invoice):
    if "include_in_total" in invoice and type(invoice["include_in_total"]) is not bool:
        raise ValueError("发票合计勾选状态必须为布尔值")


def preserve_invoice_selection(source, target):
    target["include_in_total"] = source.get("include_in_total", True)


def update_invoice_selection(ledger, payload):
    ids, selected = payload.get("invoice_ids"), payload.get("selected")
    if not isinstance(ids, list) or not ids or any(not isinstance(value, str) or not value.strip() for value in ids):
        raise ValueError("请选择至少一张有效发票，发票编号必须为字符串列表")
    if type(selected) is not bool:
        raise ValueError("发票勾选状态必须为 true 或 false")
    ids = list(dict.fromkeys(ids))
    invoices = {invoice["id"]: invoice for invoice in ledger["invoices"]}
    if any(invoice_id not in invoices for invoice_id in ids):
        raise ValueError("所选发票不存在或不属于当前公司，整批勾选未保存")
    previous = {invoice_id: invoices[invoice_id].get("include_in_total", True) for invoice_id in ids}
    for invoice_id in ids:
        invoices[invoice_id]["include_in_total"] = selected
    record_event(ledger, "invoice_selection", "更新发票票面金额合计勾选", invoice_ids=ids, previous=previous, selected=selected)
