import base64
import copy
import io
import json
import sys
import unittest
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reconcile.config import load_config
from reconcile.excel_reader import read_sheets
from reconcile.matching import build_result
from reconcile.normalize import cents, name_key
from reconcile.parsers import parse_bank, parse_invoices
from reconcile.review import validate_decision
from reconcile.storage import SessionStore
from reconcile.export import safe_cell

ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_config(ROOT)
SETTINGS = {"exclude_special": True, "aliases": {}}


def bank(identifier="B1", amount=10000, party="甲公司", **extra):
    return dict({"id": identifier, "date": "2026-08-01", "party": party, "currency": "CNY", "amount_cents": amount, "debit_cents": amount, "credit_cents": 0, "direction": "支出", "summary": "货款", "duplicate": False}, **extra)


def invoice(identifier="I1", amount=10000, party="甲公司", **extra):
    return dict({"id": identifier, "date": "2026-08-05", "party": party, "currency": "CNY", "amount_cents": amount, "red": False, "invalid": "", "number": "26950000000000000001"}, **extra)


class MatchingTests(unittest.TestCase):
    def run_match(self, banks, invoices, settings=None, decisions=None):
        return build_result(banks, invoices, settings or SETTINGS, CONFIG, decisions)

    def test_exact_and_input_immutability(self):
        source = [bank()]
        before = copy.deepcopy(source)
        result = self.run_match(source, [invoice()])
        self.assertEqual(result["bank"][0]["status"], "matched")
        self.assertEqual(source, before)

    def test_different_party_not_automatic(self):
        result = self.run_match([bank()], [invoice(party="乙公司")])
        self.assertEqual(result["bank"][0]["status"], "review")
        self.assertEqual(result["invoices"][0]["bank_ids"], [])

    def test_amount_difference_stays_review(self):
        result = self.run_match([bank(amount=122400)], [invoice(amount=155000)])
        row = result["bank"][0]
        self.assertEqual(row["status"], "review")
        self.assertEqual(row["suggestions"][0]["difference_cents"], 32600)
        with self.assertRaises(ValueError):
            validate_decision(result, {"kind": "match", "bank_ids": ["B1"], "invoice_ids": ["I1"], "note": "金额不等不能确认"})

    def test_one_invoice_not_reused(self):
        result = self.run_match([bank("B1"), bank("B2")], [invoice()])
        self.assertTrue(all(r["status"] == "review" for r in result["bank"]))
        self.assertFalse(result["invoices"][0]["bank_ids"])

    def test_duplicate_invoice_no_automatic_match(self):
        result = self.run_match([bank()], [invoice("I1"), invoice("I2")])
        self.assertEqual(result["bank"][0]["status"], "review")

    def test_red_same_party_blocks_automatic_match(self):
        result = self.run_match([bank()], [invoice(), invoice("I2", amount=-10000, red=True)])
        self.assertEqual(result["bank"][0]["status"], "review")

    def test_red_other_party_never_offsets(self):
        result = self.run_match([bank()], [invoice(), invoice("I2", amount=-10000, red=True, party="乙公司")])
        self.assertEqual(result["bank"][0]["status"], "matched")
        self.assertEqual(result["invoices"][1]["reconciliation_status"], "红字发票待核对")

    def test_void_invoice_not_used(self):
        result = self.run_match([bank()], [invoice(invalid="作废")])
        self.assertEqual(result["bank"][0]["status"], "review")

    def test_many_invoices_proposal_and_manual_confirm(self):
        banks, invoices = [bank()], [invoice("I1", 6000), invoice("I2", 4000)]
        result = self.run_match(banks, invoices)
        proposal = result["bank"][0]["suggestions"][0]
        self.assertEqual(len(proposal["invoice_ids"]), 2)
        decision = validate_decision(result, dict(proposal, kind="match", note="按合同确认"))
        result = self.run_match(banks, invoices, decisions=[decision])
        self.assertEqual(result["bank"][0]["status"], "matched")
        self.assertTrue(all(i["bank_ids"] == ["B1"] for i in result["invoices"]))

    def test_many_payments_proposal(self):
        result = self.run_match([bank("B1", 6000), bank("B2", 4000)], [invoice()])
        self.assertEqual(set(result["bank"][0]["suggestions"][0]["bank_ids"]), {"B1", "B2"})

    def test_income_and_wages_separate(self):
        result = self.run_match([bank("B1", summary="工资"), bank("B2", direction="收入", debit_cents=0, credit_cents=10000), bank("B3", summary="2026年网银服务年费"), bank("B4", summary="2026年网银证书服务年费.用户00001"), bank("B5", summary="网银转账货款")], [invoice()])
        self.assertEqual(result["stats"]["excluded"]["count"], 4)
        self.assertEqual(result["stats"]["excluded"]["debit_cents"], 30000)
        self.assertEqual(result["stats"]["excluded"]["credit_cents"], 10000)
        self.assertIn("网银服务年费", result["bank"][2]["reason"])
        self.assertIn("网银证书服务年费", result["bank"][3]["reason"])
        self.assertEqual(result["bank"][4]["status"], "matched")

    def test_switch_special_rule_off(self):
        for summary in ("工资", "2026年网银服务年费", "2026年网银证书服务年费.用户00001"):
            result = self.run_match([bank(summary=summary)], [], {"exclude_special": False})
            self.assertEqual(result["bank"][0]["status"], "unmatched")

    def test_alias_is_session_specific(self):
        self.assertEqual(self.run_match([bank()], [invoice(party="乙公司")], {"aliases": {"甲公司": "乙公司"}})["bank"][0]["status"], "matched")
        self.assertEqual(self.run_match([bank()], [invoice(party="乙公司")])["bank"][0]["status"], "review")

    def test_currency_mismatch_no_match(self):
        result = self.run_match([bank()], [invoice(currency="USD")])
        self.assertEqual(result["bank"][0]["status"], "unmatched")

    def test_manual_cannot_reuse_invoice(self):
        result = self.run_match([bank("B1"), bank("B2", 20000)], [invoice()])
        with self.assertRaises(ValueError):
            validate_decision(result, {"kind": "match", "bank_ids": ["B2"], "invoice_ids": ["I1"], "note": "不得复用"})

    def test_manual_requires_evidence_and_valid_ids(self):
        result = self.run_match([bank()], [])
        for payload in ({"kind": "exclude", "bank_ids": ["B1"], "note": ""}, {"kind": "exclude", "bank_ids": ["X"], "note": "说明"}):
            with self.assertRaises(ValueError):
                validate_decision(result, payload)


class ParsingTests(unittest.TestCase):
    def test_decimal_and_name_boundaries(self):
        self.assertEqual(cents("1,224.00"), 122400)
        self.assertEqual(cents("（1,550.00）"), -155000)
        self.assertEqual(cents("0.29"), 29)
        self.assertEqual(name_key("甲（深圳） 有限公司"), name_key("甲(深圳)有限公司"))
        self.assertNotEqual(name_key("深圳市甲有限公司"), name_key("甲有限公司"))
        for bad in ("NaN", "abc", "Infinity"):
            with self.assertRaises(ValueError):
                cents(bad)

    def test_invoice_total_and_red_and_duplicate(self):
        rows = [["发票号码", "开票日期", "销方名称", "金额", "价税合计", "红字蓝字"], ["001", "2026-08-01", "甲", 90, 100, "蓝字"], ["002", "2026-08-02", "乙", -90, -100, "红字"], ["合计", "", "", 0, 0], ["001", "2026-08-01", "甲", 90, 100, "蓝字"]]
        parsed, _ = parse_invoices([{"name": "票", "rows": rows}], "票.xlsx", CONFIG)
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[0]["amount_cents"], 10000)
        self.assertIn("重复", parsed[0]["invalid"])
        self.assertTrue(parsed[1]["red"])

    def test_net_amount_cannot_replace_gross(self):
        with self.assertRaises(ValueError):
            parse_invoices([{"name": "票", "rows": [["发票号码", "开票日期", "销方名称", "金额"], ["1", "2026-08-01", "甲", 100]]}], "票.xlsx", CONFIG)

    def test_bad_bank_amount_cannot_silently_disappear(self):
        rows = [["交易日期", "对方户名", "借方发生额", "贷方发生额"], ["20260801", "甲", "乱码金额", ""]]
        with self.assertRaises(ValueError):
            parse_bank([{"name": "流水", "rows": rows}], "流水.xls", CONFIG)

    def test_csv_formula_guard(self):
        self.assertEqual(safe_cell("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(safe_cell("  +cmd"), "'  +cmd")
        self.assertEqual(safe_cell("12345678901234567890"), "'12345678901234567890")

    def test_storage_isolation_and_path_validation(self):
        directory = (ROOT / CONFIG["temp_dir"]).resolve() / ("tests_" + uuid.uuid4().hex)
        store = SessionStore(directory)
        sid = store.new_id()
        store.save({"id": sid, "bank_name": "甲", "invoice_name": "乙", "decisions": []})
        self.assertEqual(store.load(sid)["bank_name"], "甲")
        with self.assertRaises(ValueError):
            store.load("../outside")


if __name__ == "__main__":
    unittest.main(verbosity=2)
