import copy
import csv
import json
from pathlib import Path
from fault_support import FaultCase, bank_row, invoice_row
from reconcile.ledger_validation import LedgerCorruptionError
from deployment.data_backup import backup_data
from deployment.data_restore import restore_data


class SummaryKeywordTests(FaultCase):
    def settings(self, keywords, built_in=True):
        current = self.ledger()
        return self.service.settings({"revision": current["revision"], "aliases": current["settings"]["aliases"],
                                      "exclude_special": built_in, "custom_exclude_keywords": keywords})

    def fee_row(self, reference="F1", summary="8月份网银手续费", amount=10000, **values):
        return dict(bank_row(reference, amount, **values), summary=summary)

    def test_custom_rules_apply_to_history_and_new_records(self):
        self.import_files(bank=self.bank_file([self.fee_row(), self.fee_row("NORMAL", "采购货款")]))
        view = self.settings(["网银手续费"])
        self.assertEqual(view["bank"][0]["status"], "excluded")
        self.assertEqual(view["bank"][0]["reason"], "自定义摘要命中：网银手续费")
        self.assertEqual(view["bank"][1]["status"], "unmatched")
        view = self.import_files(bank=self.bank_file([self.fee_row("F2", "9月份网银手续费", date="2026-09-03")]))
        self.assertEqual(view["bank"][2]["status"], "excluded")
        self.assertEqual(view["stats"]["excluded"]["count"], 2)

    def test_broad_keyword_matches_every_literal_occurrence(self):
        self.import_files(bank=self.bank_file([self.fee_row(), self.fee_row("PAY", "网银转账货款"), self.fee_row("CASH", "现金货款")]))
        view = self.settings(["网银"])
        self.assertEqual([b["status"] for b in view["bank"]], ["excluded", "excluded", "unmatched"])

    def test_built_in_switch_is_independent_and_precedence_is_stable(self):
        self.import_files(bank=self.bank_file([self.fee_row("ANNUAL", "2026年网银服务年费"), self.fee_row("SALARY", "工资"), self.fee_row()]))
        view = self.settings(["网银", "网银手续费"])
        self.assertEqual(view["bank"][0]["reason"], "摘要命中：网银服务年费")
        self.assertEqual(view["bank"][2]["reason"], "自定义摘要命中：网银")
        view = self.settings(["网银"], built_in=False)
        self.assertEqual(view["bank"][0]["reason"], "自定义摘要命中：网银")
        self.assertEqual(view["bank"][1]["status"], "unmatched")
        self.assertEqual(view["bank"][2]["status"], "excluded")

    def test_normalization_and_literal_spaces_wildcards_and_case(self):
        rows = [self.fee_row("SPACE", "8月网银 服务费"), self.fee_row("NOSPACE", "网银服务费"),
                self.fee_row("WILDCARD", "网银*费"), self.fee_row("PLACEHOLDER", "网银 xxx"),
                self.fee_row("NORMAL", "网银其他费用"), self.fee_row("UPPER", "ABC服务费"), self.fee_row("LOWER", "abc服务费")]
        self.import_files(bank=self.bank_file(rows))
        view = self.settings(["  网银 服务费  ", "", "\n\t", "网银 服务费", "网银*费", "网银 xxx", "ABC"])
        self.assertEqual(view["settings"]["custom_exclude_keywords"], ["网银 服务费", "网银*费", "网银 xxx", "ABC"])
        self.assertEqual([b["status"] for b in view["bank"]], ["excluded", "unmatched", "excluded", "excluded", "unmatched", "excluded", "unmatched"])
        view = self.settings(["", " \t "])
        self.assertEqual(view["settings"]["custom_exclude_keywords"], [])
        self.assertTrue(all(b["status"] == "unmatched" for b in view["bank"]))

    def test_invalid_values_and_stale_submission_do_not_write(self):
        self.import_files(bank=self.bank_file([self.fee_row()]))
        for value in (None, "网银", {}, True, 123, [None], [1], [False], [[]]):
            with self.subTest(value=value):
                self.assert_rejected_unchanged(lambda: self.settings(value))
        before = self.ledger()["revision"]
        self.settings(["网银"])
        self.assert_rejected_unchanged(lambda: self.service.settings({"revision": before, "aliases": {}, "exclude_special": True, "custom_exclude_keywords": []}))

    def test_old_client_preserves_keywords_and_empty_list_clears(self):
        self.import_files(bank=self.bank_file([self.fee_row()]))
        self.settings(["网银手续费"])
        view = self.service.settings({"revision": self.ledger()["revision"], "aliases": {}, "exclude_special": False})
        self.assertEqual(view["settings"]["custom_exclude_keywords"], ["网银手续费"])
        self.assertEqual(view["bank"][0]["status"], "excluded")
        view = self.settings([], built_in=False)
        self.assertEqual(view["bank"][0]["status"], "unmatched")

    def test_removal_releases_matching_but_keeps_other_exclusions(self):
        self.settings(["网银"])
        view = self.import_files(bank=self.bank_file([self.fee_row("FEE", amount=10000), self.fee_row("ANNUAL", "网银服务年费", 20000), self.fee_row("MANUAL", "网银其他费用", 30000)]),
                                 invoice=self.invoice_file([invoice_row(amount=10000)]))
        self.assertEqual(view["allocations"], [])
        self.service.exclusion({"revision": view["revision"], "bank_id": view["bank"][2]["id"], "excluded": True, "note": "人工确认不参与"})
        view = self.settings([])
        self.assertEqual([b["status"] for b in view["bank"]], ["matched", "excluded", "excluded"])
        self.assertEqual(view["bank"][2]["reason"], "人工确认不参与")
        self.assertEqual(len(view["allocations"]), 1)

    def test_manual_exclusion_and_income_remain_authoritative(self):
        incoming = dict(self.fee_row("IN"), direction="收入", debit_cents=0, credit_cents=10000)
        view = self.import_files(bank=self.bank_file([self.fee_row("MANUAL", "网银服务年费"), incoming]))
        self.service.exclusion({"revision": view["revision"], "bank_id": view["bank"][0]["id"], "excluded": True, "note": "人工费用分类依据"})
        view = self.settings(["网银"])
        self.assertEqual(view["bank"][0]["reason"], "人工费用分类依据")
        self.assertEqual(view["bank"][1]["reason"], "收入不参与进项发票比对")

    def test_existing_allocations_and_difference_guards_survive_rule_change(self):
        view = self.import_files(bank=self.bank_file([self.fee_row("FULL", amount=100000, party="已匹配公司"), self.fee_row("PART", amount=100000, party="部分匹配公司")]),
                                 invoice=self.invoice_file([invoice_row("I1", 100000, "已匹配公司"), invoice_row("I2", 150000, "部分匹配公司")]))
        row = dict(bank_id=view["bank"][1]["id"], invoice_id=view["invoices"][1]["id"], amount_cents=40000)
        self.allocate([row])
        original = copy.deepcopy(self.ledger()["allocations"])
        view = self.settings(["网银"])
        self.assertEqual([b["status"] for b in view["bank"]], ["matched", "partial"])
        self.assertEqual(view["allocations"], original)
        self.assertEqual(view["invoices"][1]["difference_status"], "pending")
        self.assert_rejected_unchanged(lambda: self.allocate([dict(row, amount_cents=60000)]))
        self.service.invoice_difference({"revision": view["revision"], "invoice_id": row["invoice_id"], "status": "carry_forward", "note": "确认剩余金额继续使用"})
        view = self.allocate([dict(row, amount_cents=60000)])
        self.assertEqual(view["bank"][1]["status"], "matched")

    def test_full_revoke_makes_payment_subject_to_rules_again(self):
        view = self.import_files(bank=self.bank_file([self.fee_row(amount=10000)]), invoice=self.invoice_file([invoice_row(amount=10000)]))
        view = self.settings(["网银"])
        self.assertEqual(view["bank"][0]["status"], "matched")
        view = self.service.undo({"revision": view["revision"], "allocation_id": view["allocations"][0]["id"], "note": "撤回后按费用处理"})
        self.assertEqual(view["bank"][0]["status"], "excluded")

    def test_old_ledger_read_does_not_write_and_corrupt_list_is_rejected(self):
        self.import_files(bank=self.bank_file([self.fee_row()]))
        legacy = json.loads(self.saved())
        legacy["settings"].pop("custom_exclude_keywords")
        self.service.store.path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        before = self.saved()
        self.assertEqual(self.ledger()["settings"].get("custom_exclude_keywords", []), [])
        self.assertEqual(self.saved(), before)
        self.service.settings({"revision": self.ledger()["revision"], "aliases": {}, "exclude_special": True})
        self.assertEqual(json.loads(self.saved())["settings"]["custom_exclude_keywords"], [])
        broken = json.loads(self.saved())
        broken["settings"]["custom_exclude_keywords"] = "网银"
        self.service.store.path.write_text(json.dumps(broken), encoding="utf-8")
        before = self.saved()
        with self.assertRaises(LedgerCorruptionError):
            self.ledger()
        self.assertEqual(self.saved(), before)

    def test_export_and_audit_include_keyword_source(self):
        self.import_files(bank=self.bank_file([self.fee_row()]))
        view = self.settings(["网银手续费"])
        event = next(e for e in reversed(view["audit"]) if e["action"] == "settings")
        self.assertEqual(event["previous"]["custom_exclude_keywords"], [])
        self.assertEqual(event["current"]["custom_exclude_keywords"], ["网银手续费"])
        for month in ("", "2026-08"):
            result = self.service.export({"revision": view["revision"], "month": month})
            with (Path(result["directory"]) / "流水对账结果.csv").open(encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["状态"], "不参与进项比对")
            self.assertEqual(row["依据"], "自定义摘要命中：网银手续费")

    def test_restart_backup_restore_and_company_isolation(self):
        self.import_files(bank=self.bank_file([self.fee_row()]))
        self.settings(["网银手续费"])
        self.service = type(self.service)(self.directory, self.config)
        before = self.saved()
        (self.directory / "config").mkdir()
        (self.directory / "config" / "defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        archive = backup_data(self.directory, self.directory / "output")["archive"]
        target = self.directory / "restored"
        (target / "config").mkdir(parents=True)
        (target / "config" / "defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        restore_data(target, archive)
        other = type(self.service)(target, self.config)
        self.assertEqual(other.store.path.read_bytes(), before)
        self.assertEqual(other.ledger()["settings"]["custom_exclude_keywords"], ["网银手续费"])
        self.assertEqual(other.ledger()["bank"][0]["status"], "excluded")
        separate = type(self.service)(self.directory / "separate", self.config)
        self.assertEqual(separate.ledger()["settings"]["custom_exclude_keywords"], [])
