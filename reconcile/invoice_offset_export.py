from collections import defaultdict, deque
from decimal import Decimal
from .export import write_csv


def offset_export_scope(view, invoice_ids, all_invoices=False):
    # 按有效和撤回历史的双向关系补齐原票、红票，保留证据引用。
    graph = defaultdict(set)
    for row in view.get("invoice_offsets", []):
        red, blue = row["red_invoice_id"], row["blue_invoice_id"]
        graph[red].add(blue)
        graph[blue].add(red)
    ids = {i["id"] for i in view["invoices"]} if all_invoices else set(invoice_ids)
    queue = deque(ids)
    while queue:
        for neighbor in graph[queue.popleft()]:
            if neighbor not in ids:
                ids.add(neighbor)
                queue.append(neighbor)
    return ids, [r for r in view.get("invoice_offsets", []) if r["red_invoice_id"] in ids]


def export_offsets(directory, relations, invoices, at):
    money = lambda value: Decimal(value).scaleb(-2)
    write_csv(directory / "红冲关系明细.csv", ["红冲编号", "状态", "红票编号", "红票号码", "原票编号", "原票号码", "冲红金额", "有效冲红金额", "确认时间", "确认依据", "撤回时间", "撤回依据", "导出时间"], [
        [r["id"], "有效" if r["state"] == "active" else "已撤回", r["red_invoice_id"], "'"+invoices[r["red_invoice_id"]]["number"],
         r["blue_invoice_id"], "'"+invoices[r["blue_invoice_id"]]["number"], money(r["amount_cents"]),
         money(r["amount_cents"] if r["state"] == "active" else 0), r["at"], r["note"], r.get("revoked_at", ""), r.get("revoke_note", ""), at]
        for r in relations])
