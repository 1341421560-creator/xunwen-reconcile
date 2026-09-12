import copy
import csv
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from fault_support import FaultCase, bank_row, invoice_row, upload, ROOT
from reconcile.ledger_validation import validate_ledger
from reconcile.ledger_progress import ledger_view
from reconcile.invoice_offsets import checked_pair
from reconcile.company_registry import CompanyRegistry
from reconcile.request_router import RequestRouter
from reconcile.config import load_config
from reconcile.atomic_write import replace_snapshot
from deployment.data_backup import backup_data
from deployment.data_restore import restore_data
from deployment.company_transfer import backup_companies, restore_companies


class InvoiceOffsetTests(FaultCase):
    def seed(self, face=155000, paid=122400, party="深圳市富芯通电子有限公司", source="正常"):
        result = self.import_files(invoice=self.invoice_file([dict(invoice_row("BLUE", face, party=party), source_status=source)]),
                                   bank=self.bank_file([bank_row(amount=paid, party=party)]) if paid else None)
        self.blue = result["invoices"][0]["id"]
        self.bank = result["bank"][0]["id"] if paid else None
        if paid and not result["allocations"]:
            result = self.service.review(dict(revision=result["revision"], allocations=[dict(bank_id=self.bank, invoice_id=self.blue, amount_cents=paid)], note="多开"))
        return result

    def red(self, amount=32600, number="RED", party="深圳市富芯通电子有限公司", source="正常"):
        result = self.import_files(invoice=self.invoice_file([dict(invoice_row(number, -amount, party=party, date="2026-10-03"), red=True, source_status=source)]))
        return next(i["id"] for i in result["invoices"] if i["number"] == number)

    def act(self, method, **payload):
        return getattr(self.service, method)(dict(revision=self.ledger()["revision"], **payload))

    def offset(self, red, blue=None):
        return self.act("invoice_offset", red_invoice_id=red, blue_invoice_id=blue or self.blue, note="已核对原票号码和红票依据")

    def invoice(self, iid=None):
        return next(i for i in self.ledger()["invoices"] if i["id"] == (iid or self.blue))

    def test_difference_exactly_cleared_keeps_payment_and_original_note(self):
        original = self.seed()
        red = self.red()
        before = self.saved()
        preview = self.act("invoice_offset_preview", red_invoice_id=red, blue_invoice_id=self.blue)
        self.assertEqual(self.saved(), before)
        self.assertEqual((preview["net_amount_cents"], preview["remaining_cents"], preview["can_save"]), (122400, 0, True))
        result = self.offset(red)
        blue = self.invoice()
        self.assertEqual((blue["amount_cents"], blue["offset_cents"], blue["net_amount_cents"], blue["remaining_cents"], blue["distributable_cents"]), (155000, 32600, 122400, 0, 0))
        self.assertEqual(blue["difference_status"], "offset_cleared")
        self.assertEqual(result["bank"][0]["status"], "matched")
        self.assertEqual(result["allocations"], original["allocations"])
        self.assertEqual(result["allocations"][0]["note"], "多开")
        self.assertEqual(self.invoice(red)["offset_status"], "linked")
        self.assertEqual(self.invoice(red)["distributable_cents"], 0)
        self.assertIn("326.00", result["bank"][0]["invoice_offset_hint"])
        self.assertEqual(result["schema_version"], 2)

    def test_excess_rejects_then_whole_allocation_undo_allows_net_allocation(self):
        self.seed(100000, 100000)
        red = self.red(20000)
        preview = self.act("invoice_offset_preview", red_invoice_id=red, blue_invoice_id=self.blue)
        self.assertEqual((preview["can_save"], preview["excess_cents"], len(preview["allocations"])), (False, 20000, 1))
        self.assert_rejected_unchanged(lambda: self.offset(red))
        self.act("undo", allocation_id=self.ledger()["allocations"][0]["id"], note="先撤回整条付款关联")
        result = self.offset(red)
        self.assertEqual(self.invoice()["difference_status"], "none")
        self.assertFalse(any(a["state"] == "active" for a in result["allocations"]))
        result = self.allocate([dict(bank_id=self.bank, invoice_id=self.blue, amount_cents=80000)])
        self.assertEqual(result["bank"][0]["remaining_cents"], 20000)
        self.assertEqual(self.invoice()["remaining_cents"], 0)

    def test_multiple_reds_full_offset_over_limit_and_red_reuse_rejected(self):
        self.seed(100000, 0)
        red1, red2 = self.red(30000, "R1"), self.red(70000, "R2")
        self.offset(red1)
        self.assertEqual(self.invoice()["offset_status"], "partial")
        self.assertEqual(self.invoice()["difference_status"], "none")
        self.assert_rejected_unchanged(lambda: self.offset(red1))
        result = self.offset(red2)
        self.assertEqual((self.invoice()["net_amount_cents"], self.invoice()["status"]), (0, "offset_full"))
        extra = self.red(100, "R3")
        self.assert_rejected_unchanged(lambda: self.offset(extra))
        self.act("exception", invoice_id=extra, note="该红票不冲抵当前账本")
        result = self.import_files(bank=self.bank_file([bank_row(amount=100, party="深圳市富芯通电子有限公司")]))
        bid = result["bank"][0]["id"]
        self.assert_rejected_unchanged(lambda: self.allocate([dict(bank_id=bid, invoice_id=self.blue, amount_cents=100)]))
        self.assert_rejected_unchanged(lambda: self.allocate([dict(bank_id=bid, invoice_id=red1, amount_cents=100)]))

    def test_existing_conclusions_preserved_when_offset_only_reduces_balance(self):
        for status in ("pending", "amount_error", "carry_forward"):
            with self.subTest(status=status):
                self.setUp()
                self.seed()
                self.act("invoice_difference", invoice_id=self.blue, status=status, note="保留现有处理结论")
                self.offset(self.red(10000))
                invoice = self.invoice()
                self.assertEqual((invoice["difference_status"], invoice["difference_note"], invoice["remaining_cents"]), (status, "保留现有处理结论", 22600))
                self.assertEqual(invoice["distributable_cents"], 22600 if status == "carry_forward" else 0)

    def test_undo_reopens_difference_and_retains_history_and_allocations(self):
        self.seed()
        self.act("invoice_difference", invoice_id=self.blue, status="carry_forward", note="原跨月依据")
        result = self.offset(self.red())
        original_allocations = copy.deepcopy(result["allocations"])
        self.act("invoice_offset_undo", offset_id=result["invoice_offsets"][0]["id"], note="对应原票选择错误，重新核实")
        result = self.ledger()
        self.assertEqual((self.invoice()["remaining_cents"], self.invoice()["difference_status"]), (32600, "pending"))
        self.assertEqual(result["allocations"], original_allocations)
        self.assertEqual(result["invoice_offsets"][0]["state"], "revoked")
        self.assertTrue(any(a.get("previous", {}).get("difference", {}).get("note") == "原跨月依据" for a in result["audit"] if isinstance(a.get("previous"), dict)))
        self.assert_rejected_unchanged(lambda: self.act("invoice_offset_undo", offset_id=result["invoice_offsets"][0]["id"], note="重复撤回"))

    def test_undo_without_difference_does_not_invent_difference_or_restore_payment(self):
        self.seed(100000, 100000)
        self.act("undo", allocation_id=self.ledger()["allocations"][0]["id"], note="重新核对")
        result = self.offset(self.red(20000))
        self.act("invoice_offset_undo", offset_id=result["invoice_offsets"][0]["id"], note="撤回错误对应")
        self.assertEqual(self.invoice()["difference_status"], "none")
        self.assertEqual(self.ledger()["stats"]["allocated_cents"], 0)

    def test_old_exception_review_must_be_undone_and_cannot_overlap_offset(self):
        self.seed()
        red = self.red()
        self.act("exception", invoice_id=red, note="此前认为不冲抵当前账本")
        self.assert_rejected_unchanged(lambda: self.offset(red))
        self.act("reverse_exception", invoice_id=red, note="更正原复核结论")
        self.offset(red)
        self.assert_rejected_unchanged(lambda: self.act("exception", invoice_id=red, note="不可并存"))
        self.assertEqual(len(self.invoice(red)["exception_review_history"]), 1)

    def test_red_first_and_same_amount_originals_require_manual_choice(self):
        red = self.red(20000)
        self.assertEqual(self.invoice(red)["offset_status"], "pending")
        result = self.import_files(invoice=self.invoice_file([invoice_row(n, 100000, party="深圳市富芯通电子有限公司") for n in ("B1", "B2")]))
        self.assertEqual(result["invoice_offsets"], [])
        self.blue = next(i["id"] for i in result["invoices"] if i["number"] == "B2")
        self.offset(red)
        self.assertEqual(self.invoice()["net_amount_cents"], 80000)
        self.assertEqual(next(i for i in self.ledger()["invoices"] if i["number"] == "B1")["net_amount_cents"], 100000)

    def test_source_full_requires_complete_reds_and_old_flag_is_compatible(self):
        self.seed(100000, 0, source="全额红冲")
        raw = json.loads(self.saved())
        raw["invoices"][0]["invalid"] = "发票状态：全额红冲"
        self.service.store.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        before = self.saved()
        self.assertTrue(self.invoice()["offset_source_hold"])
        self.assertEqual(self.saved(), before)
        result = self.import_files(invoice=self.invoice_file([dict(invoice_row("BLUE", 100000, party="深圳市富芯通电子有限公司"), source_status="全额红冲")]))
        self.assertEqual(len(result["conflicts"]), 0)
        self.offset(self.red(30000, "R1"))
        self.assertEqual(self.invoice()["distributable_cents"], 0)
        self.offset(self.red(70000, "R2"))
        self.assertEqual(self.invoice()["offset_source_hold"], "")
        self.assertEqual(self.invoice()["status"], "offset_full")

    def test_source_partial_and_true_invalid_are_distinct(self):
        self.seed(100000, 0, source="部分红冲")
        self.assertEqual(self.invoice()["distributable_cents"], 0)
        self.offset(self.red(20000))
        self.assertEqual((self.invoice()["distributable_cents"], self.invoice()["difference_status"]), (80000, "none"))
        red = self.red(10000, "VOID", source="作废")
        self.assert_rejected_unchanged(lambda: self.offset(red))
        raw = self.service.store.load()
        normal_red = next(i for i in raw["invoices"] if i["id"] == red)
        normal_red.update(source_status="正常", invalid="")
        blue = next(i for i in raw["invoices"] if i["id"] == self.blue)
        for state in ("作废", "全额红冲异常", "失控"):
            blue["source_status"] = state
            with self.assertRaises(ValueError):
                checked_pair(raw, dict(red_invoice_id=red, blue_invoice_id=self.blue))

    def test_legacy_red_source_marker_is_not_a_true_invalid_or_direct_payment(self):
        self.seed(100000, 0)
        red = self.red(20000, source="已红冲")
        raw = json.loads(self.saved())
        next(i for i in raw["invoices"] if i["id"] == red)["invalid"] = "发票状态：已红冲"
        self.service.store.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.offset(red)
        self.assertEqual(self.invoice()["net_amount_cents"], 80000)
        self.assertEqual(self.invoice(red)["distributable_cents"], 0)

    def test_shared_original_payments_excess_and_other_anomaly_still_hold(self):
        result = self.import_files(bank=self.bank_file([bank_row("P1", 50000), bank_row("P2", 30000)]), invoice=self.invoice_file([invoice_row("BLUE", 100000)]))
        self.blue = result["invoices"][0]["id"]
        self.allocate([dict(bank_id=b["id"], invoice_id=self.blue, amount_cents=b["amount_cents"]) for b in result["bank"]])
        red = self.red(20000, party="测试供应商")
        other = self.red(10000, "OTHER", party="测试供应商")
        result = self.offset(red)
        self.assertEqual(result["stats"]["allocated_cents"], 80000)
        self.assertTrue(all(b["status"] == "review" for b in result["bank"]))
        result = self.act("exception", invoice_id=other, note="另一张红票不属于当前账本")
        self.assertTrue(all(b["status"] == "matched" for b in result["bank"]))

    def test_version_changes_on_either_end_require_offset_undo(self):
        for red_end in (False, True):
            with self.subTest(red_end=red_end):
                self.setUp()
                self.seed(100000, 0)
                red = self.red(20000)
                self.offset(red)
                changed = dict(invoice_row("RED" if red_end else "BLUE", -15000 if red_end else 90000, party="深圳市富芯通电子有限公司", date="2026-10-03" if red_end else "2026-09-03"), red=red_end)
                result = self.import_files(invoice=self.invoice_file([changed]))
                cid = result["conflicts"][0]["id"]
                self.assert_rejected_unchanged(lambda: self.act("resolve", conflict_id=cid, action="accept", note="接受更正"))
                self.act("invoice_offset_undo", offset_id=result["invoice_offsets"][0]["id"], note="先撤回冲红关系")
                self.act("resolve", conflict_id=cid, action="accept", note="更正已核实")
                self.offset(red)
                self.assert_rejected_unchanged(lambda: self.act("reverse_conflict", conflict_id=cid, note="撤回更正"))

    def test_pending_source_version_on_red_prevents_pairing(self):
        self.seed(100000, 0)
        red = self.red(20000)
        self.import_files(invoice=self.invoice_file([dict(invoice_row("RED", -20000, party="深圳市富芯通电子有限公司", date="2026-10-03"), red=True, source_status="作废")]))
        self.assert_rejected_unchanged(lambda: self.offset(red))

    def test_repeat_import_stale_duplicate_note_and_pair_guards(self):
        self.seed(100000, 0)
        red = self.red(20000)
        base = dict(revision=self.ledger()["revision"], red_invoice_id=red, blue_invoice_id=self.blue, note="核实对应")
        for values in ({"note": " "}, {"note": []}, {"note": "长"*1001}, {"blue_invoice_id": red}, {"red_invoice_id": self.blue}, {"blue_invoice_id": "missing"}, {"revision": True}):
            self.assert_rejected_unchanged(lambda: self.service.invoice_offset(dict(base, **values)))
        self.service.invoice_offset(base)
        self.assert_rejected_unchanged(lambda: self.service.invoice_offset(base))
        self.assert_rejected_unchanged(lambda: self.service.invoice_offset_preview(base))
        self.red(20000)
        self.assertEqual(len(self.ledger()["invoice_offsets"]), 1)
        self.assertEqual(len(self.ledger()["invoices"]), 2)

    def test_wrong_supplier_currency_and_ledger_rejected(self):
        self.seed(100000, 0)
        red = self.red(20000, party="其他供应商")
        self.assert_rejected_unchanged(lambda: self.offset(red))
        raw = self.service.store.load()
        candidate = next(i for i in raw["invoices"] if i["id"] == red)
        candidate["party"] = "深圳市富芯通电子有限公司"
        for field, value in (("currency", "USD"), ("company_id", "other")):
            altered = copy.deepcopy(raw)
            next(i for i in altered["invoices"] if i["id"] == red)[field] = value
            with self.assertRaises(ValueError):
                checked_pair(altered, dict(red_invoice_id=red, blue_invoice_id=self.blue))

    def test_atomic_failure_does_not_upgrade_or_partly_persist(self):
        self.seed()
        red = self.red()
        before = self.saved()
        with patch("reconcile.ledger_storage.replace_snapshot", side_effect=OSError("模拟保存失败")):
            with self.assertRaises(OSError):
                self.offset(red)
        self.assertEqual(self.saved(), before)
        self.assertEqual(self.ledger()["schema_version"], 1)
        self.assertEqual(self.ledger()["invoice_offsets"], [])
        self.offset(red)
        before = self.saved()
        with patch("reconcile.ledger_storage.replace_snapshot", side_effect=OSError("模拟撤回失败")):
            with self.assertRaises(OSError):
                self.act("invoice_offset_undo", offset_id=self.ledger()["invoice_offsets"][0]["id"], note="验证原子撤回")
        self.assertEqual(self.saved(), before)

    def test_readonly_compatibility_and_old_program_rejects_new_format(self):
        self.seed(100000, 0)
        red = self.red(20000)
        before = self.saved()
        self.ledger()
        self.assertEqual(before, self.saved())
        self.assertNotIn("invoice_offsets", json.loads(before))
        self.offset(red)
        old_code = (ROOT / "tests/fixtures/legacy_ledger_validation_v1.py").read_text(encoding="utf-8")
        namespace = {"__package__": "reconcile"}
        exec(compile(old_code, "旧程序账本校验", "exec"), namespace)
        namespace["validate_ledger"](json.loads(before))
        with self.assertRaises(ValueError):
            namespace["validate_ledger"](json.loads(self.saved()))
        before = self.saved()
        self.service = type(self.service)(self.directory, self.config)
        self.assertEqual(self.invoice()["net_amount_cents"], 80000)
        self.assertEqual(before, self.saved())

    def test_corrupt_offset_records_and_net_overallocation_are_rejected(self):
        self.seed()
        self.offset(self.red())
        raw = json.loads(self.saved())
        mutations = [lambda l: l.update(schema_version=1), lambda l: l.pop("invoice_offsets"),
                     lambda l: l["invoice_offsets"][0].update(amount_cents=True),
                     lambda l: l["invoice_offsets"].append(copy.deepcopy(l["invoice_offsets"][0])),
                     lambda l: l["invoices"][1].update(exception_review={"note": "非法并存"}),
                     lambda l: l["invoice_offsets"][0].update(red_invoice_id="missing"),
                     lambda l: l["allocations"][0].update(amount_cents=130000)]
        for change in mutations:
            with self.subTest(change=change):
                altered = copy.deepcopy(raw)
                change(altered)
                with self.assertRaises(ValueError):
                    validate_ledger(altered)

    def test_save_and_undo_do_not_auto_match_but_import_uses_net(self):
        self.seed(100000, 0)
        red = self.red(20000)
        self.import_files(bank=self.bank_file([bank_row(amount=80000, party="深圳市富芯通电子有限公司")]))
        result = self.offset(red)
        self.assertEqual(result["allocations"], [])
        self.import_files(bank=self.bank_file([bank_row(amount=80000, party="深圳市富芯通电子有限公司")]))
        self.assertEqual(self.ledger()["stats"]["allocated_cents"], 80000)
        result = self.act("invoice_offset_undo", offset_id=result["invoice_offsets"][0]["id"], note="重新确认")
        self.assertEqual(len(result["allocations"]), 1)

    def test_month_export_signed_total_and_single_backup_restore(self):
        self.seed()
        result = self.offset(self.red())
        self.assertEqual(sum(i["amount_cents"] for i in result["invoices"]), 122400)
        originals = []
        for month in ("", "2026-08"):
            output = self.act("export", month=month)
            directory = Path(output["directory"])
            snapshot = json.loads((directory / "完整对账快照.json").read_text(encoding="utf-8"))
            self.assertEqual(len(snapshot["invoices"]), 2)
            self.assertEqual(snapshot["invoice_offsets"], result["invoice_offsets"])
            self.assertEqual(snapshot["stats"]["allocated_cents"], 122400)
            with (directory / "发票余额明细.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            blue = next(row for row in rows if row["发票编号"] == self.blue)
            self.assertEqual((blue["累计冲红金额"], blue["冲红后可核销总额"], blue["差额状态"]), ("326.00", "1224.00", "差额已冲销"))
            self.assertTrue((directory / "红冲关系明细.csv").exists())
            originals.append((directory / "完整对账快照.json", (directory / "完整对账快照.json").read_bytes()))
        (self.directory / "config").mkdir()
        (self.directory / "config/defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        archive = backup_data(self.directory, self.directory / "temp/packages")["archive"]
        target = self.directory / "temp/restored"
        (target / "config").mkdir(parents=True)
        (target / "config/defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        restore_data(target, archive)
        restored = type(self.service)(target, self.config)
        self.assertEqual(restored.store.path.read_bytes(), self.saved())
        self.assertEqual(restored.ledger()["invoices"], self.ledger()["invoices"])
        self.act("invoice_offset_undo", offset_id=result["invoice_offsets"][0]["id"], note="核对撤回后导出保留")
        for path, before in originals:
            self.assertEqual(path.read_bytes(), before)

    def test_full_offset_export_and_old_configuration_fallback_are_readonly(self):
        self.seed(100000, 0)
        self.offset(self.red(100000))
        directory = Path(self.act("export")["directory"])
        with (directory / "发票余额明细.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertIn("不可核销", rows[0]["状态"])
        self.assertEqual(rows[1]["状态"], "已对应原票")
        old = copy.deepcopy(self.config)
        old.pop("offset_statuses")
        old["difference_statuses"].pop("offset_cleared")
        (self.directory / "config").mkdir()
        path = self.directory / "config/defaults.json"
        path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        before = path.read_bytes()
        config = load_config(self.directory)
        self.assertEqual(config["difference_statuses"]["offset_cleared"], "差额已冲销")
        self.assertEqual(ledger_view(self.service.store.load(), config)["invoices"][0]["offset_status"], "full")
        self.assertEqual(path.read_bytes(), before)

    def test_void_red_correction_requires_undo_and_keeps_payments_and_evidence(self):
        original = self.seed()
        red = self.red()
        result = self.offset(red)
        self.import_files(invoice=self.invoice_file([dict(invoice_row("RED", -32600, party="深圳市富芯通电子有限公司", date="2026-10-03"), red=True, source_status="作废")]))
        cid = self.ledger()["conflicts"][0]["id"]
        self.assert_rejected_unchanged(lambda: self.act("resolve", conflict_id=cid, action="accept", note="红票已作废"))
        self.act("invoice_offset_undo", offset_id=result["invoice_offsets"][0]["id"], note="先撤回冲红")
        result = self.act("resolve", conflict_id=cid, action="accept", note="采用红票作废版本")
        self.assertEqual(result["allocations"], original["allocations"])
        self.assertEqual(self.invoice()["difference_status"], "pending")
        self.assertEqual(self.invoice()["distributable_cents"], 0)
        self.assertEqual(result["invoice_offsets"][0]["state"], "revoked")
        self.assert_rejected_unchanged(lambda: self.offset(red))

    def test_carry_forward_after_partial_offset_can_use_remaining_in_batch(self):
        self.seed(150000, 100000)
        self.act("invoice_difference", invoice_id=self.blue, status="carry_forward", note="确认跨月继续使用")
        self.offset(self.red(20000))
        view = self.import_files(bank=self.bank_file([bank_row("NEXT1", 10000, party="深圳市富芯通电子有限公司", date="2026-11-01"), bank_row("NEXT2", 20000, party="深圳市富芯通电子有限公司", date="2026-11-02")]))
        self.allocate([dict(bank_id=b["id"], invoice_id=self.blue, amount_cents=b["amount_cents"]) for b in view["bank"] if b["reference"].startswith("NEXT")])
        self.assertEqual((self.invoice()["remaining_cents"], self.invoice()["difference_status"]), (0, "cleared"))
        self.assertEqual(self.invoice()["difference_note"], "确认跨月继续使用")
        self.assertEqual(self.ledger()["stats"]["allocated_cents"], 130000)


class CompanyOffsetTests(FaultCase):
    def test_four_company_isolation_routing_concurrency_and_bundle_restore(self):
        (self.directory / "config").mkdir()
        (self.directory / "config/defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        registry = CompanyRegistry(self.directory, self.config)
        router = RequestRouter(registry)
        services, payloads = {}, {}
        for profile in self.config["companies"]:
            key = profile["key"]
            name = self.config["company_name"] if key == "moderate" else profile["label"]+"隔离验证有限公司"
            service = registry.get(key) if key == "moderate" else registry.activate(dict(company_key=key, legal_name=name))
            view = service.import_files(dict(revision=service.ledger()["revision"], invoice=upload([invoice_row("BLUE", 100000), dict(invoice_row("RED", -20000), red=True)], "invoice", name)))
            services[key] = service
            payloads[key] = dict(company_key=key, revision=view["revision"], red_invoice_id=view["invoices"][1]["id"], blue_invoice_id=view["invoices"][0]["id"], note="当前公司确认对应")
        snapshots = {key: s.store.path.read_bytes() for key, s in services.items()}
        payload = payloads["moderate"]
        for route in ("/api/invoice-offset/preview", "/api/invoice-offset", "/api/invoice-offset/undo"):
            with self.assertRaisesRegex(ValueError, "缺少公司标识"):
                router.post(route, {k: v for k, v in payload.items() if k != "company_key"})
        with self.assertRaises(ValueError):
            router.post("/api/invoice-offset", dict(payload, red_invoice_id=payloads["haisi"]["red_invoice_id"]))
        result = router.post("/api/invoice-offset/preview", payload)
        self.assertEqual(result["company_key"], "moderate")
        self.assertEqual(snapshots, {k: s.store.path.read_bytes() for k, s in services.items()})
        def save(_):
            try:
                return router.post("/api/invoice-offset", payload)["schema_version"]
            except ValueError:
                return "stale"
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(save, range(4)))
        self.assertEqual(results.count(2), 1)
        self.assertEqual(results.count("stale"), 3)
        for key in services:
            if key != "moderate":
                self.assertEqual(services[key].store.path.read_bytes(), snapshots[key])
                router.post("/api/invoice-offset", payloads[key])
        archive = backup_companies(self.directory, self.directory / "temp/packages", all_companies=True)["archive"]
        target = self.directory / "temp/restored"
        (target / "config").mkdir(parents=True)
        (target / "config/defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        published = []
        def interrupted(source, destination):
            published.append(destination)
            if len(published) == 2:
                raise OSError("模拟第二家公司恢复中断")
            replace_snapshot(source, destination)
        with self.assertRaisesRegex(OSError, "恢复未完成"):
            restore_companies(target, archive, all_companies=True, publisher=interrupted)
        restore_companies(target, archive, all_companies=True)
        restored = CompanyRegistry(target, self.config)
        for key, service in services.items():
            self.assertEqual(restored.get(key).store.path.read_bytes(), service.store.path.read_bytes())
            self.assertEqual(restored.get(key).ledger()["invoices"][0]["net_amount_cents"], 80000)
