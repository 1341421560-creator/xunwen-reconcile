import copy
import csv
import json
from pathlib import Path
from fault_support import FaultCase, bank_row, invoice_row
from reconcile.auto_matching import auto_match
from reconcile.ledger_progress import ledger_view
from reconcile.ledger_validation import LedgerCorruptionError
from deployment.data_backup import backup_data
from deployment.data_restore import restore_data


class InvoiceDifferenceTests(FaultCase):
    def seed(self, payment=122400, invoice=155000):
        result = self.import_files(bank=self.bank_file([bank_row(amount=payment)]),
                                   invoice=self.invoice_file([invoice_row(amount=invoice)]))
        self.bank_id, self.invoice_id = result["bank"][0]["id"], result["invoices"][0]["id"]
        return self.service.review({"revision": result["revision"], "allocations": [
            {"bank_id": self.bank_id, "invoice_id": self.invoice_id, "amount_cents": payment}], "note": "多开"})

    def change(self, status, note="已与对方核实差额处理方式"):
        return self.service.invoice_difference({"revision": self.ledger()["revision"],
                                               "invoice_id": self.invoice_id, "status": status, "note": note})

    def test_fuxintong_balance_and_both_allocation_guards(self):
        result = self.seed()
        bank, invoice = result["bank"][0], result["invoices"][0]
        self.assertEqual((bank["status"], bank["remaining_cents"]), ("matched", 0))
        self.assertEqual((invoice["remaining_cents"], invoice["distributable_cents"], invoice["difference_status"]), (32600, 0, "pending"))
        self.assertEqual(result["allocations"][0]["note"], "多开")
        self.assertIn("326.00", bank["difference_hint"])
        result = self.import_files(bank=self.bank_file([bank_row("B2", 32600, date="2026-10-01")]))
        self.assertEqual(len(result["allocations"]), 1)
        row = dict(bank_id=result["bank"][1]["id"], invoice_id=self.invoice_id, amount_cents=32600)
        self.assert_rejected_unchanged(lambda: self.allocate([row]))
        self.assertEqual(result["bank"][1]["candidate_ids"], [])

    def test_carry_change_does_not_automatically_match_and_later_import_does(self):
        self.seed()
        self.import_files(bank=self.bank_file([bank_row("B2", 32600, date="2026-10-01")]))
        result = self.change("carry_forward")
        self.assertEqual(len(result["allocations"]), 1)
        self.assertEqual(result["invoices"][0]["distributable_cents"], 32600)
        result = self.import_files(bank=self.bank_file([bank_row("B2", 32600, date="2026-10-01")]))
        self.assertEqual(len(result["allocations"]), 2)
        invoice = result["invoices"][0]
        self.assertEqual((invoice["difference_status"], invoice["remaining_cents"]), ("cleared", 0))
        self.assertEqual(invoice["difference_note"], "已与对方核实差额处理方式")

    def test_amount_error_is_scoped_to_one_invoice(self):
        self.seed()
        result = self.change("amount_error")
        self.assertEqual(result["bank"][0]["status"], "matched")
        result = self.import_files(bank=self.bank_file([bank_row("B2", 70000)]),
                                   invoice=self.invoice_file([invoice_row("I2", 70000)]))
        self.assertEqual(result["bank"][1]["status"], "matched")
        self.assertEqual(result["invoices"][0]["distributable_cents"], 0)
        result = self.import_files(bank=self.bank_file([bank_row("B3", 32600)]))
        self.assert_rejected_unchanged(lambda: self.allocate([dict(bank_id=result["bank"][2]["id"], invoice_id=self.invoice_id, amount_cents=32600)]))

    def test_two_months_and_two_rows_in_one_submission(self):
        self.seed(100000, 150000)
        self.change("carry_forward")
        result = self.import_files(bank=self.bank_file([bank_row("B2", 100000, date="2026-10-01")]),
                                   invoice=self.invoice_file([invoice_row("I2", 50000, date="2026-10-02")]))
        b2, i2 = result["bank"][1]["id"], result["invoices"][1]["id"]
        result = self.allocate([dict(bank_id=b2, invoice_id=self.invoice_id, amount_cents=50000),
                                dict(bank_id=b2, invoice_id=i2, amount_cents=50000)])
        self.assertTrue(all(row["remaining_cents"] == 0 for row in result["bank"] + result["invoices"]))
        self.assertEqual(result["stats"]["allocated_cents"], 200000)
        self.assertEqual([i["amount_cents"] for i in result["invoices"]], [150000, 50000])

    def test_batch_final_balance_does_not_create_transient_difference(self):
        result = self.import_files(bank=self.bank_file([bank_row("B1", 100000), bank_row("B2", 50000)]),
                                   invoice=self.invoice_file([invoice_row(amount=150000)]))
        iid = result["invoices"][0]["id"]
        result = self.allocate([dict(bank_id=b["id"], invoice_id=iid, amount_cents=b["amount_cents"]) for b in result["bank"]])
        self.assertEqual(result["invoices"][0]["difference_status"], "none")
        self.assertNotIn("difference", self.service.store.load()["invoices"][0])

    def test_carry_reduction_and_revoke_reopens_pending(self):
        self.seed()
        self.change("carry_forward")
        result = self.import_files(bank=self.bank_file([bank_row("B2", 20000)]))
        result = self.allocate([dict(bank_id=result["bank"][1]["id"], invoice_id=self.invoice_id, amount_cents=10000)])
        self.assertEqual(result["invoices"][0]["difference_status"], "carry_forward")
        result = self.service.undo({"revision": result["revision"], "allocation_id": result["allocations"][-1]["id"], "note": "撤回后重新核实"})
        self.assertEqual((result["invoices"][0]["difference_status"], result["invoices"][0]["distributable_cents"]), ("pending", 0))
        events = [e for e in result["audit"] if e["action"] == "invoice_difference_auto"]
        self.assertEqual(events[-1]["previous"]["status"], "carry_forward")
        self.assertEqual(events[-1]["revision"], result["revision"])

    def test_pending_or_error_survives_full_revoke(self):
        for status in ("pending", "amount_error"):
            with self.subTest(status=status):
                self.setUp()
                result = self.seed()
                if status == "amount_error":
                    result = self.change(status)
                result = self.service.undo({"revision": result["revision"], "allocation_id": result["allocations"][0]["id"], "note": "准备核实原票"})
                invoice = result["invoices"][0]
                self.assertEqual(invoice["allocated_cents"], 0)
                self.assertTrue(invoice["difference_blocked"])
                self.assertEqual(invoice["distributable_cents"], 0)
                self.service = type(self.service)(self.directory, self.config)
                self.assertTrue(self.ledger()["invoices"][0]["difference_blocked"])

    def test_corrected_version_resets_difference_and_retains_evidence(self):
        self.seed()
        result = self.change("amount_error", "确认金额填错，等待更正")
        result = self.import_files(invoice=self.invoice_file([invoice_row(amount=122400)]))
        conflict = result["conflicts"][0]
        action = lambda: self.service.resolve({"revision": self.ledger()["revision"], "conflict_id": conflict["id"], "action": "accept", "note": "接受更正金额"})
        self.assert_rejected_unchanged(action)
        self.service.undo({"revision": result["revision"], "allocation_id": result["allocations"][0]["id"], "note": "先撤回原关联"})
        result = action()
        self.assertEqual(result["invoices"][0]["difference_status"], "none")
        self.assertEqual(result["invoices"][0]["amount_cents"], 122400)
        self.assertTrue(any(e["note"] == "确认金额填错，等待更正" for e in result["audit"]))
        self.assertIn("difference", result["conflicts"][0]["previous_record"])

    def test_legacy_read_is_byte_preserving_and_next_commit_persists(self):
        self.seed()
        legacy = json.loads(self.saved())
        legacy["invoices"][0].pop("difference")
        self.service.store.path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        before = self.saved()
        result = self.ledger()
        self.assertEqual(result["invoices"][0]["difference_status"], "pending")
        self.assertEqual(self.saved(), before)
        self.import_files(bank=self.bank_file([bank_row("OTHER", 70000, party="其他公司")]))
        self.assertEqual(json.loads(self.saved())["invoices"][0]["difference"]["status"], "pending")
        self.assertEqual(self.ledger()["allocations"][0]["note"], "多开")

    def test_legacy_in_memory_view_blocks_automatic_without_mutation(self):
        self.seed()
        legacy = json.loads(self.saved())
        legacy["invoices"][0].pop("difference")
        before = copy.deepcopy(legacy)
        view = ledger_view(legacy, self.config)
        self.assertEqual(view["invoices"][0]["distributable_cents"], 0)
        self.assertEqual(auto_match(legacy, self.config), [])
        self.assertEqual(legacy, before)

    def test_validation_stale_and_duplicate_requests_do_not_write(self):
        result = self.seed()
        base = dict(revision=result["revision"], invoice_id=self.invoice_id, status="carry_forward", note="核实")
        for change in ({"status": "cleared"}, {"status": []}, {"note": " "}, {"note": "长" * 1001}, {"note": []}, {"invoice_id": "missing"}):
            with self.subTest(change=change):
                self.assert_rejected_unchanged(lambda: self.service.invoice_difference(dict(base, **change)))
        self.service.invoice_difference(base)
        self.assert_rejected_unchanged(lambda: self.service.invoice_difference(base))
        self.assert_rejected_unchanged(lambda: self.service.review({"revision": result["revision"], "allocations": [], "note": "旧页面"}))

    def test_unused_invoice_is_normal_and_not_editable(self):
        result = self.import_files(invoice=self.invoice_file())
        invoice = result["invoices"][0]
        self.assertEqual((invoice["difference_status"], invoice["distributable_cents"]), ("none", 1200000))
        self.assert_rejected_unchanged(lambda: self.service.invoice_difference(dict(revision=result["revision"], invoice_id=invoice["id"], status="pending", note="尚未产生差额")))

    def test_red_and_conflict_hold_override_carry_permission(self):
        self.seed()
        self.change("carry_forward")
        result = self.import_files(invoice=self.invoice_file([dict(invoice_row(amount=155000), source_status="作废")]))
        self.assertEqual(result["invoices"][0]["difference_status"], "carry_forward")
        self.assertEqual(result["invoices"][0]["distributable_cents"], 0)
        self.assertEqual(result["invoices"][0]["status"], "review")

    def test_difference_record_corruption_stops_loading(self):
        self.seed()
        ledger = json.loads(self.saved())
        ledger["invoices"][0]["difference"]["remaining_cents"] = True
        self.service.store.path.write_text(json.dumps(ledger), encoding="utf-8")
        before = self.saved()
        with self.assertRaises(LedgerCorruptionError):
            self.ledger()
        self.assertEqual(self.saved(), before)

    def test_export_month_and_all_preserve_amounts_and_history(self):
        self.seed()
        outputs = []
        for month in ("", "2026-08"):
            output = self.service.export({"revision": self.ledger()["revision"], "month": month})
            directory = Path(output["directory"])
            with (directory / "发票余额明细.csv").open(encoding="utf-8-sig", newline="") as stream:
                row = list(csv.DictReader(stream))[0]
            self.assertEqual((row["全账本未分配余额"], row["可继续分配金额"], row["差额状态"]), ("326.00", "0.00", "差额待确认"))
            with (directory / "流水对账结果.csv").open(encoding="utf-8-sig", newline="") as stream:
                bank = list(csv.DictReader(stream))[0]
            self.assertIn("326.00", bank["关联发票差额提示"])
            outputs.append((directory / "发票余额明细.csv", (directory / "发票余额明细.csv").read_bytes()))
        self.change("carry_forward")
        for path, content in outputs:
            self.assertEqual(path.read_bytes(), content)

    def test_backup_restore_and_restart_keep_difference(self):
        self.seed()
        self.change("amount_error", "等待对方核实开票金额")
        before = self.saved()
        (self.directory / "config").mkdir()
        (self.directory / "config" / "defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        archive = backup_data(self.service.root, self.directory / "output")["archive"]
        target = self.directory / "restored"
        (target / "config").mkdir(parents=True)
        (target / "config" / "defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        restore_data(target, archive)
        other = type(self.service)(target, self.config)
        self.assertEqual(other.store.path.read_bytes(), before)
        self.assertEqual(other.ledger()["invoices"][0]["difference_status"], "amount_error")
        self.assertEqual(other.ledger()["allocations"][0]["note"], "多开")
