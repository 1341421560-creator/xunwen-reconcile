import csv
import json
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from fault_support import FaultCase, bank_row, invoice_row
from deployment.data_backup import backup_data
from deployment.data_restore import restore_data


class ComplexScenarioTests(FaultCase):
    def mutate(self, operation, **payload):
        return getattr(self.service, operation)({"revision": self.ledger()["revision"], **payload})

    def assert_statistics(self, expected, payload=None):
        before = self.saved()
        stats = self.service.expense_statistics(payload or {})
        totals = {item["id"]: item["debit_cents"] for item in stats["categories"]}
        self.assertEqual(totals, {item["id"]: expected.get(item["id"], 0) for item in self.config["expense_categories"]})
        self.assertEqual(stats["debit_cents"], sum(expected.values()))
        self.assertEqual(stats["debit_cents"], sum(row["debit_cents"] for row in stats["rows"]))
        self.assertEqual(stats["count"], len({row["id"] for row in stats["rows"]}))
        self.assertEqual(stats["count"], sum(item["count"] for item in stats["categories"]))
        self.assertEqual(self.saved(), before)
        return stats

    def test_cross_month_split_revoke_and_difference_do_not_change_spending(self):
        party = "深圳市富芯通电子有限公司"
        view = self.import_files(bank=self.bank_file([bank_row("AUG", 100000, party, "2026-08-01")]),
                                 invoice=self.invoice_file([invoice_row("I1", 150000, party)]))
        b1, i1 = view["bank"][0]["id"], view["invoices"][0]["id"]
        self.allocate([dict(bank_id=b1, invoice_id=i1, amount_cents=100000)])
        self.assert_statistics({"goods": 100000})
        view = self.import_files(bank=self.bank_file([bank_row("SEP", 100000, party, "2026-09-01")]),
                                 invoice=self.invoice_file([invoice_row("I2", 50000, party)]))
        b2, i2 = view["bank"][1]["id"], view["invoices"][1]["id"]
        rows = [dict(bank_id=b2, invoice_id=i2, amount_cents=50000), dict(bank_id=b2, invoice_id=i1, amount_cents=50000)]
        self.assert_rejected_unchanged(lambda: self.allocate(rows))
        self.assert_statistics({"goods": 200000})
        self.mutate("invoice_difference", invoice_id=i1, status="carry_forward", note="已核实跨月抵用")
        view = self.allocate(rows)
        self.assertTrue(all(row["remaining_cents"] == 0 for row in view["bank"] + view["invoices"]))
        self.assert_statistics({"goods": 100000}, {"start_date": "2026-09-01", "end_date": "2026-09-30"})
        aid = next(a["id"] for a in view["allocations"] if a["bank_id"] == b2 and a["invoice_id"] == i1)
        view = self.mutate("undo", allocation_id=aid, note="跨月用途重新核实")
        bank = view["bank"][1]
        self.assertEqual((bank["status"], bank["remaining_cents"]), ("partial", 50000))
        self.assertEqual(bank["related_invoice_notices"][0]["revoke_note"], "跨月用途重新核实")
        self.assert_statistics({"goods": 200000})
        self.mutate("invoice_difference", invoice_id=i1, status="amount_error", note="差额暂不可用")
        view = self.import_files(bank=self.bank_file([bank_row("NORMAL", 55000, party, "2026-10-01")]),
                                 invoice=self.invoice_file([invoice_row("NORMAL", 55000, party)]))
        self.assertEqual(view["bank"][-1]["status"], "matched")
        self.assertEqual(view["invoices"][0]["distributable_cents"], 0)
        self.assert_statistics({"goods": 255000})

    def test_summary_exclusion_note_and_category_override_remain_independent(self):
        row = dict(bank_row("FEE", 100000), summary="网银转账货款")
        salary = dict(bank_row("SALARY", 20000, "员工"), summary="工资与物流费")
        view = self.import_files(bank=self.bank_file([row, salary]), invoice=self.invoice_file([invoice_row(amount=150000)]))
        bid, iid = view["bank"][0]["id"], view["invoices"][0]["id"]
        self.allocate([dict(bank_id=bid, invoice_id=iid, amount_cents=40000)])
        self.mutate("bank_note", bank_id=bid, note="货款分批支付\n不影响分类 <script>")
        view = self.mutate("settings", aliases={}, exclude_special=True, custom_exclude_keywords=["网银"])
        self.assertEqual(view["bank"][0]["status"], "partial")
        self.assertEqual(view["bank"][1]["status"], "excluded")
        self.assert_statistics({"goods": 100000, "other": 20000})
        self.mutate("expense_category", bank_id=view["bank"][1]["id"], category="labor", note="人工工资")
        self.assert_statistics({"goods": 100000, "labor": 20000})
        view = self.mutate("undo", allocation_id=view["allocations"][0]["id"], note="撤回后按摘要规则处理")
        self.assertEqual(view["bank"][0]["status"], "excluded")
        self.assert_statistics({"goods": 100000, "labor": 20000})
        view = self.mutate("settings", aliases={}, exclude_special=True, custom_exclude_keywords=[])
        self.assertEqual(view["bank"][0]["status"], "unmatched")
        self.assertEqual(view["bank"][0]["allocated_cents"], 0)
        self.assertEqual(view["invoices"][0]["distributable_cents"], 0)
        self.assertIn("不影响分类", view["bank"][0]["manual_note"])
        self.assert_statistics({"goods": 100000, "labor": 20000})

    def test_bank_corrected_versions_require_revoke_and_update_statistics_once(self):
        view = self.import_files(bank=self.bank_file([bank_row(amount=10000)]), invoice=self.invoice_file([invoice_row(amount=10000)]))
        bid = view["bank"][0]["id"]
        self.mutate("expense_category", bank_id=bid, category="utilities", note="确认水电采购")
        self.mutate("bank_note", bank_id=bid, note="原始备注")
        changed = dict(bank_row(amount=12000), summary="更正后的物流费")
        view = self.import_files(bank=self.bank_file([changed]))
        cid = view["conflicts"][0]["id"]
        accept = lambda: self.mutate("resolve", conflict_id=cid, action="accept", note="核实新金额")
        self.assert_rejected_unchanged(accept)
        self.assert_statistics({"utilities": 10000})
        self.mutate("undo", allocation_id=view["allocations"][0]["id"], note="更正前撤回金额关联")
        accept()
        self.assert_statistics({"utilities": 12000})
        self.import_files(bank=self.bank_file([changed]))
        self.assert_statistics({"utilities": 12000})
        self.mutate("bank_note", bank_id=bid, note="更正后补充备注")
        view = self.mutate("reverse_conflict", conflict_id=cid, note="撤回更正结论")
        self.assertEqual(view["bank"][0]["amount_cents"], 10000)
        self.assertEqual(view["bank"][0]["manual_note"], "更正后补充备注")
        self.assert_statistics({"utilities": 10000})
        self.mutate("expense_category", bank_id=bid, category="auto")
        self.assert_statistics({"goods": 10000})

    def test_red_invoice_review_reversal_blocks_carry_but_not_other_company(self):
        view = self.import_files(bank=self.bank_file([bank_row("P1", 100000)]), invoice=self.invoice_file([invoice_row("BLUE", 150000)]))
        bid, iid = view["bank"][0]["id"], view["invoices"][0]["id"]
        self.allocate([dict(bank_id=bid, invoice_id=iid, amount_cents=50000)])
        self.mutate("invoice_difference", invoice_id=iid, status="carry_forward", note="允许跨月")
        view = self.import_files(bank=self.bank_file([bank_row("OTHER", 12345, "另一公司")]),
                                 invoice=self.invoice_file([dict(invoice_row("RED", -10000), red=True), invoice_row("OTHER", 12345, "另一公司")]))
        self.assertEqual(view["bank"][1]["status"], "matched")
        remaining = dict(bank_id=bid, invoice_id=iid, amount_cents=50000)
        self.assert_rejected_unchanged(lambda: self.allocate([remaining]))
        red_id = view["invoices"][1]["id"]
        self.mutate("exception", invoice_id=red_id, note="已核实红字票用途")
        view = self.allocate([remaining])
        view = self.mutate("reverse_exception", invoice_id=red_id, note="撤回复核重新确认")
        self.assertEqual(view["bank"][0]["allocated_cents"], 100000)
        self.assertEqual(view["invoices"][0]["distributable_cents"], 0)
        self.assertEqual(view["bank"][1]["status"], "matched")
        self.assert_statistics({"goods": 112345})

    def test_batch_atomicity_stale_requests_and_repeated_saves_preserve_controls(self):
        view = self.import_files(bank=self.bank_file([bank_row("B1", 100000), bank_row("B2", 50000)]),
                                 invoice=self.invoice_file([invoice_row("I1", 150000), invoice_row("I2", 90000)]))
        b1, b2 = [b["id"] for b in view["bank"]]
        i1, i2 = [i["id"] for i in view["invoices"]]
        self.allocate([dict(bank_id=b1, invoice_id=i1, amount_cents=10000)])
        good = dict(bank_id=b2, invoice_id=i2, amount_cents=10000)
        blocked = dict(bank_id=b1, invoice_id=i1, amount_cents=10000)
        for rows in ([good, blocked], [good, dict(good, amount_cents=50000)], [good, good]):
            self.assert_rejected_unchanged(lambda: self.allocate(rows))
            self.assert_statistics({"goods": 150000})
        payload = {"revision": self.ledger()["revision"], "bank_id": b1, "category": "logistics", "note": "过期页"}
        self.mutate("bank_note", bank_id=b1, note="另一个页面先保存")
        self.assert_rejected_unchanged(lambda: self.service.expense_category(payload))
        self.assert_statistics({"goods": 150000})
        submission = {"revision": self.ledger()["revision"], "allocations": [good], "note": "一次有效提交"}
        self.service.review(submission)
        self.assert_rejected_unchanged(lambda: self.service.review(submission))
        self.assertEqual(sum(a["state"] == "active" for a in self.ledger()["allocations"]), 2)
        self.assert_statistics({"goods": 150000})

    def test_backup_restart_month_exports_keep_rules_notes_and_revoke_evidence(self):
        view = self.import_files(bank=self.bank_file([bank_row("AUG", 122400, date="2026-08-06"), bank_row("SEP", 32600, date="2026-09-01")]),
                                 invoice=self.invoice_file([invoice_row(amount=155000)]))
        bid, iid = view["bank"][0]["id"], view["invoices"][0]["id"]
        view = self.allocate([dict(bank_id=bid, invoice_id=iid, amount_cents=122400)])
        self.mutate("undo", allocation_id=view["allocations"][0]["id"], note="错开")
        self.mutate("invoice_difference", invoice_id=iid, status="amount_error", note="等待更正")
        self.mutate("bank_note", bank_id=bid, note="历史备注 <script> & 已追踪")
        self.mutate("expense_category", bank_id=bid, category="logistics", note="确认运费")
        self.mutate("settings", aliases={"付款别名": "测试供应商"}, exclude_special=True, custom_exclude_keywords=["网银手续费"])
        stats = self.assert_statistics({"logistics": 122400, "goods": 32600})
        outputs = []
        for month, expected in (("2026-08", 122400), ("2026-09", 32600), ("", 155000)):
            export = self.mutate("export", month=month)
            path = Path(export["directory"]) / "流水对账结果.csv"
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(sum(int(Decimal(row["支出金额"]) * 100) for row in rows), expected)
            if month != "2026-09":
                row = next(r for r in rows if r["流水编号"] == bid)
                self.assertIn("错开", row["相关发票提示"])
                self.assertEqual(row["累计有效核销金额"], "0.00")
                self.assertEqual(row["支出类别"], "物流")
            outputs.append((path, path.read_bytes()))
        before = self.saved()
        (self.directory / "config").mkdir()
        (self.directory / "config" / "defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        archive = backup_data(self.directory, self.directory / "output")["archive"]
        restored = self.directory / "restored"
        (restored / "config").mkdir(parents=True)
        (restored / "config" / "defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        restore_data(restored, archive)
        other = type(self.service)(restored, self.config)
        self.assertEqual(other.store.path.read_bytes(), before)
        self.assertEqual(other.expense_statistics({}), stats)
        self.assertEqual(other.ledger()["bank"][0]["related_invoice_notices"][0]["revoke_note"], "错开")
        self.service = type(self.service)(self.directory, self.config)
        self.mutate("expense_category", bank_id=bid, category="goods", note="后续更正")
        for path, contents in outputs:
            self.assertEqual(path.read_bytes(), contents)

    def test_seeded_bulk_statistics_with_duplicates_and_independent_date_controls(self):
        randomizer = random.Random(20260909)
        summaries = [("采购货款", "goods"), ("物流费", "logistics"), ("工资", "labor"), ("水电费", "utilities"), ("手续费", "other")]
        rows, expected_rows = [], []
        for index in range(420):
            summary, category = summaries[index % 5]
            at = (date(2023, 12, 1) + timedelta(days=index)).isoformat()
            amount = randomizer.randint(1, 999999)
            row = dict(bank_row("BULK" + str(index), amount, "供应商" + str(index % 7), at), summary=summary)
            if index % 11 == 0:
                row.update(direction="收入", debit_cents=0, credit_cents=amount)
            else:
                expected_rows.append((at, amount, category))
            rows.append(row)
        self.import_files(bank=self.bank_file(rows[:210]))
        view = self.import_files(bank=self.bank_file(rows[140:]))
        self.assertEqual(len(view["bank"]), 420)
        self.import_files(bank=self.bank_file(rows))
        for start, end in (("", ""), ("2024-02-29", "2024-02-29"), ("2024-01-01", "2024-12-31"), ("2024-08-01", ""), ("", "2023-12-31"), ("2030-01-01", "2030-12-31")):
            expected = {}
            for at, amount, category in expected_rows:
                if (not start or at >= start) and (not end or at <= end):
                    expected[category] = expected.get(category, 0) + amount
            with self.subTest(start=start, end=end):
                self.assert_statistics(expected, {"start_date": start, "end_date": end})
        self.assertEqual(len(self.ledger()["bank"]), 420)

    def test_seeded_category_and_note_changes_match_independent_control_ledger(self):
        rows = [bank_row("EDIT" + str(i), 101 + i * 13, "公司" + str(i % 3), f"2026-08-{i + 1:02}") for i in range(18)]
        view = self.import_files(bank=self.bank_file(rows))
        oracle = {b["id"]: (b["debit_cents"], "goods") for b in view["bank"]}
        randomizer = random.Random(98765)
        ids = list(oracle)
        for step in range(64):
            bid = randomizer.choice(ids)
            category = randomizer.choice(["auto", "goods", "logistics", "labor", "utilities", "other"])
            self.mutate("expense_category", bank_id=bid, category=category, note="随机流程分类更正")
            oracle[bid] = (oracle[bid][0], "goods" if category == "auto" else category)
            if step % 5 == 0:
                self.mutate("bank_note", bank_id=bid, note=f"第 {step} 次核对\n不会影响金额")
                self.service = type(self.service)(self.directory, self.config)
            if step % 9 == 0:
                self.import_files(bank=self.bank_file(rows))
            expected = {}
            for amount, kind in oracle.values():
                expected[kind] = expected.get(kind, 0) + amount
            self.assert_statistics(expected)
        self.assertEqual(len(self.ledger()["bank"]), 18)
