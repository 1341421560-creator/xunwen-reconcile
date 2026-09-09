from pathlib import Path
from threading import RLock
from .audit import identifier, timestamp, record_event
from .ledger_storage import LedgerStore
from .ledger_progress import ledger_view
from .import_reader import read_uploads
from .identity_dedup import ingest
from .auto_matching import auto_match
from .allocations import allocate, revoke, set_exclusion
from .conflict_review import resolve_conflict, review_exception
from .ledger_rules import update_rules
from .ledger_export import export_ledger
from .legacy_service import ReconciliationService as LegacyService
from .input_validation import month_field, boolean_field
from .invoice_difference import update_difference
from .bank_notes import update_bank_note
from .expense_categories import update_expense_category
from .expense_statistics import expense_statistics
from .review_reversal import reverse_conflict, reverse_exception


class ReconciliationService:
    def __init__(self, root, config):
        self.root = Path(root)
        self.config = config
        self.lock = RLock()
        self.store = LedgerStore(self.root / config["ledger_dir"], self.root / config["temp_dir"], config["company_name"])
        self.legacy = LegacyService(root, config)

    def ledger(self, payload=None):
        payload = payload or {}
        return ledger_view(self.store.load(), self.config, month_field(payload), boolean_field(payload, "unfinished"))

    def bootstrap(self):
        history = self.legacy.store.list()
        return {"app_name": self.config["app_name"], "company_name": self.config["company_name"],
                "statuses": self.config["statuses"], "history": history, "result": self.ledger(),
                "migration_required": bool(history and not self.store.path.exists())}

    def current(self, payload):
        if not self.store.path.exists() and self.legacy.store.list():
            raise ValueError("检测到旧记录，请先运行一次性迁移脚本，防止与历史账本混用")
        ledger = self.store.load()
        expected = payload.get("revision")
        if type(expected) is not int or expected != ledger["revision"]:
            raise ValueError("账本已更新或缺少版本号，请刷新后重新核对并提交")
        return ledger, expected

    def import_files(self, payload):
        ledger, expected = self.current(payload)
        parsed = read_uploads(payload, self.config)
        historical = {b["id"] for b in ledger["bank"]}
        bid = identifier("T")
        counts = ingest(ledger, parsed, bid)
        created = auto_match(ledger, self.config)
        batch = {"id": bid, "at": timestamp(), "files": parsed["files"], "counts": counts,
                 "auto_allocation_ids": [a["id"] for a in created], "auto_matched_count": len(created),
                 "historical_matched_count": len({a["bank_id"] for a in created if a["bank_id"] in historical}),
                 "notes": parsed["notes"], "controls": parsed["controls"]}
        ledger["batches"].append(batch)
        record_event(ledger, "import", "累计导入并追加来源", batch_id=bid, counts=counts)
        self.store.commit(ledger, expected)
        return dict(ledger_view(ledger, self.config), last_import=batch)

    def mutate(self, payload, operation, run_auto=False):
        ledger, expected = self.current(payload)
        operation(ledger, payload)
        if run_auto:
            auto_match(ledger, self.config)
        self.store.commit(ledger, expected)
        return ledger_view(ledger, self.config)

    def review(self, payload):
        return self.mutate(payload, lambda l, p: allocate(l, p, self.config))

    def undo(self, payload):
        return self.mutate(payload, revoke)

    def invoice_difference(self, payload):
        return self.mutate(payload, update_difference)

    def settings(self, payload):
        return self.mutate(payload, update_rules, True)

    def bank_note(self, payload):
        return self.mutate(payload, lambda ledger, data: update_bank_note(ledger, data, self.config))

    def expense_category(self, payload):
        return self.mutate(payload, lambda ledger, data: update_expense_category(ledger, data, self.config["expense_categories"]))

    def expense_statistics(self, payload):
        return expense_statistics(self.store.load(), self.config["expense_categories"], payload)

    def reverse_conflict(self, payload):
        return self.mutate(payload, reverse_conflict)

    def reverse_exception(self, payload):
        return self.mutate(payload, reverse_exception)

    def exclusion(self, payload):
        return self.mutate(payload, set_exclusion)

    def resolve(self, payload):
        return self.mutate(payload, resolve_conflict)

    def exception(self, payload):
        return self.mutate(payload, review_exception)

    def restore(self, payload):
        return dict(self.legacy.render(self.legacy.store.load(payload["session_id"])), readonly=True)

    def export(self, payload):
        ledger, _ = self.current(payload)
        directory = export_ledger(ledger_view(ledger, self.config), self.root / self.config["report_dir"], self.config["statuses"], month_field(payload))
        return {"directory": str(directory.resolve()), "files": [{"name": p.name, "url": "/reports/" + directory.name + "/" + p.name} for p in directory.iterdir()]}
