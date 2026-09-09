import json
from pathlib import Path
from fault_support import FaultCase, bank_row, invoice_row


class ReviewReversalTests(FaultCase):
    def resolve(self, cid, action):
        return self.service.resolve({"revision": self.ledger()["revision"], "conflict_id": cid, "action": action, "note": "核对依据保留"})

    def reverse(self, cid):
        return self.service.reverse_conflict({"revision": self.ledger()["revision"], "conflict_id": cid, "note": "重新核查撤回"})

    def version_conflict(self):
        self.import_files(bank=self.bank_file(), invoice=self.invoice_file())
        return self.import_files(invoice=self.invoice_file([invoice_row(amount=1300000)]))

    def test_keep_reversal_restores_hold_and_retains_allocations(self):
        view = self.version_conflict()
        cid = view["conflicts"][0]["id"]
        self.resolve(cid, "keep")
        view = self.reverse(cid)
        self.assertEqual(view["conflicts"][0]["state"], "pending")
        self.assertEqual(view["bank"][0]["status"], "review")
        self.assertEqual(view["bank"][0]["allocated_cents"], 1200000)
        self.assertEqual(view["conflicts"][0]["reversal_history"][0]["previous_note"], "核对依据保留")
        self.assert_rejected_unchanged(lambda: self.reverse(cid))

    def test_accepted_version_requires_undo_and_restores_original(self):
        view = self.version_conflict()
        cid = view["conflicts"][0]["id"]
        self.service.undo({"revision": view["revision"], "allocation_id": view["allocations"][0]["id"], "note": "更正前撤回"})
        view = self.resolve(cid, "accept")
        bid, iid = view["bank"][0]["id"], view["invoices"][0]["id"]
        view = self.allocate([dict(bank_id=bid, invoice_id=iid, amount_cents=1200000)])
        self.assert_rejected_unchanged(lambda: self.reverse(cid))
        view = self.service.undo({"revision": view["revision"], "allocation_id": view["allocations"][-1]["id"], "note": "撤回新版关联"})
        view = self.reverse(cid)
        self.assertEqual(view["invoices"][0]["amount_cents"], 1200000)
        self.assertEqual(view["invoices"][0]["allocated_cents"], 0)
        self.assertEqual(len(view["allocations"]), 2)
        self.assertEqual(view["conflicts"][0]["reversal_history"][0]["replaced_record"]["amount_cents"], 1300000)

    def test_versions_must_be_reversed_in_reverse_order(self):
        self.import_files(invoice=self.invoice_file())
        first = self.import_files(invoice=self.invoice_file([invoice_row(amount=1300000)]))["conflicts"][0]["id"]
        self.resolve(first, "accept")
        second = self.import_files(invoice=self.invoice_file([invoice_row(amount=1400000)]))["conflicts"][-1]["id"]
        self.resolve(second, "accept")
        self.assert_rejected_unchanged(lambda: self.reverse(first))
        self.reverse(second)
        view = self.reverse(first)
        self.assertEqual(view["invoices"][0]["amount_cents"], 1200000)
        self.assertTrue(all(c["state"] == "pending" for c in view["conflicts"]))

    def test_new_payment_reversal_archives_history_and_can_be_confirmed_again(self):
        file = self.bank_file([bank_row("")])
        self.import_files(bank=file)
        view = self.import_files(bank=file)
        cid = view["conflicts"][0]["id"]
        view = self.resolve(cid, "new")
        new_id = view["conflicts"][0]["accepted_record_id"]
        view = self.import_files(invoice=self.invoice_file())
        row = dict(bank_id=new_id, invoice_id=view["invoices"][0]["id"], amount_cents=1200000)
        view = self.allocate([row])
        self.assert_rejected_unchanged(lambda: self.reverse(cid))
        self.service.undo({"revision": view["revision"], "allocation_id": view["allocations"][0]["id"], "note": "撤回疑似重复付款关联"})
        view = self.reverse(cid)
        self.assertEqual(len(view["bank"]), 1)
        self.assertEqual(self.service.expense_statistics({})["debit_cents"], 1200000)
        history = view["conflicts"][0]["reversal_history"][0]
        self.assertEqual(history["withdrawn_record"]["id"], new_id)
        self.assertEqual(history["withdrawn_allocations"][0]["state"], "revoked")
        self.service = type(self.service)(self.directory, self.config)
        exported = self.service.export({"revision": view["revision"], "month": ""})
        snapshot = json.loads((Path(exported["directory"]) / "完整对账快照.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["conflicts"][0]["reversal_history"][0], history)
        view = self.resolve(cid, "new")
        self.assertEqual(view["bank"][-1]["id"], new_id)
        self.assertEqual(view["allocations"][0]["state"], "revoked")
        self.assertEqual(view["bank"][-1]["allocated_cents"], 0)
        view = self.reverse(cid)
        self.assertEqual(len(view["bank"]), 1)
        self.assertEqual(len(view["conflicts"][0]["reversal_history"]), 2)

    def test_legacy_accepted_versions_can_be_reversed_in_order(self):
        self.import_files(invoice=self.invoice_file())
        first = self.import_files(invoice=self.invoice_file([invoice_row(amount=1300000)]))["conflicts"][0]["id"]
        self.resolve(first, "accept")
        second = self.import_files(invoice=self.invoice_file([invoice_row(amount=1400000)]))["conflicts"][-1]["id"]
        self.resolve(second, "accept")
        legacy = json.loads(self.saved())
        for conflict in legacy["conflicts"]:
            conflict.pop("resolved_record_version")
        self.service.store.path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        self.reverse(second)
        self.assertEqual(self.reverse(first)["invoices"][0]["amount_cents"], 1200000)

    def test_exception_reversal_reblocks_related_records_without_losing_allocations(self):
        view = self.import_files(bank=self.bank_file(), invoice=self.invoice_file([invoice_row(), dict(invoice_row("RED", -1200000), red=True)]))
        iid = view["invoices"][1]["id"]
        view = self.service.exception({"revision": view["revision"], "invoice_id": iid, "note": "原异常复核依据"})
        view = self.import_files(invoice=self.invoice_file())
        self.assertEqual(view["bank"][0]["status"], "matched")
        view = self.service.reverse_exception({"revision": view["revision"], "invoice_id": iid, "note": "需重新核查红票"})
        self.assertEqual(view["bank"][0]["status"], "review")
        self.assertEqual(view["bank"][0]["allocated_cents"], 1200000)
        self.assertFalse(view["invoices"][1]["exception_review"])
        self.assertEqual(view["invoices"][1]["exception_review_history"][0]["previous"]["note"], "原异常复核依据")
        self.assert_rejected_unchanged(lambda: self.service.reverse_exception({"revision": view["revision"], "invoice_id": iid, "note": "重复撤回"}))

    def test_reverse_validation_and_stale_version_preserve_ledger(self):
        view = self.version_conflict()
        cid = view["conflicts"][0]["id"]
        self.resolve(cid, "keep")
        for payload in ({"conflict_id": "bad", "note": "依据"}, {"conflict_id": cid, "note": ""}):
            self.assert_rejected_unchanged(lambda: self.service.reverse_conflict(dict(payload, revision=self.ledger()["revision"])))
        self.assert_rejected_unchanged(lambda: self.service.reverse_conflict({"revision": view["revision"], "conflict_id": cid, "note": "过期提交"}))
