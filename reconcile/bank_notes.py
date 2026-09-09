from .audit import record_event, timestamp


def validate_bank_note(bank):
    if not isinstance(bank.get("manual_note", ""), str):
        raise ValueError("流水手工备注必须是文字")
    if not isinstance(bank.get("manual_note_updated_at", ""), str):
        raise ValueError("流水备注更新时间无效")


def update_bank_note(ledger, payload, config):
    bank = next((b for b in ledger["bank"] if b["id"] == payload.get("bank_id")), None)
    if bank is None:
        raise ValueError("流水不存在")
    note = payload.get("note")
    limit = config["manual_note_max_length"]
    if not isinstance(note, str) or len(note) > limit:
        raise ValueError(f"手工备注必须是文字，最多 {limit} 个字符")
    previous = bank.get("manual_note", "")
    bank.update(manual_note=note.strip(), manual_note_updated_at=timestamp())
    record_event(ledger, "bank_note", "更新流水手工备注", bank_id=bank["id"],
                 previous=previous, current=bank["manual_note"])
