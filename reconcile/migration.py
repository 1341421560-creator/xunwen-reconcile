import base64
import hashlib
import json
from pathlib import Path
from .audit import identifier, timestamp, record_event
from .import_reader import read_uploads
from .identity_dedup import ingest, signature
from .matching import build_result
from .ledger_model import new_ledger
from .ledger_progress import ledger_view


def migrate_legacy(service, bank_path, invoice_path):
    history = service.legacy.store.list()
    if len(history) != 1:
        raise ValueError("本次迁移基准要求恰好一份旧记录，请核对迁移来源")
    legacy = service.legacy.store.load(history[0]["id"])
    marker = legacy["id"]
    ledger = service.store.load()
    if marker in ledger["migrations"]:
        return dict(ledger_view(ledger, service.config), migration_skipped=True)
    if ledger["revision"] or legacy["decisions"]:
        raise ValueError("迁移基准发生变化：账本已有数据或旧记录含人工确认，停止迁移")
    payload = {}
    for key, path in (("bank", bank_path), ("invoice", invoice_path)):
        path = Path(path)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != legacy[key + "_hash"]:
            raise ValueError(f"{key} 原始文件哈希与旧记录不符，禁止迁移")
        payload[key] = {"name": path.name, "content": base64.b64encode(content).decode("ascii")}
    parsed = read_uploads(payload, service.config)
    if len(parsed["bank"]) != 18 or len(parsed["invoices"]) != 16:
        raise ValueError("迁移基准不是 18 笔流水和 16 张发票")
    for kind in ("bank", "invoices"):
        if [signature(r, kind) for r in parsed[kind]] != [signature(r, kind) for r in legacy[kind]]:
            raise ValueError("原始文件解析结果与旧记录不一致，停止迁移")
    backup = service.root / "backups" / ("legacy_" + marker)
    backup.mkdir(parents=True, exist_ok=True)
    original = service.legacy.store.directory / (marker + ".json")
    content = original.read_bytes()
    destination = backup / original.name
    if destination.exists() and destination.read_bytes() != content:
        raise ValueError("迁移备份与旧记录不一致，停止迁移")
    destination.write_bytes(content)
    (backup / "source-hashes.json").write_text(json.dumps({"legacy_sha256": hashlib.sha256(content).hexdigest(), "bank_sha256": legacy["bank_hash"], "invoice_sha256": legacy["invoice_hash"]}, indent=2), encoding="utf-8")
    ledger = new_ledger(service.config["company_name"])
    ledger["settings"] = legacy["settings"]
    batch_id = identifier("T")
    counts = ingest(ledger, parsed, batch_id, preserve_ids=True)
    old = build_result(legacy["bank"], legacy["invoices"], legacy["settings"], service.config, [])
    for b in old["bank"]:
        if b["status"] == "matched":
            if len(b["invoice_ids"]) != 1:
                raise ValueError("发现非一对一历史关联，停止迁移")
            ledger["allocations"].append({"id": identifier("A"), "bank_id": b["id"], "invoice_id": b["invoice_ids"][0], "amount_cents": b["amount_cents"], "state": "active", "kind": "migrated", "at": timestamp(), "note": "迁入旧记录既有自动关联：" + b["reason"], "revision": 1})
    ledger["batches"].append({"id": batch_id, "at": timestamp(), "files": parsed["files"], "counts": counts, "auto_allocation_ids": [], "auto_matched_count": 4, "historical_matched_count": 0, "notes": parsed["notes"], "controls": parsed["controls"], "legacy_session_id": marker})
    ledger["migrations"].append(marker)
    record_event(ledger, "migration", "已核验原件哈希、备份旧记录并补读交通银行账号", legacy_session_id=marker, files=parsed["files"])
    view = ledger_view(ledger, service.config)
    expected = {"matched": 4, "unmatched": 3, "review": 1, "excluded": 10, "partial": 0}
    if any(view["stats"][k]["count"] != v for k, v in expected.items()):
        raise ValueError("迁移后的对账分类不符合基准，未提交")
    service.store.commit(ledger, 0)
    return ledger_view(ledger, service.config)
