import base64
import hashlib
import io
import json
import os
import sys
import unittest
import uuid
from pathlib import Path
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reconcile.config import load_config
from reconcile.service import ReconciliationService
from ledger_fixtures import bank_row, invoice_row, upload

CONFIG = load_config(ROOT)
RUN_ROOT = Path(os.environ.get("RECON_FAULT_TEMP", str((ROOT / CONFIG["temp_dir"]).resolve() / "fault-tests")))


def edit_upload(item, edit):
    book = openpyxl.load_workbook(io.BytesIO(base64.b64decode(item["content"])))
    edit(book)
    output = io.BytesIO()
    book.save(output)
    return dict(item, content=base64.b64encode(output.getvalue()).decode("ascii"))


class FaultCase(unittest.TestCase):
    def setUp(self):
        self.directory = RUN_ROOT / (self.id().split(".")[-1][:24] + "_" + uuid.uuid4().hex[:8])
        self.config = dict(CONFIG, temp_dir="temp", request_timeout_seconds=0.4)
        self.service = ReconciliationService(self.directory, self.config)

    def ledger(self):
        return self.service.ledger()

    def bank_file(self, rows=None):
        return upload(rows or [bank_row()], "bank", CONFIG["company_name"])

    def invoice_file(self, rows=None):
        return upload(rows or [invoice_row()], "invoice", CONFIG["company_name"])

    def import_files(self, bank=None, invoice=None):
        payload = {"revision": self.ledger()["revision"]}
        if bank is not None:
            payload["bank"] = bank
        if invoice is not None:
            payload["invoice"] = invoice
        return self.service.import_files(payload)

    def saved(self):
        return self.service.store.path.read_bytes() if self.service.store.path.exists() else None

    def assert_rejected_unchanged(self, operation):
        before = self.saved()
        with self.assertRaises(ValueError):
            operation()
        self.assertEqual(self.saved(), before)

    def allocate(self, rows):
        return self.service.review({"revision": self.ledger()["revision"], "allocations": rows, "note": "异常验证：按独立金额记录核对"})
