from datetime import date
from .expense_categories import describe_expense


def date_range(payload):
    values = []
    for key in ("start_date", "end_date"):
        value = payload.get(key, "")
        if not isinstance(value, str):
            raise ValueError("起止日期必须使用 YYYY-MM-DD 格式")
        if value:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError:
                raise ValueError("起止日期必须是有效的 YYYY-MM-DD 日期") from None
        values.append(value)
    start, end = values
    if start and end and start > end:
        raise ValueError("开始日期不能晚于结束日期")
    return start, end


def expense_statistics(ledger, categories, payload):
    start, end = date_range(payload)
    totals = {item["id"]: {"id": item["id"], "label": item["label"], "count": 0, "debit_cents": 0} for item in categories}
    rows = []
    for bank in ledger["bank"]:
        if bank["direction"] != "支出" or start and bank["date"] < start or end and bank["date"] > end:
            continue
        classification = describe_expense(bank, categories)
        item = totals[classification["expense_category"]]
        item["count"] += 1
        item["debit_cents"] += bank["debit_cents"]
        rows.append({key: bank.get(key, "") for key in ("id", "date", "party", "summary", "debit_cents", "manual_note")} | classification)
    return {"revision": ledger["revision"], "start_date": start, "end_date": end, "categories": list(totals.values()),
            "count": len(rows), "debit_cents": sum(item["debit_cents"] for item in totals.values()),
            "rows": sorted(rows, key=lambda row: (row["date"], row["id"]), reverse=True)}
