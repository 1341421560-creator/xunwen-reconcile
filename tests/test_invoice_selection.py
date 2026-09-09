import copy
import csv
import http.client
import json
import threading
from pathlib import Path
from unittest.mock import patch
from fault_support import FaultCase, bank_row, invoice_row, upload
from reconcile.company_registry import CompanyRegistry
from reconcile.http_server import make_server
from reconcile.ledger_validation import LedgerCorruptionError
from reconcile.request_router import RequestRouter
from deployment.company_transfer import backup_companies, restore_companies


class InvoiceSelectionTests(FaultCase):
    def select(self, invoice_ids, selected, **extra):
        return self.service.invoice_selection(dict(revision=self.ledger()["revision"],
                                                   invoice_ids=invoice_ids, selected=selected, **extra))

    def seed(self, count=3):
        return self.import_files(invoice=self.invoice_file([
            invoice_row("选择测试" + str(index), amount=10001 + index,
                        date="2026-08-01" if index % 2 else "2026-09-01")
            for index in range(count)]))

    def configuration(self, root):
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / "config/defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")

    def test_old_ledger_defaults_to_all_selected_without_read_write(self):
        self.seed()
        legacy = json.loads(self.saved())
        for item in legacy["invoices"]:
            item.pop("include_in_total", None)
        self.service.store.path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        original = self.saved()
        self.assertTrue(all(item["include_in_total"] is True for item in self.ledger()["invoices"]))
        self.service = type(self.service)(self.directory, self.config)
        self.assertTrue(all(item["include_in_total"] is True for item in self.ledger()["invoices"]))
        self.assertEqual(self.saved(), original)

    def test_batch_choice_survives_restart_and_audit_preserves_prior_events(self):
        initial = self.seed()
        ids = [item["id"] for item in initial["invoices"]]
        result = self.select([ids[2], ids[0], ids[2]], False)
        self.assertEqual([item["include_in_total"] for item in result["invoices"]], [False, True, False])
        self.assertEqual(result["revision"], initial["revision"] + 1)
        self.assertEqual(result["audit"][:-1], initial["audit"])
        self.assertEqual(result["audit"][-1]["action"], "invoice_selection")
        self.assertEqual(result["audit"][-1]["invoice_ids"], [ids[2], ids[0]])
        self.assertEqual(result["audit"][-1]["previous"], {ids[2]: True, ids[0]: True})
        self.assertIs(result["audit"][-1]["selected"], False)
        self.service = type(self.service)(self.directory, self.config)
        self.assertEqual([item["include_in_total"] for item in self.ledger()["invoices"]], [False, True, False])
        result = self.select(ids, True)
        self.assertTrue(all(item["include_in_total"] is True for item in result["invoices"]))

    def test_more_than_two_pages_can_be_changed_atomically(self):
        initial = self.seed(205)
        ids = [item["id"] for item in initial["invoices"]]
        result = self.select(ids, False)
        self.assertEqual(len(result["invoices"]), 205)
        self.assertFalse(any(item["include_in_total"] for item in result["invoices"]))
        result = self.select(ids[99:201], True)
        selected = [item for item in result["invoices"] if item["include_in_total"]]
        self.assertEqual(len(selected), 102)
        self.assertEqual(sum(item["amount_cents"] for item in selected), sum(10001 + n for n in range(99, 201)))

    def test_invalid_ids_reject_entire_batch_without_partial_save(self):
        initial = self.seed()
        ids = [item["id"] for item in initial["invoices"]]
        self.assert_rejected_unchanged(lambda: self.select([ids[0], "不存在的发票", ids[1]], False))
        self.assertTrue(all(item["include_in_total"] for item in self.ledger()["invoices"]))

    def test_payload_types_empty_ids_and_missing_fields_are_rejected(self):
        initial = self.seed()
        base = dict(revision=initial["revision"], invoice_ids=[initial["invoices"][0]["id"]], selected=False)
        invalid = [{"selected": value} for value in (None, "false", "true", 0, 1, [], {})]
        invalid.extend({"invoice_ids": value} for value in (None, [], "invoice", {}, [None], [1], [True], [""], [" "], [[]]))
        invalid.extend({"revision": value} for value in (None, True, "1", -1))
        for change in invalid:
            with self.subTest(change=change):
                self.assert_rejected_unchanged(lambda: self.service.invoice_selection(dict(base, **change)))
        for field in ("revision", "invoice_ids", "selected"):
            payload = {key: value for key, value in base.items() if key != field}
            with self.subTest(missing=field):
                self.assert_rejected_unchanged(lambda: self.service.invoice_selection(payload))

    def test_duplicate_and_stale_requests_cannot_overwrite_newer_choice(self):
        initial = self.seed()
        iid = initial["invoices"][0]["id"]
        old = dict(revision=initial["revision"], invoice_ids=[iid], selected=False)
        self.service.invoice_selection(old)
        self.assert_rejected_unchanged(lambda: self.service.invoice_selection(old))
        self.select([iid], True)
        self.assert_rejected_unchanged(lambda: self.service.invoice_selection(old))
        self.assertTrue(self.ledger()["invoices"][0]["include_in_total"])

    def test_selection_does_not_run_matching_or_change_business_progress(self):
        view = self.import_files(bank=self.bank_file([bank_row(amount=122400)]),
                                 invoice=self.invoice_file([invoice_row(amount=155000)]))
        bid, iid = view["bank"][0]["id"], view["invoices"][0]["id"]
        view = self.allocate([dict(bank_id=bid, invoice_id=iid, amount_cents=122400)])
        before = self.service.store.load()
        with patch("reconcile.service.auto_match", side_effect=AssertionError("勾选不应触发匹配")):
            result = self.select([iid], False)
        for field in ("bank", "allocations", "stats", "filtered_stats", "settings", "conflicts", "batches"):
            self.assertEqual(result[field], view[field])
        self.assertEqual(result["invoices"][0]["difference_status"], "pending")
        self.assertEqual(result["invoices"][0]["distributable_cents"], 0)
        after = self.service.store.load()
        prior_invoice, next_invoice = copy.deepcopy(before["invoices"][0]), copy.deepcopy(after["invoices"][0])
        prior_invoice.pop("include_in_total", None)
        next_invoice.pop("include_in_total", None)
        self.assertEqual(next_invoice, prior_invoice)
        self.assert_rejected_unchanged(lambda: self.allocate([dict(bank_id=bid, invoice_id=iid, amount_cents=1)]))

    def test_deselection_does_not_block_future_normal_matching(self):
        view = self.import_files(invoice=self.invoice_file([invoice_row(amount=122400)]))
        self.select([view["invoices"][0]["id"]], False)
        result = self.import_files(bank=self.bank_file([bank_row(amount=122400)]))
        self.assertEqual(result["bank"][0]["status"], "matched")
        self.assertEqual(result["invoices"][0]["allocated_cents"], 122400)
        self.assertFalse(result["invoices"][0]["include_in_total"])

    def test_red_invalid_and_conflicting_invoices_can_be_selected_without_review_changes(self):
        view = self.import_files(invoice=self.invoice_file([
            invoice_row("普通", 30000), dict(invoice_row("红字", -10000), red=True),
            dict(invoice_row("异常", 20000), source_status="作废")]))
        view = self.import_files(invoice=self.invoice_file([invoice_row("普通", 35000)]))
        ids = [item["id"] for item in view["invoices"]]
        result = self.select(ids, False)
        self.assertFalse(any(item["include_in_total"] for item in result["invoices"]))
        self.assertEqual(result["conflicts"], view["conflicts"])
        self.assertEqual([item["status"] for item in result["invoices"]], [item["status"] for item in view["invoices"]])
        self.assertEqual([item["distributable_cents"] for item in result["invoices"]], [item["distributable_cents"] for item in view["invoices"]])
        self.assertTrue(all(item["include_in_total"] for item in self.select(ids, True)["invoices"]))

    def test_duplicate_import_retains_choice_and_new_invoice_defaults_selected(self):
        initial = self.seed()
        self.select([initial["invoices"][0]["id"]], False)
        result = self.import_files(invoice=self.invoice_file([
            invoice_row("选择测试0", amount=10001, date="2026-09-01"), invoice_row("真正的新票", amount=90001)]))
        self.assertEqual(len(result["invoices"]), 4)
        self.assertEqual(result["last_import"]["counts"]["invoices"]["duplicate"], 1)
        self.assertFalse(result["invoices"][0]["include_in_total"])
        self.assertTrue(result["invoices"][-1]["include_in_total"])

    def test_corrected_invoice_keeps_choice_and_reverse_keeps_latest_choice(self):
        view = self.import_files(invoice=self.invoice_file([invoice_row(amount=10001)]))
        iid = view["invoices"][0]["id"]
        self.select([iid], False)
        view = self.import_files(invoice=self.invoice_file([invoice_row(amount=23456)]))
        cid = view["conflicts"][0]["id"]
        view = self.service.resolve(dict(revision=view["revision"], conflict_id=cid, action="accept", note="核实新版金额"))
        self.assertEqual(view["invoices"][0]["amount_cents"], 23456)
        self.assertFalse(view["invoices"][0]["include_in_total"])
        view = self.select([iid], True)
        view = self.service.reverse_conflict(dict(revision=view["revision"], conflict_id=cid, note="撤回复核继续检查"))
        self.assertEqual(view["invoices"][0]["amount_cents"], 10001)
        self.assertTrue(view["invoices"][0]["include_in_total"])
        self.assertFalse(view["conflicts"][0]["previous_record"]["include_in_total"])
        self.assertTrue(view["conflicts"][0]["reversal_history"][0]["replaced_record"]["include_in_total"])

    def test_exception_review_and_reversal_retain_selection(self):
        view = self.import_files(invoice=self.invoice_file([dict(invoice_row("红字", -10000), red=True)]))
        iid = view["invoices"][0]["id"]
        view = self.select([iid], False)
        view = self.service.exception(dict(revision=view["revision"], invoice_id=iid, note="红票已核查"))
        self.assertFalse(view["invoices"][0]["include_in_total"])
        view = self.service.reverse_exception(dict(revision=view["revision"], invoice_id=iid, note="再次复核"))
        self.assertFalse(view["invoices"][0]["include_in_total"])

    def test_invalid_persisted_selection_stops_read_without_rewriting(self):
        self.seed()
        original = json.loads(self.saved())
        for value in (0, 1, "false", None, []):
            with self.subTest(value=value):
                content = copy.deepcopy(original)
                content["invoices"][0]["include_in_total"] = value
                self.service.store.path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
                before = self.saved()
                with self.assertRaises(LedgerCorruptionError):
                    self.ledger()
                self.assertEqual(self.saved(), before)

    def test_export_scope_stays_unchanged_and_snapshot_keeps_selection(self):
        view = self.import_files(bank=self.bank_file([bank_row(amount=10001)]),
                                 invoice=self.invoice_file([invoice_row("已关联", 10001), invoice_row("未关联", 20002)]))
        self.select([item["id"] for item in view["invoices"]], False)
        for month, expected_count in (("", 2), ("2026-08", 1)):
            with self.subTest(month=month):
                output = self.service.export(dict(revision=self.ledger()["revision"], month=month))
                directory = Path(output["directory"])
                with (directory / "发票余额明细.csv").open(encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), expected_count)
                snapshot = json.loads((directory / "完整对账快照.json").read_text(encoding="utf-8"))
                self.assertEqual(len(snapshot["invoices"]), expected_count)
                self.assertFalse(any(item["include_in_total"] for item in snapshot["invoices"]))
                self.assertEqual(snapshot["stats"]["allocated_cents"], 10001)
                self.assertEqual(snapshot["audit"][-1]["action"], "invoice_selection")

    def test_four_company_choices_are_independent_and_backups_keep_them(self):
        self.configuration(self.directory)
        registry = CompanyRegistry(self.directory, self.config)
        names = {"haisi": "测试海思有限公司", "moderate": self.config["company_name"],
                 "dongguan_xunwen": "测试东莞循文有限公司", "huachuangxing": "测试华创星有限公司"}
        services = {}
        for key, name in names.items():
            service = registry.activate(dict(company_key=key, legal_name=name))
            view = service.import_files(dict(revision=service.ledger()["revision"],
                                             bank=upload([bank_row()], "bank", name),
                                             invoice=upload([invoice_row()], "invoice", name)))
            service.bank_note(dict(revision=view["revision"], bank_id=view["bank"][0]["id"], note=key + "已保存的长期备注"))
            services[key] = service
        originals = {key: service.store.path.read_bytes() for key, service in services.items()}
        router = RequestRouter(registry)
        for key in ("haisi", "dongguan_xunwen"):
            view = services[key].ledger()
            result = router.post("/api/invoice-selection", dict(company_key=key, revision=view["revision"],
                                                                invoice_ids=[view["invoices"][0]["id"]], selected=False))
            self.assertEqual(result["company_key"], key)
        for key in ("moderate", "huachuangxing"):
            self.assertEqual(services[key].store.path.read_bytes(), originals[key])
        before = services["haisi"].store.path.read_bytes()
        with self.assertRaises(ValueError):
            router.post("/api/invoice-selection", dict(company_key="haisi", revision=services["haisi"].ledger()["revision"],
                                                       invoice_ids=[services["moderate"].ledger()["invoices"][0]["id"]], selected=False))
        self.assertEqual(services["haisi"].store.path.read_bytes(), before)
        for scope in (dict(company_key="haisi"), dict(all_companies=True)):
            archive = backup_companies(self.directory, self.directory / "temp/packages", **scope)["archive"]
            target = self.directory / ("restored-single" if "company_key" in scope else "restored-all")
            self.configuration(target)
            restore_companies(target, archive, **scope)
            restored = CompanyRegistry(target, self.config)
            for key in (["haisi"] if "company_key" in scope else names):
                self.assertEqual(restored.get(key).store.path.read_bytes(), services[key].store.path.read_bytes())
                self.assertEqual(restored.get(key).ledger()["invoices"][0]["include_in_total"], key not in ("haisi", "dongguan_xunwen"))
                self.assertEqual(restored.get(key).ledger()["bank"][0]["manual_note"], key + "已保存的长期备注")

    def test_http_route_rejects_missing_company_and_stale_revision(self):
        view = self.seed()
        server = make_server(self.service, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def call(payload):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request("POST", "/api/invoice-selection", json.dumps(payload).encode("utf-8"),
                                   {"Content-Type": "application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        try:
            payload = dict(revision=view["revision"], invoice_ids=[view["invoices"][0]["id"]], selected=False)
            before = self.saved()
            self.assertEqual(call(payload)[0], 400)
            self.assertEqual(self.saved(), before)
            payload["company_key"] = "moderate"
            status, result = call(payload)
            self.assertEqual(status, 200)
            self.assertEqual(result["company_key"], "moderate")
            self.assertFalse(result["invoices"][0]["include_in_total"])
            before = self.saved()
            self.assertEqual(call(payload)[0], 400)
            self.assertEqual(self.saved(), before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
