import copy
import csv
import json
from pathlib import Path
from unittest.mock import patch
from fault_support import FaultCase, bank_row, invoice_row
from reconcile.auto_matching import auto_match
from reconcile.ledger_progress import ledger_view
from reconcile.invoice_notices import annotate_invoice_notices


class InvoiceNoticeTests(FaultCase):
    def seed(self, revoked=False):
        self.party = "深圳市富芯通电子有限公司"
        view = self.import_files(bank=self.bank_file([bank_row(amount=122400, party=self.party)]),
                                 invoice=self.invoice_file([invoice_row("26952000003348792166", 155000, self.party)]))
        self.bid, self.iid = view["bank"][0]["id"], view["invoices"][0]["id"]
        view = self.allocate([dict(bank_id=self.bid, invoice_id=self.iid, amount_cents=122400)])
        if revoked:
            view = self.service.undo({"revision": view["revision"], "allocation_id": view["allocations"][0]["id"], "note": "错开"})
        return view

    def change(self, status):
        return self.service.invoice_difference({"revision": self.ledger()["revision"], "invoice_id": self.iid, "status": status, "note": "隔离测试：确认处理方式"})

    def test_fuxintong_revoked_invoice_visible_but_still_blocked(self):
        view = self.seed(True)
        bank, invoice = view["bank"][0], view["invoices"][0]
        self.assertEqual((bank["status"], bank["allocated_cents"], bank["remaining_cents"]), ("unmatched", 0, 122400))
        self.assertEqual(bank["reason"], "相关发票已导入，当前暂停分配")
        self.assertEqual(bank["candidate_ids"], [])
        item, = bank["related_invoice_notices"]
        self.assertEqual((item["relation_source"], item["revoke_note"], item["remaining_cents"]), ("revoked", "错开", 155000))
        self.assertIn("曾关联发票：1,550.00 元", item["summary"])
        self.assertIn("付款未核销：1,224.00 元", bank["related_invoice_hint"])
        self.assertIn("发票未分配余额：1,550.00 元", bank["related_invoice_hint"])
        self.assertEqual(invoice["distributable_cents"], 0)
        self.assert_rejected_unchanged(lambda: self.allocate([dict(bank_id=self.bid, invoice_id=self.iid, amount_cents=122400)]))
        before = self.saved()
        self.assertEqual(auto_match(self.service.store.load(), self.config), [])
        self.assertEqual(self.saved(), before)

    def test_active_association_precedes_revoked_and_same_party_without_duplicates(self):
        self.seed(True)
        self.change("carry_forward")
        self.allocate([dict(bank_id=self.bid, invoice_id=self.iid, amount_cents=122400)])
        view = self.change("pending")
        item, = view["bank"][0]["related_invoice_notices"]
        self.assertEqual(item["relation_source"], "active")
        self.assertEqual(item["revoke_note"], "")
        self.assertEqual(item["remaining_cents"], 32600)
        self.assertEqual(view["bank"][0]["status"], "matched")

    def test_latest_revocation_uses_audit_order_when_timestamps_tie(self):
        self.seed(True)
        self.change("carry_forward")
        row = dict(bank_id=self.bid, invoice_id=self.iid, amount_cents=40000)
        self.allocate([row])
        view = self.allocate([row])
        first, second = view["allocations"][-2:]
        with patch("reconcile.allocations.timestamp", return_value="2099-01-01T00:00:00+08:00"):
            self.service.undo({"revision": self.ledger()["revision"], "allocation_id": second["id"], "note": "先撤回后建关联"})
            view = self.service.undo({"revision": self.ledger()["revision"], "allocation_id": first["id"], "note": "最新撤回依据"})
        item, = view["bank"][0]["related_invoice_notices"]
        self.assertEqual(item["revoke_note"], "最新撤回依据")
        self.assertEqual(len(view["allocations"]), 3)

    def test_same_party_scope_respects_alias_currency_income_and_exclusion(self):
        self.seed()
        rows = [bank_row("LATER", 10000, "富芯通付款别名"), bank_row("OTHER", 10000, "另一家公司"),
                dict(bank_row("WAGE", 10000, self.party), summary="工资"),
                dict(bank_row("IN", 10000, self.party), direction="收入", debit_cents=0, credit_cents=10000)]
        view = self.import_files(bank=self.bank_file(rows))
        self.assertEqual(view["bank"][1]["related_invoice_notices"], [])
        view = self.service.settings({"revision": view["revision"], "aliases": {"富芯通付款别名": self.party}, "exclude_special": True})
        item, = view["bank"][1]["related_invoice_notices"]
        self.assertEqual(item["relation_source"], "same_party")
        self.assertIn("尚未确认", view["bank"][1]["related_invoice_hint"])
        self.assertTrue(all(not bank["related_invoice_notices"] for bank in view["bank"][2:]))
        banks = copy.deepcopy(view["bank"])
        banks[1]["currency"] = "USD"
        annotate_invoice_notices(banks, view["invoices"], view["allocations"], view["audit"], self.config)
        self.assertEqual(banks[1]["related_invoice_notices"], [])

    def test_notice_priority_for_multiple_invoices_is_active_revoked_then_company(self):
        self.seed(True)
        view = self.import_files(bank=self.bank_file([bank_row("SECOND", 20000, self.party)]),
                                 invoice=self.invoice_file([invoice_row("ACTIVE", 50000, self.party), invoice_row("COMPANY", 80000, self.party)]))
        active, company = view["invoices"][1:]
        self.allocate([dict(bank_id=self.bid, invoice_id=active["id"], amount_cents=10000),
                       dict(bank_id=view["bank"][1]["id"], invoice_id=company["id"], amount_cents=10000)])
        bank = self.ledger()["bank"][0]
        self.assertEqual([item["relation_source"] for item in bank["related_invoice_notices"]], ["active", "revoked", "same_party"])
        self.assertEqual(len({item["invoice_id"] for item in bank["related_invoice_notices"]}), 3)

    def test_carry_confirmation_removes_warning_without_restoring_allocations(self):
        self.seed(True)
        view = self.change("carry_forward")
        bank = view["bank"][0]
        self.assertEqual(bank["related_invoice_notices"], [])
        self.assertEqual(bank["related_invoice_hint"], "")
        self.assertIn(self.iid, bank["candidate_ids"])
        self.assertEqual(bank["allocated_cents"], 0)
        self.assertTrue(all(item["state"] == "revoked" for item in view["allocations"]))
        view = self.change("amount_error")
        self.assertEqual(view["bank"][0]["related_invoice_notices"][0]["difference_label"], "开票金额有误")
        self.assertEqual(view["bank"][0]["candidate_ids"], [])

    def test_notice_does_not_change_status_statistics_or_higher_priority_reasons(self):
        self.seed(True)
        view = self.import_files(invoice=self.invoice_file([dict(invoice_row("26952000003348792166", 155000, self.party), source_status="作废")]))
        self.assertEqual(view["bank"][0]["status"], "review")
        self.assertIn("冲突", view["bank"][0]["reason"])
        ledger = self.service.store.load()
        with patch("reconcile.ledger_progress.annotate_invoice_notices"):
            baseline = ledger_view(ledger, self.config)
        current = ledger_view(ledger, self.config)
        self.assertEqual(current["stats"], baseline["stats"])
        for key in ("status", "allocated_cents", "remaining_cents", "candidate_ids", "invoice_ids", "hold_reasons"):
            self.assertEqual(current["bank"][0][key], baseline["bank"][0][key])
        self.assertEqual(current["allocations"], baseline["allocations"])

    def test_old_ledger_read_and_normal_save_never_persist_derived_notices(self):
        self.seed()
        legacy = json.loads(self.saved())
        legacy["invoices"][0].pop("difference")
        self.service.store.path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        before = self.saved()
        view = self.ledger()
        self.assertEqual(view["bank"][0]["related_invoice_notices"][0]["relation_source"], "active")
        self.assertEqual(self.saved(), before)
        self.service.bank_note({"revision": view["revision"], "bank_id": self.bid, "note": "后续正常保存"})
        self.assertNotIn("related_invoice_notices", json.loads(self.saved())["bank"][0])
        self.service = type(self.service)(self.directory, self.config)
        self.assertEqual(self.ledger()["bank"][0]["related_invoice_notices"][0]["remaining_cents"], 32600)

    def test_exports_keep_notice_amounts_and_original_files(self):
        view = self.seed(True)
        outputs = []
        for month in ("", "2026-08"):
            exported = self.service.export({"revision": view["revision"], "month": month})
            directory = Path(exported["directory"])
            path = directory / "流水对账结果.csv"
            with path.open(encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["相关发票提示"], view["bank"][0]["related_invoice_hint"])
            self.assertEqual((row["累计有效核销金额"], row["剩余金额"]), ("0.00", "1224.00"))
            snapshot = json.loads((directory / "完整对账快照.json").read_text(encoding="utf-8"))
            self.assertEqual(snapshot["bank"][0]["related_invoice_notices"], view["bank"][0]["related_invoice_notices"])
            outputs.append((path, path.read_bytes()))
        self.change("carry_forward")
        for path, original in outputs:
            self.assertEqual(path.read_bytes(), original)

    def test_unrelated_or_no_restricted_invoice_keeps_original_no_invoice_reason(self):
        self.seed()
        view = self.import_files(bank=self.bank_file([bank_row("OTHER", 66600, "其他供应商")]))
        bank = view["bank"][-1]
        self.assertEqual(bank["related_invoice_notices"], [])
        self.assertEqual(bank["reason"], "累计账本暂未找到对应发票")
