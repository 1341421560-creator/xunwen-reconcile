import base64
import copy
import csv
import json
import os
import shutil
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reconcile.config import load_config
from reconcile.service import ReconciliationService
from reconcile.migration import migrate_legacy
from reconcile.ledger_storage import LedgerStore
from reconcile.ledger_model import new_ledger
from reconcile.identity_dedup import ingest
from reconcile.auto_matching import auto_match
from reconcile.ledger_progress import ledger_view
from ledger_fixtures import bank_row, invoice_row, upload

ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_config(ROOT)
TEST_ROOT = (ROOT / CONFIG["temp_dir"]).resolve() / ("ledger-tests-" + uuid.uuid4().hex)
TEST_ROOT.mkdir(parents=True, exist_ok=True)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = TEST_ROOT / self.id().split(".")[-1]
        self.config = dict(CONFIG, temp_dir="temp")
        self.service = ReconciliationService(self.directory, self.config)

    def load(self):
        return self.service.ledger()

    def import_rows(self, banks=None, invoices=None, company=None):
        payload = {"revision": self.load()["revision"]}
        for kind, rows in (("bank", banks), ("invoice", invoices)):
            if rows is not None:
                payload[kind] = upload(rows, kind, company or CONFIG["company_name"])
        return self.service.import_files(payload)

    def allocate(self, rows, revision=None):
        return self.service.review({"revision": self.load()["revision"] if revision is None else revision,
                                    "allocations": rows, "note": "按合同及送货单确认"})

    def test_01_cross_month_invoice_only_updates_august(self):
        self.import_rows([bank_row()])
        result = self.import_rows(invoices=[invoice_row()])
        self.assertEqual(result["bank"][0]["status"], "matched")
        self.assertEqual(result["last_import"]["historical_matched_count"], 1)
        view = self.service.ledger({"month": "2026-08"})
        self.assertEqual(view["filtered_stats"]["matched"]["count"], 1)
        self.assertEqual(view["invoices"][0]["date"], "2026-09-03")

    def test_02_partial_then_remaining_auto_and_restart(self):
        result = self.import_rows([bank_row(amount=5000000)])
        self.assertEqual(result["bank"][0]["status"], "unmatched")
        result = self.import_rows(invoices=[invoice_row(amount=2000000)])
        bid, iid = result["bank"][0]["id"], result["invoices"][0]["id"]
        result = self.allocate([dict(bank_id=bid, invoice_id=iid, amount_cents=2000000)])
        self.assertEqual(result["bank"][0]["status"], "partial")
        self.assertEqual(result["bank"][0]["remaining_cents"], 3000000)
        result = self.import_rows(invoices=[invoice_row("N2", 3000000, date="2026-10-05")])
        self.assertEqual(result["bank"][0]["status"], "matched")
        self.assertEqual(len(result["allocations"]), 2)
        self.service = ReconciliationService(self.directory, self.config)
        self.assertEqual(self.load()["allocations"], result["allocations"])
        self.assertEqual(self.load()["audit"], result["audit"])

    def test_03_duplicate_amount_ambiguity_no_fifo(self):
        result = self.import_rows([bank_row("A"), bank_row("B", date="2026-09-01")], [invoice_row()])
        self.assertEqual([b["status"] for b in result["bank"]], ["review", "review"])
        self.assertEqual(result["allocations"], [])

    def test_04_duplicate_file_overlap_and_invoice_code(self):
        result = self.import_rows([bank_row()], [invoice_row()])
        ids = [r["id"] for r in result["bank"] + result["invoices"]]
        repeated = self.import_rows([bank_row(), bank_row("R2", 77700)], [invoice_row(), invoice_row("N2", 77700)])
        self.assertEqual(len(repeated["bank"]), 2)
        self.assertEqual(len(repeated["invoices"]), 2)
        self.assertEqual(repeated["bank"][0]["id"], ids[0])
        self.assertEqual(repeated["last_import"]["counts"]["bank"], {"new": 1, "duplicate": 1, "conflict": 0})
        other_code = invoice_row()
        other_code["code"] = "different-code"
        self.assertEqual(len(self.import_rows(invoices=[other_code])["invoices"]), 3)

    def test_05_many_to_many_and_overallocation_atomic(self):
        result = self.import_rows([bank_row("A", 600000), bank_row("B", 400000)], [invoice_row("N1", 700000), invoice_row("N2", 300000)])
        b1, b2 = [b["id"] for b in result["bank"]]
        i1, i2 = [i["id"] for i in result["invoices"]]
        before = self.service.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.allocate([dict(bank_id=b1, invoice_id=i1, amount_cents=600000), dict(bank_id=b2, invoice_id=i1, amount_cents=400000)])
        self.assertEqual(self.service.store.path.read_bytes(), before)
        result = self.allocate([dict(bank_id=b1, invoice_id=i1, amount_cents=600000), dict(bank_id=b2, invoice_id=i1, amount_cents=100000), dict(bank_id=b2, invoice_id=i2, amount_cents=300000)])
        self.assertTrue(all(b["remaining_cents"] == 0 for b in result["bank"]))
        self.assertTrue(all(i["remaining_cents"] == 0 for i in result["invoices"]))

    def test_06_stale_page_and_pair_reuse_rejected(self):
        result = self.import_rows([bank_row(amount=500000)], [invoice_row(amount=200000)])
        row = dict(bank_id=result["bank"][0]["id"], invoice_id=result["invoices"][0]["id"], amount_cents=100000)
        self.allocate([row])
        with self.assertRaisesRegex(ValueError, "版本"):
            self.allocate([row], revision=result["revision"])
        with self.assertRaisesRegex(ValueError, "重复"):
            self.allocate([dict(row, amount_cents=50000), dict(row, amount_cents=50000)])
        self.assertEqual(self.load()["bank"][0]["allocated_cents"], 100000)
        self.allocate([row])
        with self.assertRaisesRegex(ValueError, "超过"):
            self.allocate([dict(row, amount_cents=1)])

    def test_07_undo_blocks_automatic_after_restart_reimport(self):
        result = self.import_rows([bank_row()], [invoice_row()])
        a = result["allocations"][0]
        self.service.undo({"revision": result["revision"], "allocation_id": a["id"], "note": "关联有误，暂不重配"})
        self.service = ReconciliationService(self.directory, self.config)
        result = self.import_rows([bank_row()], [invoice_row()])
        self.assertEqual(result["bank"][0]["allocated_cents"], 0)
        self.assertEqual(len(result["allocations"]), 1)
        result = self.allocate([{k: a[k] for k in ("bank_id", "invoice_id", "amount_cents")}])
        self.assertEqual(result["bank"][0]["status"], "matched")

    def test_08_status_conflict_preserves_allocation_and_requires_undo(self):
        result = self.import_rows([bank_row()], [invoice_row()])
        changed = invoice_row()
        changed["source_status"] = "作废"
        result = self.import_rows(invoices=[changed])
        self.assertEqual(result["bank"][0]["status"], "review")
        self.assertEqual(result["bank"][0]["allocated_cents"], 1200000)
        self.assertEqual(result["invoices"][0]["source_status"], "正常")
        cid = result["conflicts"][0]["id"]
        with self.assertRaisesRegex(ValueError, "撤回"):
            self.service.resolve({"revision": result["revision"], "conflict_id": cid, "action": "accept", "note": "发票已作废"})
        self.service.undo({"revision": result["revision"], "allocation_id": result["allocations"][0]["id"], "note": "已核验作废"})
        result = self.service.resolve({"revision": self.load()["revision"], "conflict_id": cid, "action": "accept", "note": "采用税务清单新状态"})
        self.assertEqual(result["invoices"][0]["source_status"], "作废")
        self.assertEqual(result["invoices"][0]["allocated_cents"], 0)
        self.assertEqual(result["bank"][0]["status"], "review")

    def test_09_red_invoice_never_offsets_and_review_evidence(self):
        red = invoice_row("RED", -1200000)
        red["red"] = True
        result = self.import_rows([bank_row()], [invoice_row(), red])
        self.assertEqual(result["bank"][0]["status"], "review")
        self.assertFalse(result["allocations"])
        result = self.service.exception({"revision": result["revision"], "invoice_id": result["invoices"][1]["id"], "note": "此红票对应其他支付账户，已核验"})
        result = self.import_rows(invoices=[invoice_row()])
        self.assertEqual(result["bank"][0]["status"], "matched")
        self.assertEqual(result["invoices"][1]["status"], "review")

    def test_10_missing_reference_requires_resolution(self):
        file = upload([bank_row("")], "bank", CONFIG["company_name"])
        result = self.service.import_files({"revision": 0, "bank": file})
        result = self.service.import_files({"revision": result["revision"], "bank": file})
        self.assertEqual(len(result["bank"]), 1)
        self.assertEqual(result["bank"][0]["status"], "review")
        cid = result["conflicts"][0]["id"]
        result = self.service.resolve({"revision": result["revision"], "conflict_id": cid, "action": "new", "note": "确为两笔不同交易，银行缺少流水号"})
        self.assertEqual(len(result["bank"]), 2)
        repeated = self.service.import_files({"revision": result["revision"], "bank": file})
        self.assertEqual(len(repeated["bank"]), 2)
        self.assertEqual(sum(c["state"] == "pending" for c in repeated["conflicts"]), 0)

    def test_11_bad_second_file_rolls_back_both(self):
        self.import_rows([bank_row()])
        before = self.service.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.service.import_files({"revision": self.load()["revision"], "bank": upload([bank_row("R2")], "bank", CONFIG["company_name"]), "invoice": {"name": "bad.xlsx", "content": base64.b64encode(b"not-excel").decode("ascii")}})
        self.assertEqual(self.service.store.path.read_bytes(), before)

    def test_12_foreign_company_stops_and_optional_files(self):
        for kind in ("bank", "invoice"):
            with self.assertRaises(ValueError):
                self.import_rows(**({"banks": [bank_row()]} if kind == "bank" else {"invoices": [invoice_row()]}), company="另一家公司")
        with self.assertRaisesRegex(ValueError, "至少"):
            self.import_rows()
        self.assertEqual(self.load()["revision"], 0)

    def test_13_replace_failure_preserves_original(self):
        self.import_rows([bank_row()])
        before = self.service.store.path.read_bytes()
        with patch("reconcile.ledger_storage.os.replace", side_effect=OSError("模拟磁盘写入失败")):
            with self.assertRaises(OSError):
                self.import_rows(invoices=[invoice_row()])
        self.assertEqual(self.service.store.path.read_bytes(), before)
        self.assertEqual(self.import_rows(invoices=[invoice_row()])["stats"]["invoice_count"], 1)

    def test_14_competing_store_revision_conflict(self):
        self.import_rows([bank_row()])
        old = self.service.store.load()
        other = LedgerStore(self.directory / "ledger", self.directory / "temp", CONFIG["company_name"])
        self.import_rows(invoices=[invoice_row()])
        with self.assertRaisesRegex(ValueError, "版本"):
            other.commit(old, old["revision"])

    def test_15_rule_changes_do_not_reallocate_existing(self):
        result = self.import_rows([bank_row()], [invoice_row()])
        ids = result["allocations"]
        result = self.service.settings({"revision": result["revision"], "aliases": {"测试供应商": "另一开票名称"}, "exclude_special": False})
        self.assertEqual(result["allocations"], ids)
        self.assertEqual(result["bank"][0]["status"], "matched")

    def test_16_cross_year_and_cny_only(self):
        result = self.import_rows([bank_row(date="2026-08-03")], [invoice_row(date="2027-01-01")])
        self.assertEqual(result["bank"][0]["status"], "matched")

    def test_17_exports_separate_face_and_effective_amount(self):
        result = self.import_rows([bank_row("A", 600000), bank_row("B", 400000, date="2026-09-01")], [invoice_row(amount=1000000)])
        iid = result["invoices"][0]["id"]
        result = self.allocate([dict(bank_id=b["id"], invoice_id=iid, amount_cents=b["amount_cents"]) for b in result["bank"]])
        exported = self.service.export({"revision": result["revision"], "month": "2026-08"})
        folder = Path(exported["directory"])
        with (folder / "逐条金额分配明细.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["票面金额（不可按本表求和）"], "10000.00")
        self.assertEqual(rows[0]["有效核销金额（可求和）"], "6000.00")
        before = (folder / "完整对账快照.json").read_bytes()
        self.import_rows([bank_row("C", 99900)])
        self.assertEqual((folder / "完整对账快照.json").read_bytes(), before)

    def test_18_ten_thousand_historical_and_new_invoices(self):
        start = time.perf_counter()
        banks = [bank_row(f"R{n}", 10000 + n, f"容量供应商{n:05d}") for n in range(10000)]
        invoices = [invoice_row(f"N{n}", 10000 + n, f"容量供应商{n:05d}") for n in range(10000)]
        self.import_rows(banks)
        result = self.import_rows(invoices=invoices)
        self.assertEqual(result["stats"]["matched"]["count"], 10000)
        self.assertEqual(result["last_import"]["historical_matched_count"], 10000)
        self.assertEqual(len(result["allocations"]), 10000)
        self.assertEqual(result["bank"][-1]["remaining_cents"], 0)
        elapsed = time.perf_counter() - start
        (self.directory / "performance.json").write_text(json.dumps({"historical_banks": 10000, "new_invoices": 10000, "matched": 10000, "seconds": round(elapsed, 3)}), encoding="utf-8")

    @unittest.skipUnless(os.environ.get("RECON_BANK_SOURCE") and os.environ.get("RECON_INVOICE_SOURCE"), "提供原始文件路径后验证真实迁移")
    def test_19_migration_interrupt_idempotence_and_real_baseline(self):
        for path in (ROOT / "sessions").glob("*.json"):
            shutil.copy2(path, self.directory / "sessions" / path.name)
        bank, invoice = os.environ["RECON_BANK_SOURCE"], os.environ["RECON_INVOICE_SOURCE"]
        with patch("reconcile.ledger_storage.os.replace", side_effect=OSError("模拟迁移中断")):
            with self.assertRaises(OSError):
                migrate_legacy(self.service, bank, invoice)
        self.assertFalse(self.service.store.path.exists())
        result = migrate_legacy(self.service, bank, invoice)
        self.assertEqual(result["stats"]["bank_count"], 18)
        self.assertEqual(result["stats"]["invoice_count"], 16)
        for k, n in (("matched", 4), ("unmatched", 3), ("review", 1), ("excluded", 10), ("partial", 0)):
            self.assertEqual(result["stats"][k]["count"], n)
        again = migrate_legacy(self.service, bank, invoice)
        self.assertTrue(again["migration_skipped"])
        self.assertEqual(again["revision"], result["revision"])

    def test_20_same_reference_different_account_not_deduplicated(self):
        ledger = new_ledger(CONFIG["company_name"])
        rows = [bank_row(), dict(bank_row(), account="another-account")]
        counts = ingest(ledger, {"bank": rows, "invoices": []}, "batch")
        self.assertEqual(counts["bank"]["new"], 2)

    def test_21_repeated_invalid_invoice_does_not_add_amount(self):
        item = invoice_row(number="")
        file = upload([item], "invoice", CONFIG["company_name"])
        result = self.service.import_files({"revision": 0, "invoice": file})
        result = self.service.import_files({"revision": result["revision"], "invoice": file})
        self.assertEqual(len(result["invoices"]), 1)
        self.assertEqual(result["invoices"][0]["status"], "review")
        self.assertEqual(result["last_import"]["counts"]["invoices"]["duplicate"], 1)

    def test_22_concurrent_writer_lock_rejects(self):
        self.import_rows([bank_row()])
        other = LedgerStore(self.directory / "ledger", self.directory / "temp", CONFIG["company_name"])
        with self.service.store.exclusive():
            with self.assertRaisesRegex(ValueError, "正在保存"):
                with other.exclusive():
                    self.fail("第二个写入者不能获得锁")

    def test_23_manual_amount_is_saved_exactly_as_previewed(self):
        result = self.import_rows([bank_row()], [invoice_row()])
        a = result["allocations"][0]
        self.service.undo({"revision": result["revision"], "allocation_id": a["id"], "note": "改为分批确认"})
        row = {k: a[k] for k in ("bank_id", "invoice_id")}
        result = self.allocate([dict(row, amount_cents=500000)])
        self.assertEqual(result["bank"][0]["allocated_cents"], 500000)
        result = self.allocate([dict(row, amount_cents=700000)])
        self.assertEqual(result["bank"][0]["status"], "matched")

    @unittest.skipUnless(os.environ.get("RECON_CAPACITY_DIR"), "提供固定模板容量验证目录后测试累计导入")
    def test_24_fixed_templates_paged_cumulative_import(self):
        directory = Path(os.environ["RECON_CAPACITY_DIR"])
        for kind, name in (("bank", "bank_1000_paged.xls"), ("invoice", "invoice_1000_paged.xlsx")):
            file = directory / name
            result = self.service.import_files({"revision": self.load()["revision"], kind: {"name": file.name, "content": base64.b64encode(file.read_bytes()).decode("ascii")}})
        self.assertEqual(result["stats"]["bank_count"], 1000)
        self.assertEqual(result["stats"]["invoice_count"], 1000)
        self.assertEqual(result["stats"]["matched"]["count"], 800)
        self.assertEqual(result["stats"]["excluded"]["count"], 200)
        self.assertEqual(result["last_import"]["historical_matched_count"], 800)


if __name__ == "__main__":
    unittest.main(verbosity=2)
