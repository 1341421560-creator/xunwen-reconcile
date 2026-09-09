import copy
import http.client
import io
import json
import shutil
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from fault_support import FaultCase, bank_row, invoice_row, upload
from reconcile.company_registry import CompanyRegistry
from reconcile.request_router import RequestRouter
from reconcile.http_server import make_server
from reconcile.ledger_model import new_ledger
from reconcile.atomic_write import replace_snapshot
from deployment.company_transfer import backup_companies, restore_companies
from deployment.company_archive import read_companies, encode_zip
from deployment.archive_validation import LEDGER_PATH, digest


NAMES = {"haisi": "隔离测试海思完整有限公司", "moderate": "深圳市摩德瑞特科技有限公司",
         "dongguan_xunwen": "隔离测试东莞循文完整有限公司", "huachuangxing": "隔离测试华创星完整有限公司"}


class CompanyTests(FaultCase):
    def setUp(self):
        super().setUp()
        (self.directory / "config").mkdir()
        (self.directory / "config/defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        self.registry = CompanyRegistry(self.directory, self.config)
        self.router = RequestRouter(self.registry)

    def activate(self, key):
        return self.registry.activate({"company_key": key, "legal_name": NAMES[key]})

    def seed(self, key, difference=False):
        service = self.activate(key)
        if not service.store.path.exists():
            service.store.commit(new_ledger(NAMES[key]), 0)
        view = service.import_files({"revision": service.ledger()["revision"],
                                     "bank": upload([bank_row(amount=122400)], "bank", NAMES[key]),
                                     "invoice": upload([invoice_row(amount=155000 if difference else 122400)], "invoice", NAMES[key])})
        return service, view

    def all_seeded(self, difference=False):
        return {key: self.seed(key, difference)[0] for key in NAMES}

    def target(self, name="restored"):
        root = self.directory / name
        (root / "config").mkdir(parents=True)
        shutil.copy2(self.directory / "config/defaults.json", root / "config/defaults.json")
        return root

    def test_catalog_initialization_and_old_read_never_write_ledger(self):
        self.import_files(bank=self.bank_file(), invoice=self.invoice_file())
        original = self.saved()
        self.assertNotIn("key", json.loads(original)["company"])
        old = self.ledger()
        result = self.router.get("/api/bootstrap", {})
        self.assertEqual(result["company_key"], "moderate")
        self.assertEqual(result["result"]["company"]["id"], old["company"]["id"])
        for field in ("bank", "invoices", "allocations", "audit", "stats", "settings", "batches"):
            self.assertEqual(result["result"][field], old[field])
        for key in NAMES:
            data = self.router.get("/api/bootstrap", {"company_key": key})
            self.assertEqual(data["setup_required"], key != "moderate")
        self.assertFalse((self.directory / "company-data").exists())
        self.assertEqual(self.saved(), original)

    def test_legacy_company_key_persists_on_next_normal_commit_only(self):
        self.import_files(bank=self.bank_file())
        original = self.saved()
        old = self.router.get('/api/bootstrap', {})['result']
        self.assertNotIn('key', json.loads(original)['company'])
        self.assertEqual(self.saved(), original)
        result = self.router.post('/api/bank-note', dict(company_key='moderate', revision=old['revision'], bank_id=old['bank'][0]['id'], note='正常提交补齐公司标识'))
        self.assertEqual(json.loads(self.saved())['company']['key'], 'moderate')
        self.assertEqual(result['company']['id'], old['company']['id'])

    def test_legacy_history_readonly_entries_and_restore_routes_are_company_scoped(self):
        services = self.all_seeded()
        sessions = {}
        for key, service in services.items():
            history = service.legacy.import_files({'bank': upload([bank_row()], 'bank', NAMES[key]), 'invoice': upload([invoice_row()], 'invoice', NAMES[key])})
            sessions[key] = history['session_id']
            boot = self.router.get('/api/bootstrap', dict(company_key=key))
            self.assertEqual([row['id'] for row in boot['history']], [history['session_id']])
            self.assertTrue(service.store.temp.is_relative_to(self.directory / 'temp/companies' / key))
        for key in NAMES:
            restored = self.router.post('/api/restore', dict(company_key=key, session_id=sessions[key]))
            self.assertTrue(restored['readonly'])
            self.assertEqual(restored['company_key'], key)
        with self.assertRaises(ValueError):
            self.router.post('/api/restore', dict(company_key='haisi', session_id=sessions['moderate']))
        archive = backup_companies(self.directory, self.directory / 'temp/packages', all_companies=True)['archive']
        target = self.target()
        restore_companies(target, archive, all_companies=True)
        restored_registry = CompanyRegistry(target, self.config)
        for key in NAMES:
            self.assertEqual(restored_registry.get(key).bootstrap()['history'], services[key].bootstrap()['history'])

    def test_activation_is_atomic_idempotent_and_immutable(self):
        service = self.activate("haisi")
        original = service.store.path.read_bytes()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.activate("haisi"), range(8)))
        self.assertTrue(all(result is service for result in results))
        self.assertEqual(service.store.path.read_bytes(), original)
        self.assertEqual(service.ledger()["revision"], 1)
        self.assertEqual(service.ledger()["stats"]["bank_count"], 0)
        for payload in ({"company_key": "haisi", "legal_name": "新公司"}, {"company_key": "huachuangxing", "legal_name": NAMES["haisi"]},
                        {"company_key": "dongguan_xunwen", "legal_name": " "}, {"company_key": "haisi", "legal_name": []}):
            with self.assertRaises(ValueError):
                self.registry.activate(payload)
        self.assertEqual(service.store.path.read_bytes(), original)

    def test_concurrent_first_activation_does_not_duplicate_or_overwrite(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.activate("haisi").ledger()["company"]["id"], range(8)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(self.registry.get("haisi").ledger()["revision"], 1)

    def test_invalid_company_and_old_write_requests_are_rejected(self):
        for key in (None, "", "unknown", "../moderate", "HAISI", [], {}, "haisi/../../ledger"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.router.get("/api/bootstrap", {"company_key": key})
        for route in ("/api/import", "/api/settings", "/api/review", "/api/undo", "/api/export", "/api/bank-note"):
            with self.subTest(route=route), self.assertRaisesRegex(ValueError, "缺少公司标识"):
                self.router.post(route, {"revision": 0})
        with self.assertRaisesRegex(ValueError, "尚未启用"):
            self.router.post("/api/import", {"company_key": "haisi", "revision": 0})

    def test_same_supplier_dates_numbers_and_amounts_match_only_within_company(self):
        services = self.all_seeded()
        self.assertEqual({service.ledger()["revision"] for service in services.values()}, {2})
        ids = []
        for key, service in services.items():
            view = service.ledger()
            self.assertEqual(view["stats"]["allocated_cents"], 122400)
            self.assertEqual((len(view["bank"]), len(view["invoices"]), len(view["allocations"])), (1, 1, 1))
            self.assertEqual(view["allocations"][0]["bank_id"], view["bank"][0]["id"])
            ids.append(view["company"]["id"])
            again = service.import_files({"revision": view["revision"], "bank": upload([bank_row(amount=122400)], "bank", NAMES[key]),
                                           "invoice": upload([invoice_row(amount=122400)], "invoice", NAMES[key])})
            self.assertEqual((len(again["bank"]), len(again["invoices"]), len(again["allocations"])), (1, 1, 1))
            self.assertEqual(again["last_import"]["counts"]["bank"]["duplicate"], 1)
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(len({id(s.lock) for s in services.values()}), 4)
        self.assertEqual(len({str(s.store.path) for s in services.values()}), 4)
        services["haisi"].config["expense_categories"][0]["keywords"].append("隔离测试词")
        self.assertNotIn("隔离测试词", services["moderate"].config["expense_categories"][0]["keywords"])

    def test_mutations_rules_difference_and_revoke_leave_other_three_byte_identical(self):
        services = self.all_seeded(True)
        snapshots = {key: service.store.path.read_bytes() for key, service in services.items()}
        stats = {key: service.expense_statistics({}) for key, service in services.items()}
        service = services["haisi"]
        view = service.ledger()
        bid, iid = view["bank"][0]["id"], view["invoices"][0]["id"]
        def act(method, **payload):
            return getattr(service, method)({"revision": service.ledger()["revision"], **payload})
        act("bank_note", bank_id=bid, note="海思专属备注")
        act("expense_category", bank_id=bid, category="logistics", note="海思专属分类")
        act("settings", aliases={"别名": "测试供应商"}, exclude_special=False, custom_exclude_keywords=["网银手续费"])
        view = act("review", allocations=[dict(bank_id=bid, invoice_id=iid, amount_cents=122400)], note="多开待核实")
        act("invoice_difference", invoice_id=iid, status="carry_forward", note="海思确认跨月")
        act("undo", allocation_id=view["allocations"][0]["id"], note="海思撤回")
        self.assertEqual(service.ledger()["invoices"][0]["difference_status"], "pending")
        for key in NAMES:
            if key != "haisi":
                self.assertEqual(services[key].store.path.read_bytes(), snapshots[key])
                self.assertEqual(services[key].expense_statistics({}), stats[key])
        restarted = CompanyRegistry(self.directory, self.config).get("haisi").ledger()
        self.assertEqual(restarted["bank"][0]["manual_note"], "海思专属备注")
        self.assertEqual(restarted["settings"]["custom_exclude_keywords"], ["网银手续费"])

    def test_wrong_company_file_rejects_entire_import_and_foreign_ids_reject(self):
        a, av = self.seed("haisi", True)
        b, bv = self.seed("huachuangxing", True)
        before = a.store.path.read_bytes()
        for bank_name, invoice_name in ((NAMES["haisi"], NAMES["huachuangxing"]), (NAMES["huachuangxing"], NAMES["haisi"])):
            with self.assertRaises(ValueError):
                a.import_files({"revision": av["revision"], "bank": upload([bank_row("NEW")], "bank", bank_name),
                                "invoice": upload([invoice_row("NEW")], "invoice", invoice_name)})
            self.assertEqual(a.store.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            a.review({"revision": av["revision"], "allocations": [dict(bank_id=av["bank"][0]["id"], invoice_id=bv["invoices"][0]["id"], amount_cents=100)], "note": "尝试跨公司"})
        self.assertEqual(a.store.path.read_bytes(), before)

    def test_company_export_and_old_moderate_links_have_separate_files(self):
        for key, service in self.all_seeded().items():
            export = self.router.post("/api/export", {"company_key": key, "revision": service.ledger()["revision"]})
            for file in export["files"]:
                self.assertTrue(file["url"].startswith(f"/reports/{key}/"))
                path = self.router.file(file["url"])
                self.assertTrue(path.is_relative_to(self.directory / service.config["report_dir"]))
                if key == "moderate":
                    self.assertEqual(self.router.file(file["url"].replace('/moderate/', '/')), path)
                else:
                    self.assertIsNone(self.router.file(file["url"].replace(f'/{key}/', '/moderate/')))

    def test_all_backup_restore_preserves_files_and_company_bindings(self):
        services = self.all_seeded(True)
        for service in services.values():
            service.export({"revision": service.ledger()["revision"]})
        backup = backup_companies(self.directory, self.directory / "temp/packages", all_companies=True)
        records = read_companies(backup["archive"], self.config)
        self.assertEqual(len(records), 4)
        target = self.target()
        restored = restore_companies(target, backup["archive"], all_companies=True)
        self.assertEqual(len(restored["companies"]), 4)
        self.assertEqual(restore_companies(target, backup["archive"], all_companies=True), restored)
        other = CompanyRegistry(target, self.config)
        for key, service in services.items():
            self.assertEqual(other.get(key).store.path.read_bytes(), service.store.path.read_bytes())
            self.assertEqual(other.describe(key)["legal_name"], NAMES[key])
            source_files = {p.relative_to(self.directory / service.config["report_dir"]).as_posix(): p.read_bytes() for p in (self.directory / service.config["report_dir"]).rglob('*') if p.is_file()}
            for name, value in source_files.items():
                self.assertEqual((target / other.get(key).config["report_dir"] / name).read_bytes(), value)

    def test_single_restore_from_bundle_and_inactive_companies_remain_inactive(self):
        self.seed("haisi")
        self.seed("moderate")
        backup = backup_companies(self.directory, self.directory / "temp/packages", all_companies=True)
        records = {r["key"]: r for r in read_companies(backup["archive"], self.config)}
        self.assertFalse(records["dongguan_xunwen"]["initialized"])
        self.assertEqual(records["dongguan_xunwen"]["content"], {})
        target = self.target()
        restore_companies(target, backup["archive"], company_key="haisi")
        registry = CompanyRegistry(target, self.config)
        self.assertTrue(registry.describe("haisi")["initialized"])
        self.assertFalse(registry.ledger_path("moderate").exists())
        restore_companies(target, backup["archive"], all_companies=True)
        self.assertFalse(registry.describe("dongguan_xunwen")["initialized"])
        self.assertFalse((target / "company-data/dongguan_xunwen").exists())

    def test_single_package_company_binding_and_legacy_package_compatible(self):
        self.seed("haisi")
        backup = backup_companies(self.directory, self.directory / "temp/packages", company_key="haisi")
        target = self.target()
        with self.assertRaisesRegex(ValueError, "所选公司"):
            restore_companies(target, backup["archive"], company_key="huachuangxing")
        restore_companies(target, backup["archive"], company_key="haisi")
        self.import_files(bank=self.bank_file(), invoice=self.invoice_file())
        old = backup_companies(self.directory, self.directory / "temp/packages")
        with zipfile.ZipFile(old["archive"]) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            manifest.pop('company_key')
            content = {name: archive.read(name) for name in archive.namelist() if name != 'manifest.json'}
        legacy = self.directory / "temp/legacy.zip"
        legacy.write_bytes(encode_zip(manifest, content))
        restore_companies(target, legacy)
        self.assertEqual((target / LEDGER_PATH).read_bytes(), self.saved())

    def test_all_restore_preflight_conflict_in_last_company_publishes_nothing(self):
        self.all_seeded()
        archive = backup_companies(self.directory, self.directory / "temp/packages", all_companies=True)["archive"]
        target = self.target()
        registry = CompanyRegistry(target, self.config)
        original = registry.activate({"company_key": "huachuangxing", "legal_name": NAMES["huachuangxing"]}).store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "不覆盖"):
            restore_companies(target, archive, all_companies=True)
        for key in ("haisi", "moderate", "dongguan_xunwen"):
            self.assertFalse(registry.ledger_path(key).exists())
        self.assertEqual(registry.ledger_path("huachuangxing").read_bytes(), original)

    def test_restore_interruption_reports_partial_and_retry_finishes(self):
        services = self.all_seeded()
        archive = backup_companies(self.directory, self.directory / "temp/packages", all_companies=True)["archive"]
        target = self.target()
        def interrupt(source, destination):
            if "dongguan_xunwen" in Path(destination).parts:
                raise OSError("模拟第三家公司发布中断")
            replace_snapshot(source, destination)
        with self.assertRaisesRegex(OSError, "恢复未完成.*haisi.*moderate"):
            restore_companies(target, archive, all_companies=True, publisher=interrupt)
        registry = CompanyRegistry(target, self.config)
        self.assertTrue(registry.ledger_path("haisi").exists())
        self.assertFalse(registry.ledger_path("dongguan_xunwen").exists())
        restore_companies(target, archive, all_companies=True)
        for key, service in services.items():
            self.assertEqual(registry.ledger_path(key).read_bytes(), service.store.path.read_bytes())

    def test_tampered_bundle_unknown_company_and_path_rejected(self):
        self.seed("haisi")
        archive = backup_companies(self.directory, self.directory / "temp/packages", all_companies=True)["archive"]
        with zipfile.ZipFile(archive) as source:
            original = json.loads(source.read('manifest.json'))
            content = {name: source.read(name) for name in source.namelist() if name != 'manifest.json'}
        for index, kind in enumerate(("hash", "path", "duplicate", "name")):
            manifest = copy.deepcopy(original)
            if kind == "hash":
                manifest["companies"][0]["sha256"] = '0' * 64
            elif kind == "path":
                manifest["companies"][0]["archive"] = '../ledger/company-ledger.v1.json'
            elif kind == "duplicate":
                manifest["companies"][1]["key"] = 'haisi'
            else:
                manifest["companies"][0]["legal_name"] = '另一家公司'
            path = self.directory / f"temp/tampered-{index}.zip"
            path.write_bytes(encode_zip(manifest, content))
            target = self.target(f"rejected-{index}")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                restore_companies(target, path, all_companies=True)
            self.assertFalse(list(target.rglob('company-ledger.v1.json')))

    def test_independent_http_pages_same_revision_and_locks(self):
        services = self.all_seeded()
        server = make_server(self.registry, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def call(route, data=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request("POST" if data is not None else "GET", route,
                                   json.dumps(data).encode('utf-8') if data is not None else None,
                                   {'Content-Type': 'application/json'})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        try:
            self.assertEqual(len(call('/api/companies')[1]['companies']), 4)
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda key: call('/api/bank-note', dict(company_key=key, revision=2,
                    bank_id=services[key].ledger()['bank'][0]['id'], note='只属于' + key)), NAMES))
            self.assertEqual([status for status, _ in results], [200] * 4)
            for key, (status, view) in zip(NAMES, results):
                self.assertEqual(view['company_key'], key)
                self.assertEqual(view['bank'][0]['manual_note'], '只属于' + key)
            self.assertEqual(call('/api/settings', dict(revision=3, aliases={}))[0], 400)
            self.assertEqual(call('/api/bootstrap?company_key=haisi&company_key=moderate')[0], 400)
            with services['haisi'].store.exclusive():
                other = services['moderate'].ledger()
                status, _ = call('/api/bank-note', dict(company_key='moderate', revision=other['revision'], bank_id=other['bank'][0]['id'], note='其他公司锁不阻塞'))
                self.assertEqual(status, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_old_source_config_and_empty_all_backup_are_compatible(self):
        old = dict(self.config)
        old.pop("companies")
        path = self.directory / "config/defaults.json"
        path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        original = path.read_bytes()
        archive = backup_companies(self.directory, self.directory / "temp/packages", all_companies=True)["archive"]
        records = read_companies(archive, self.config)
        self.assertEqual(len(records), 4)
        self.assertFalse(any(record["has_ledger"] for record in records))
        target = self.target()
        self.assertEqual(restore_companies(target, archive, all_companies=True)["companies"], [])
        self.assertFalse(list(target.rglob("company-ledger.v1.json")))
        self.assertEqual(path.read_bytes(), original)

    def test_inactive_target_with_unknown_files_blocks_whole_restore(self):
        self.seed("haisi")
        archive = backup_companies(self.directory, self.directory / "temp/packages", all_companies=True)["archive"]
        target = self.target()
        unknown = target / "company-data/huachuangxing/reports/unexpected.txt"
        unknown.parent.mkdir(parents=True)
        unknown.write_text("原有数据", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "目标已有数据"):
            restore_companies(target, archive, all_companies=True)
        self.assertEqual(unknown.read_text(encoding="utf-8"), "原有数据")
        self.assertFalse(list(target.rglob("company-ledger.v1.json")))

    def test_parent_file_collision_rejected_before_any_publication(self):
        self.seed("haisi")
        archive = backup_companies(self.directory, self.directory / "temp/packages", company_key="haisi")["archive"]
        with zipfile.ZipFile(archive) as source:
            manifest = json.loads(source.read('manifest.json'))
            content = {name: source.read(name) for name in source.namelist() if name != 'manifest.json'}
        for name in ('reports/collision', 'reports/collision/file.txt'):
            content[name] = b'file'
            manifest['files'].append(dict(path=name, size=4, sha256=digest(b'file')))
        bad = self.directory / 'temp/parent-collision.zip'
        bad.write_bytes(encode_zip(manifest, content))
        target = self.target()
        with self.assertRaisesRegex(ValueError, "父目录路径冲突"):
            restore_companies(target, bad, company_key='haisi')
        self.assertFalse((target / 'company-data').exists())

    def test_overlapping_data_configuration_is_rejected(self):
        for changes in (dict(ledger_dir='company-data/haisi/ledger'), dict(report_dir='ledger/reports'), dict(ledger_dir='.')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                CompanyRegistry(self.directory, dict(self.config, **changes))

    def test_existing_binding_wrong_name_and_unfinished_activation_do_not_overwrite(self):
        self.seed('haisi')
        archive = backup_companies(self.directory, self.directory / 'temp/packages', company_key='haisi')['archive']
        target = self.target()
        registry = CompanyRegistry(target, self.config)
        service = registry.activate(dict(company_key='haisi', legal_name='另一个已确认全称'))
        original = service.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '名称'):
            restore_companies(target, archive, company_key='haisi')
        self.assertEqual(service.store.path.read_bytes(), original)
        partial = target / 'company-data/dongguan_xunwen/reports/part.txt'
        partial.parent.mkdir(parents=True)
        partial.write_text('未完成恢复', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '已有数据'):
            registry.activate(dict(company_key='dongguan_xunwen', legal_name=NAMES['dongguan_xunwen']))
        self.assertFalse(registry.ledger_path('dongguan_xunwen').exists())

    def test_corrupt_other_company_does_not_hide_valid_company(self):
        self.seed('moderate')
        service, _ = self.seed('haisi')
        service.store.path.write_text('{broken', encoding='utf-8')
        data = self.router.get('/api/bootstrap', dict(company_key='moderate'))
        self.assertEqual(data['result']['stats']['bank_count'], 1)
        self.assertTrue(next(p for p in data['companies'] if p['key'] == 'haisi')['error'])
        with self.assertRaises(ValueError):
            self.router.get('/api/bootstrap', dict(company_key='haisi'))
