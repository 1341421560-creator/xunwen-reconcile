import copy
import csv
import json
from pathlib import Path
from fault_support import FaultCase, bank_row, invoice_row
from deployment.data_backup import backup_data
from deployment.data_restore import restore_data


class BankFeatureTests(FaultCase):
    def note(self, bid, note):
        return self.service.bank_note({"revision": self.ledger()["revision"], "bank_id": bid, "note": note})

    def category(self, bid, category, note=""):
        return self.service.expense_category({"revision": self.ledger()["revision"], "bank_id": bid, "category": category, "note": note})

    def test_note_save_clear_does_not_change_matching_or_summary(self):
        view = self.import_files(bank=self.bank_file(), invoice=self.invoice_file())
        bid = view["bank"][0]["id"]
        original = copy.deepcopy(view["allocations"])
        view = self.note(bid, "  跟进到货\n下月核实 <script>  ")
        self.assertEqual(view["bank"][0]["manual_note"], "跟进到货\n下月核实 <script>")
        self.assertEqual(view["bank"][0]["summary"], "货款")
        self.assertEqual(view["allocations"], original)
        self.assertEqual(view["bank"][0]["status"], "matched")
        view = self.note(bid, "")
        self.assertEqual(view["bank"][0]["manual_note"], "")
        self.assertEqual(view["audit"][-1]["previous"], "跟进到货\n下月核实 <script>")

    def test_invalid_and_stale_notes_preserve_file(self):
        view = self.import_files(bank=self.bank_file())
        bid = view["bank"][0]["id"]
        for value in (None, [], 123, "a" * 2001):
            self.assert_rejected_unchanged(lambda: self.note(bid, value))
        self.assert_rejected_unchanged(lambda: self.note("unknown", "备注"))
        self.note(bid, "已核对")
        self.assert_rejected_unchanged(lambda: self.service.bank_note({"revision": view["revision"], "bank_id": bid, "note": "过期备注"}))

    def test_statistics_inclusive_range_all_outgoing_once_and_no_income(self):
        rows = [dict(bank_row("G", 10000, date="2026-08-01"), summary="采购货款"),
                dict(bank_row("L", 20000, date="2026-08-31"), summary="物流运费"),
                dict(bank_row("W", 30000, date="2026-08-15"), summary="8月工资"),
                dict(bank_row("U", 40000, date="2026-09-01"), summary="8月水电费"),
                dict(bank_row("O", 50000, date="2026-07-31"), summary="其他费用"),
                dict(bank_row("IN", 60000, date="2026-08-15"), direction="收入", debit_cents=0, credit_cents=60000)]
        self.import_files(bank=self.bank_file(rows), invoice=self.invoice_file([invoice_row(amount=10000)]))
        before = self.saved()
        result = self.service.expense_statistics({"start_date": "2026-08-01", "end_date": "2026-08-31"})
        self.assertEqual(result["debit_cents"], 60000)
        self.assertEqual(result["count"], 3)
        expected = {"goods": 10000, "logistics": 20000, "labor": 30000}
        self.assertEqual({item["id"]: item["debit_cents"] for item in result["categories"]},
                         {item["id"]: expected.get(item["id"], 0) for item in self.config["expense_categories"]})
        self.assertEqual(self.service.expense_statistics({})["debit_cents"], 150000)
        self.assertEqual(self.saved(), before)

    def test_statistics_invalid_ranges_are_rejected_without_write(self):
        self.import_files(bank=self.bank_file())
        for payload in ({"start_date": None}, {"end_date": []}, {"start_date": "2026-02-30"},
                        {"start_date": "20260801"}, {"start_date": "2026-09-01", "end_date": "2026-08-01"}):
            self.assert_rejected_unchanged(lambda: self.service.expense_statistics(payload))

    def test_manual_category_and_automatic_fallback_do_not_change_exclusion(self):
        view = self.import_files(bank=self.bank_file([dict(bank_row(), summary="工资与物流费")]))
        bid = view["bank"][0]["id"]
        self.assertEqual(view["bank"][0]["expense_category"], "other")
        self.assertEqual(view["bank"][0]["status"], "excluded")
        view = self.category(bid, "labor", "确认属于人工支出")
        self.assertEqual(view["bank"][0]["expense_category"], "labor")
        self.assertEqual(view["bank"][0]["status"], "excluded")
        self.assertEqual(self.service.expense_statistics({})["categories"][2]["debit_cents"], 1200000)
        view = self.category(bid, "auto")
        self.assertEqual(view["bank"][0]["expense_category"], "other")

    def test_category_validation_income_and_stale_requests(self):
        view = self.import_files(bank=self.bank_file([bank_row(), dict(bank_row("IN"), direction="收入", debit_cents=0, credit_cents=1200000)]))
        bid = view["bank"][0]["id"]
        for category in (None, [], "missing"):
            self.assert_rejected_unchanged(lambda: self.category(bid, category))
        self.assert_rejected_unchanged(lambda: self.category(bid, "goods", "x" * 1001))
        self.assert_rejected_unchanged(lambda: self.category(view["bank"][1]["id"], "goods"))
        self.category(bid, "logistics")
        self.assert_rejected_unchanged(lambda: self.service.expense_category({"revision": view["revision"], "bank_id": bid, "category": "goods"}))

    def test_note_and_category_survive_reimport_corrected_version_and_reversal(self):
        view = self.import_files(bank=self.bank_file())
        bid = view["bank"][0]["id"]
        self.note(bid, "长期跟进")
        self.category(bid, "logistics", "代付运输费")
        self.import_files(bank=self.bank_file())
        view = self.import_files(bank=self.bank_file([dict(bank_row(), summary="更正货款摘要")]))
        cid = view["conflicts"][0]["id"]
        view = self.service.resolve({"revision": view["revision"], "conflict_id": cid, "action": "accept", "note": "确认更正"})
        self.assertEqual(view["bank"][0]["manual_note"], "长期跟进")
        self.assertEqual(view["bank"][0]["expense_category"], "logistics")
        view = self.service.reverse_conflict({"revision": view["revision"], "conflict_id": cid, "note": "恢复原始摘要"})
        self.assertEqual(view["bank"][0]["summary"], "货款")
        self.assertEqual(view["bank"][0]["manual_note"], "长期跟进")
        self.assertEqual(view["bank"][0]["expense_category"], "logistics")

    def test_compatibility_read_restart_export_backup_restore(self):
        view = self.import_files(bank=self.bank_file())
        bid = view["bank"][0]["id"]
        before = self.saved()
        self.ledger()
        self.assertEqual(self.saved(), before)
        self.note(bid, "交付追踪")
        view = self.category(bid, "utilities")
        self.service = type(self.service)(self.directory, self.config)
        self.assertEqual(self.ledger()["bank"][0]["manual_note"], "交付追踪")
        for month in ("", "2026-08"):
            exported = self.service.export({"revision": view["revision"], "month": month})
            with (Path(exported["directory"]) / "流水对账结果.csv").open(encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual((row["手工备注"], row["支出类别"]), ("交付追踪", "水电费"))
        (self.directory / "config").mkdir()
        (self.directory / "config" / "defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        archive = backup_data(self.directory, self.directory / "output")["archive"]
        target = self.directory / "restored"
        (target / "config").mkdir(parents=True)
        (target / "config" / "defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        restore_data(target, archive)
        restored = type(self.service)(target, self.config)
        self.assertEqual(restored.ledger()["bank"][0]["expense_category"], "utilities")
        self.assertEqual(restored.store.path.read_bytes(), self.saved())

    def test_name_keys_follow_confirmed_alias_without_truncating_company(self):
        view = self.import_files(bank=self.bank_file([bank_row(party="深圳市鸿运星电子商务有限公司")]),
                                 invoice=self.invoice_file([invoice_row(party="深圳市鸿运星电子商务有限公司", amount=1500000), invoice_row("OTHER", 1500000, "其他公司")]))
        self.assertEqual(view["bank"][0]["party_match_key"], view["invoices"][0]["party_match_key"])
        view = self.service.settings({"revision": view["revision"], "aliases": {"深圳市鸿运星电子商务有限公司": "其他公司"}, "exclude_special": True})
        self.assertEqual(view["bank"][0]["party_match_key"], view["invoices"][1]["party_match_key"])
