from .audit import timestamp, record_event


def validate_expense_override(bank):
    value = bank.get("expense_classification")
    if value is not None and (not isinstance(value, dict) or not all(isinstance(value.get(key), str) for key in ("category", "note", "updated_at"))):
        raise ValueError("流水支出分类记录无效")


def describe_expense(bank, categories):
    if bank["direction"] != "支出":
        return {"expense_category": "", "expense_category_reason": "收入不计入支出统计"}
    override = bank.get("expense_classification")
    labels = {item["id"]: item["label"] for item in categories}
    if override and override["category"] in labels:
        return {"expense_category": override["category"], "expense_category_reason": "人工分类" + ("：" + override["note"] if override["note"] else "")}
    hits = [(item, next((word for word in item["keywords"] if word in bank["summary"]), "")) for item in categories]
    hits = [(item, word) for item, word in hits if word]
    if hits:
        priority = max(item.get("priority", 0) for item, _ in hits)
        hits = [(item, word) for item, word in hits if item.get("priority", 0) == priority]
    if len(hits) == 1:
        item, word = hits[0]
        return {"expense_category": item["id"], "expense_category_reason": "摘要命中：" + word}
    reason = "摘要同时命中多个最高优先级类别，请手工确认" if hits else "摘要未命中分类关键词"
    return {"expense_category": "other", "expense_category_reason": reason}


def update_expense_category(ledger, payload, categories):
    bank = next((b for b in ledger["bank"] if b["id"] == payload.get("bank_id")), None)
    if bank is None or bank["direction"] != "支出":
        raise ValueError("请选择一笔支出流水")
    category, note = payload.get("category"), payload.get("note", "")
    if not isinstance(category, str) or category not in {"auto", *(item["id"] for item in categories)}:
        raise ValueError("请选择有效的支出类别")
    if not isinstance(note, str) or len(note) > 1000:
        raise ValueError("分类说明最多 1000 个字符")
    previous = bank.get("expense_classification")
    current = None if category == "auto" else {"category": category, "note": note.strip(), "updated_at": timestamp()}
    bank["expense_classification"] = current
    record_event(ledger, "expense_category", "更新支出统计分类", bank_id=bank["id"], previous=previous, current=current)
