import copy
import json
import os
import subprocess
import sys
import time
from unittest.mock import patch
from fault_support import FaultCase, bank_row, invoice_row
from reconcile.ledger_storage import LedgerStore


class StorageFaults(FaultCase):
    def seed(self):
        return self.import_files(bank=self.bank_file(), invoice=self.invoice_file())

    def test_fsync_failure_preserves_original_and_retry(self):
        self.seed()
        before = self.saved()
        with patch("reconcile.ledger_storage.os.fsync", side_effect=OSError("模拟磁盘同步失败")):
            with self.assertRaises(OSError):
                self.import_files(invoice=self.invoice_file([invoice_row("NEW", 500)]))
        self.assertEqual(self.saved(), before)
        self.assertEqual(len(self.import_files(invoice=self.invoice_file([invoice_row("NEW", 500)]))["invoices"]), 2)

    def test_temp_write_permission_failure_preserves_original(self):
        self.seed()
        before = self.saved()
        from pathlib import Path
        original = Path.open
        def denied(path, mode="r", *args, **kwargs):
            if path.name.startswith("snapshot-") and "w" in mode:
                raise PermissionError("模拟暂存目录不可写")
            return original(path, mode, *args, **kwargs)
        with patch.object(Path, "open", denied):
            with self.assertRaises(PermissionError):
                self.import_files(invoice=self.invoice_file([invoice_row("NEW", 500)]))
        self.assertEqual(self.saved(), before)

    def test_orphan_partial_temp_snapshot_does_not_replace_formal(self):
        self.seed()
        before = self.saved()
        (self.service.store.temp / "snapshot-interrupted.json").write_text('{"revision":', encoding="utf-8")
        self.service = type(self.service)(self.directory, self.config)
        self.assertEqual(self.ledger()["revision"], 1)
        self.assertEqual(self.saved(), before)

    def test_broken_json_is_not_reset_or_overwritten(self):
        self.seed()
        path = self.service.store.path
        path.write_text('{"schema_version":1', encoding="utf-8")
        before = self.saved()
        with self.assertRaises(ValueError):
            self.service.bootstrap()
        with self.assertRaises(ValueError):
            self.service.import_files({"revision": 0, "invoice": self.invoice_file()})
        self.assertEqual(self.saved(), before)

    def test_schema_corruption_is_rejected_before_balance_changes(self):
        self.seed()
        original = self.service.store.load()
        mutations = {
            "unknown_allocation_state": lambda l: l["allocations"][0].update(state="activ"),
            "negative_revision": lambda l: l.update(revision=-1),
            "debit_amount_disagreement": lambda l: l["bank"][0].update(debit_cents=100),
            "duplicate_bank_identity": lambda l: l["bank"].append(dict(l["bank"][0], id="ANOTHER")),
            "foreign_currency": lambda l: [r.update(currency="USD") for r in l["bank"]+l["invoices"]],
            "missing_conflict_reference": lambda l: l["conflicts"].append({"id":"C", "kind":"bank", "record_ids":["missing"], "state":"pending", "type":"version"}),
            "wrong_company": lambda l: l["company"].update(name="另一家公司"),
            "wrong_root_type": lambda l: None,
        }
        for name, mutate in mutations.items():
            with self.subTest(corruption=name):
                ledger = copy.deepcopy(original)
                mutate(ledger)
                value = [] if name == "wrong_root_type" else ledger
                self.service.store.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                before = self.saved()
                with self.assertRaises(ValueError):
                    self.service.bootstrap()
                self.assertEqual(self.saved(), before)

    def test_same_ledger_different_temp_directory_still_has_single_lock(self):
        self.seed()
        other = LedgerStore(self.directory / "ledger", self.directory / "another-temp", self.config["company_name"])
        with self.service.store.exclusive():
            with self.assertRaises(ValueError):
                with other.exclusive():
                    self.fail("同一正式账本不得因暂存目录不同而同时获得写入锁")

    def test_invalid_snapshot_not_committed(self):
        self.seed()
        ledger = self.service.store.load()
        before = self.saved()
        ledger["allocations"][0]["amount_cents"] += 1
        with self.assertRaises(ValueError):
            self.service.store.commit(ledger, ledger["revision"])
        self.assertEqual(self.saved(), before)

    def test_killed_process_releases_ledger_lock(self):
        self.seed()
        from pathlib import Path
        with (self.directory / "killed-worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name("fault_worker.py")), str(self.directory), "lock"], stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 10
                while not (self.service.store.temp / "worker-ready.txt").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue((self.service.store.temp / "worker-ready.txt").exists())
                with self.assertRaises(ValueError):
                    with self.service.store.exclusive():
                        self.fail("不能占用另一进程持有的锁")
            finally:
                process.terminate()
                process.wait(timeout=10)
        with self.service.store.exclusive():
            self.assertEqual(self.ledger()["revision"], 1)

    def test_process_exits_after_staging_before_atomic_replace(self):
        self.seed()
        from pathlib import Path
        before = self.saved()
        with (self.directory / "crash-worker.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run([sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name("fault_worker.py")), str(self.directory), "before_replace"], stdout=log, stderr=log, timeout=300)
        self.assertEqual(completed.returncode, 91)
        self.assertEqual(self.saved(), before)
        self.assertTrue(list(self.service.store.temp.glob("snapshot-*.json")))
        self.assertEqual(len(self.import_files(invoice=self.invoice_file([invoice_row("AFTER_CRASH", 500)]))["invoices"]), 2)

    def test_transient_windows_sharing_failure_retries_atomic_replace(self):
        self.seed()
        original = os.replace
        calls = []
        def intermittent(source, destination):
            calls.append(1)
            if len(calls) < 3:
                error = PermissionError("模拟 Windows 文件短暂占用")
                error.winerror = 32
                raise error
            return original(source, destination)
        with patch("reconcile.atomic_write.os.replace", intermittent):
            result = self.import_files(invoice=self.invoice_file([invoice_row("RETRY", 100)]))
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["revision"], 2)
        self.assertEqual(len(result["invoices"]), 2)

    def test_persistent_windows_sharing_failure_is_bounded_and_preserves_data(self):
        self.seed()
        before = self.saved()
        error = PermissionError("模拟持续权限故障")
        error.winerror = 5
        with patch("reconcile.atomic_write.os.replace", side_effect=error) as replace, patch("reconcile.atomic_write.time.sleep"):
            with self.assertRaises(PermissionError):
                self.import_files(invoice=self.invoice_file([invoice_row("RETRY", 100)]))
        self.assertEqual(replace.call_count, 6)
        self.assertEqual(self.saved(), before)
