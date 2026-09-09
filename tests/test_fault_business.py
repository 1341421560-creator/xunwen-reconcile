import copy
import random
from fault_support import FaultCase, bank_row, invoice_row
from reconcile.identity_dedup import ingest
from reconcile.ledger_model import new_ledger
from reconcile.ledger_progress import ledger_view


class BusinessFaults(FaultCase):
    def test_repeated_status_transition_creates_new_conflict(self):
        result = self.import_files(invoice=self.invoice_file())
        normal, void = self.invoice_file(), self.invoice_file([dict(invoice_row(), source_status="作废")])
        for file in (void, normal):
            result = self.import_files(invoice=file)
            pending = [c for c in result["conflicts"] if c["state"] == "pending"]
            self.assertEqual(len(pending), 1)
            result = self.service.resolve({"revision": result["revision"], "conflict_id": pending[0]["id"], "action": "accept", "note": "确认来源状态更新"})
        result = self.import_files(invoice=void)
        self.assertEqual(sum(c["state"] == "pending" for c in result["conflicts"]), 1)
        self.assertEqual(result["invoices"][0]["status"], "review")

    def test_conflict_hold_propagates_through_shared_allocations(self):
        result = self.import_files(bank=self.bank_file([bank_row("R1", 50000, "甲"), bank_row("R2", 50000, "乙")]), invoice=self.invoice_file([invoice_row("I1", 70000, "销方甲"), invoice_row("I2", 30000, "销方乙")]))
        b1, b2 = [b["id"] for b in result["bank"]]
        i1, i2 = [i["id"] for i in result["invoices"]]
        self.allocate([dict(bank_id=b1, invoice_id=i1, amount_cents=50000), dict(bank_id=b2, invoice_id=i1, amount_cents=20000), dict(bank_id=b2, invoice_id=i2, amount_cents=30000)])
        result = self.import_files(bank=self.bank_file([bank_row("R1", 60000, "甲")]))
        self.assertTrue(all(b["status"] == "review" for b in result["bank"]))
        self.assertTrue(all(i["status"] == "review" for i in result["invoices"]))
        self.assertEqual(result["stats"]["allocated_cents"], 100000)

    def test_holds_do_not_depend_on_allocation_order(self):
        self.test_conflict_hold_propagates_through_shared_allocations()
        ledger = self.service.store.load()
        a = ledger_view(ledger, self.config)
        ledger["allocations"].reverse()
        b = ledger_view(ledger, self.config)
        self.assertEqual([r["status"] for r in a["bank"] + a["invoices"]], [r["status"] for r in b["bank"] + b["invoices"]])

    def test_red_warning_size_is_bounded_for_same_seller(self):
        ledger = new_ledger(self.config["company_name"])
        rows = [dict(invoice_row(str(n), -100), red=True) for n in range(400)]
        ingest(ledger, {"bank": [bank_row()], "invoices": rows}, "batch")
        result = ledger_view(ledger, self.config)
        self.assertLess(len(result["bank"][0]["reason"]), 2000)
        self.assertEqual(result["bank"][0]["status"], "review")

    def test_all_invalid_allocation_values_rejected_without_write(self):
        result = self.import_files(bank=self.bank_file(), invoice=self.invoice_file([invoice_row(amount=1230000)]))
        row = dict(bank_id=result["bank"][0]["id"], invoice_id=result["invoices"][0]["id"], amount_cents=1)
        for value in (-1, 0, True, 0.1, "1", None, 10**30, [], {}):
            with self.subTest(value=value):
                self.assert_rejected_unchanged(lambda: self.allocate([dict(row, amount_cents=value)]))

    def test_unknown_reference_and_missing_note_are_rejected(self):
        self.import_files(bank=self.bank_file(), invoice=self.invoice_file([invoice_row(amount=1230000)]))
        for payload in ({"allocations": [], "note": "说明"}, {"allocations": [{"bank_id": "missing", "invoice_id": "missing", "amount_cents": 1}], "note": "说明"}, {"allocations": [{}], "note": ""}):
            with self.subTest(payload=payload):
                self.assert_rejected_unchanged(lambda: self.service.review(dict(payload, revision=self.ledger()["revision"])))

    def test_repeated_undo_and_excluding_allocated_payment_rejected(self):
        result = self.import_files(bank=self.bank_file(), invoice=self.invoice_file())
        a = result["allocations"][0]
        self.assert_rejected_unchanged(lambda: self.service.exclusion({"revision": result["revision"], "bank_id": a["bank_id"], "excluded": True, "note": "不能隐藏已核销金额"}))
        self.service.undo({"revision": result["revision"], "allocation_id": a["id"], "note": "撤回一次"})
        self.assert_rejected_unchanged(lambda: self.service.undo({"revision": self.ledger()["revision"], "allocation_id": a["id"], "note": "重复撤回"}))

    def test_rules_reject_chain_cycle_empty_and_wrong_types(self):
        self.import_files(bank=self.bank_file())
        for aliases in ({"甲": "乙", "乙": "甲"}, {"甲": "乙", "乙": "丙"}, {"": "甲"}, [], {"甲": None}):
            with self.subTest(aliases=aliases):
                self.assert_rejected_unchanged(lambda: self.service.settings({"revision": self.ledger()["revision"], "aliases": aliases, "exclude_special": True}))

    def test_rule_and_classification_booleans_must_be_boolean(self):
        result = self.import_files(bank=self.bank_file())
        for value in ("false", 0, 1, None, []):
            with self.subTest(value=value):
                self.assert_rejected_unchanged(lambda: self.service.settings({"revision": self.ledger()["revision"], "aliases": {}, "exclude_special": value}))
                self.assert_rejected_unchanged(lambda: self.service.exclusion({"revision": self.ledger()["revision"], "bank_id": result["bank"][0]["id"], "excluded": value, "note": "无效类型"}))

    def test_seeded_random_allocate_revoke_import_restart(self):
        randomizer = random.Random(20260908)
        result = self.import_files(bank=self.bank_file([bank_row(str(n), 10000+n*113, "付款方") for n in range(5)]), invoice=self.invoice_file([invoice_row(str(n), 8000+n*157, "开票方") for n in range(7)]))
        oracle = {}
        for step in range(180):
            view = self.ledger()
            available_b = [b for b in view["bank"] if b["remaining_cents"] > 0]
            available_i = [i for i in view["invoices"] if i["remaining_cents"] > 0]
            active = [a for a in view["allocations"] if a["state"] == "active"]
            if active and randomizer.random() < 0.4:
                a = randomizer.choice(active)
                self.service.undo({"revision": view["revision"], "allocation_id": a["id"], "note": "随机序列撤回"})
                oracle.pop(a["id"])
            elif available_b and available_i:
                b, i = randomizer.choice(available_b), randomizer.choice(available_i)
                amount = randomizer.randint(1, min(b["remaining_cents"], i["remaining_cents"]))
                if i["difference_blocked"]:
                    self.service.invoice_difference({"revision": view["revision"], "invoice_id": i["id"], "status": "carry_forward", "note": "随机序列确认剩余差额可继续使用"})
                updated = self.allocate([dict(bank_id=b["id"], invoice_id=i["id"], amount_cents=amount)])
                a = updated["allocations"][-1]
                oracle[a["id"]] = (b["id"], i["id"], amount)
            if step % 17 == 0:
                self.service = type(self.service)(self.directory, self.config)
            after = self.ledger()
            for b in after["bank"]:
                expected = sum(n for bid, _, n in oracle.values() if bid == b["id"])
                self.assertEqual(b["allocated_cents"], expected)
                self.assertEqual(b["remaining_cents"], b["amount_cents"]-expected)
            for i in after["invoices"]:
                expected = sum(n for _, iid, n in oracle.values() if iid == i["id"])
                self.assertEqual(i["allocated_cents"], expected)
                self.assertGreaterEqual(i["remaining_cents"], 0)
