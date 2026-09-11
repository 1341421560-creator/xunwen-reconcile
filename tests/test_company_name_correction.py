import hashlib
import json
import zipfile
from copy import deepcopy
from unittest.mock import patch
from fault_support import FaultCase, bank_row, invoice_row, upload
from reconcile.company_registry import CompanyRegistry
from reconcile.request_router import RequestRouter
from reconcile.company_name_correction import correction_availability
from deployment.company_transfer import backup_companies, restore_companies


OLD_NAME = "测试公司简称"
FULL_NAME = "隔离测试完整公司有限公司"
ROUTE = "/api/companies/correct-name"


class CompanyNameCorrectionTests(FaultCase):
    def setUp(self):
        super().setUp()
        (self.directory / "config").mkdir()
        (self.directory / "config/defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        self.registry = CompanyRegistry(self.directory, self.config)
        self.router = RequestRouter(self.registry)
        self.target = self.registry.activate({"company_key": "haisi", "legal_name": OLD_NAME})

    def payload(self, **changes):
        view = self.target.ledger()
        return {"company_key": "haisi", "company_id": view["company"]["id"], "original_name": OLD_NAME,
                "revision": view["revision"], "legal_name": FULL_NAME, "confirmed": True, **changes}

    def reject(self, payload, expected=None):
        before = self.target.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, expected or ".*"):
            self.router.post(ROUTE, payload)
        self.assertEqual(self.target.store.path.read_bytes(), before)

    def test_correct_name_preserves_identity_rules_backup_and_restart(self):
        self.target.settings({"revision": 1, "aliases": {"测试别名": "测试全称"}, "exclude_special": False, "custom_exclude_keywords": ["测试费用"]})
        old = self.target.store.load()
        original = self.target.store.path.read_bytes()
        config_bytes = (self.directory / "config/defaults.json").read_bytes()
        result = self.router.post(ROUTE, self.payload())
        after = CompanyRegistry(self.directory, self.config).get("haisi").store.load()
        self.assertEqual(after["company"], {**old["company"], "name": FULL_NAME})
        self.assertEqual(after["settings"], old["settings"])
        self.assertEqual(after["revision"], old["revision"] + 1)
        self.assertEqual(after["audit"][:-1], old["audit"])
        self.assertEqual(after["audit"][-1]["action"], "correct_company_name")
        backup = result["name_correction"]["backup"]
        self.assertEqual((self.directory / backup["path"]).read_bytes(), original)
        self.assertEqual(backup["sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual((self.directory / "config/defaults.json").read_bytes(), config_bytes)
        self.assertEqual(result["company_profile"]["legal_name"], FULL_NAME)
        self.assertEqual(self.registry.get("haisi").config["company_name"], FULL_NAME)

    def test_import_after_correction_uses_new_full_name(self):
        result = self.router.post(ROUTE, self.payload())
        service = self.registry.get("haisi")
        with self.assertRaises(ValueError):
            service.import_files({"revision": result["result"]["revision"], "bank": upload([bank_row()], "bank", OLD_NAME)})
        view = service.import_files({"revision": result["result"]["revision"], "bank": upload([bank_row()], "bank", FULL_NAME),
                                     "invoice": upload([invoice_row()], "invoice", FULL_NAME)})
        self.assertEqual((len(view["bank"]), len(view["invoices"]), len(view["allocations"])), (1, 1, 1))
        self.assertFalse(self.router.bootstrap({"company_key": "haisi"})["company_name_correction"]["allowed"])

    def test_existing_bank_or_invoice_blocks_correction(self):
        for key, kind in (("haisi", "bank"), ("huachuangxing", "invoice")):
            service = self.target if key == "haisi" else self.registry.activate({"company_key": key, "legal_name": "测试另一公司"})
            name = service.config["company_name"]
            service.import_files({"revision": 1, kind: upload([bank_row()] if kind == "bank" else [invoice_row()], kind, name)})
            before = service.store.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "已有导入数据"):
                self.router.post(ROUTE, self.payload(company_key=key, company_id=service.ledger()["company"]["id"], original_name=name, revision=2))
            self.assertEqual(service.store.path.read_bytes(), before)

    def test_stale_page_wrong_identity_missing_confirmation_and_invalid_names(self):
        cases = [dict(revision=0), dict(revision=True), dict(company_id="wrong"), dict(original_name="不同公司"),
                 dict(confirmed=False), dict(confirmed="true"), dict(company_key="../haisi")]
        cases += [dict(legal_name=value) for value in (None, "", " ", [], "名称\n换行", "名" * 201)]
        for values in cases:
            with self.subTest(values=values):
                self.reject(self.payload(**values))
        payload = self.payload()
        payload.pop("company_key")
        self.reject(payload, "缺少公司标识")

    def test_duplicate_company_name_is_rejected_and_other_ledger_unchanged(self):
        other = self.registry.activate({"company_key": "huachuangxing", "legal_name": FULL_NAME})
        before = other.store.path.read_bytes()
        self.reject(self.payload(), "已经绑定")
        self.reject(self.payload(legal_name=self.config["company_name"]), "已经绑定")
        self.assertEqual(other.store.path.read_bytes(), before)

    def test_no_change_does_not_write_or_backup(self):
        before = self.target.store.path.read_bytes()
        result = self.router.post(ROUTE, self.payload(legal_name=OLD_NAME))
        self.assertFalse(result["name_correction"]["changed"])
        self.assertEqual(self.target.store.path.read_bytes(), before)
        self.assertFalse((self.target.store.directory / "name-correction-backups").exists())

    def test_backup_or_commit_failure_preserves_original_and_retry_works(self):
        payload = self.payload()
        before = self.target.store.path.read_bytes()
        for target in ("reconcile.ledger_backup.replace_snapshot", "reconcile.ledger_storage.replace_snapshot"):
            with patch(target, side_effect=OSError("模拟磁盘写入失败")), self.assertRaises(OSError):
                self.router.post(ROUTE, payload)
            self.assertEqual(self.target.store.path.read_bytes(), before)
            self.assertEqual(self.registry.get("haisi").ledger()["company"]["name"], OLD_NAME)
        self.assertTrue(self.router.post(ROUTE, payload)["name_correction"]["changed"])

    def test_simultaneous_import_after_backup_rejects_stale_correction(self):
        from reconcile.ledger_backup import backup_snapshot
        external = CompanyRegistry(self.directory, self.config).get("haisi")
        def interleave(*args):
            result = backup_snapshot(*args)
            external.import_files({"revision": 1, "bank": upload([bank_row()], "bank", OLD_NAME)})
            return result
        with patch("reconcile.company_name_correction.backup_snapshot", side_effect=interleave), self.assertRaisesRegex(ValueError, "过期"):
            self.router.post(ROUTE, self.payload())
        view = self.target.ledger()
        self.assertEqual(view["company"]["name"], OLD_NAME)
        self.assertEqual(len(view["bank"]), 1)

    def test_double_submit_rejected_and_old_service_cannot_write(self):
        payload = self.payload()
        self.router.post(ROUTE, payload)
        before = self.target.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "版本号"):
            self.router.post(ROUTE, payload)
        with self.assertRaisesRegex(ValueError, "公司与当前配置不符"):
            self.target.import_files({"revision": 2, "bank": upload([bank_row()], "bank", OLD_NAME)})
        self.assertEqual(self.target.store.path.read_bytes(), before)

    def test_history_accounts_batches_and_migrations_prevent_empty_correction(self):
        original = self.target.store.load()
        for field in ("bank", "invoices", "batches", "allocations", "conflicts", "blocked_pairs", "migrations"):
            value = deepcopy(original)
            value[field] = [{}]
            self.assertFalse(correction_availability(self.target, value)["allowed"])
        value = deepcopy(original)
        value["company"]["accounts"] = ["123456"]
        self.assertFalse(correction_availability(self.target, value)["allowed"])
        sessions = self.directory / self.target.config["session_dir"]
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "old.json").write_text("{}", encoding="utf-8")
        self.reject(self.payload(), "旧版历史")

    def test_backup_restore_includes_correction_snapshot_and_binding(self):
        result = self.router.post(ROUTE, self.payload())
        archive = backup_companies(self.directory, self.directory / "temp/packages", company_key="haisi")["archive"]
        target = self.directory / "restored"
        (target / "config").mkdir(parents=True)
        (target / "config/defaults.json").write_bytes((self.directory / "config/defaults.json").read_bytes())
        restore_companies(target, archive, company_key="haisi")
        self.assertEqual(CompanyRegistry(target, self.config).get("haisi").ledger()["company"]["name"], FULL_NAME)
        backup_path = result["name_correction"]["backup"]["path"]
        self.assertEqual((target / backup_path).read_bytes(), (self.directory / backup_path).read_bytes())

    def test_default_config_binding_keeps_existing_guard(self):
        service = self.registry.get("moderate")
        self.assertFalse(correction_availability(service, service.store.load())["allowed"])
        service.settings({"revision": 0, "aliases": {}, "exclude_special": True, "custom_exclude_keywords": []})
        changed = {**self.config, "company_name": "另一默认公司"}
        with self.assertRaisesRegex(ValueError, "配置不符"):
            CompanyRegistry(self.directory, changed).get("moderate")

    def test_backup_paths_remain_strict(self):
        from deployment.archive_validation import check_member
        check_member("ledger/name-correction-backups/before-" + "a" * 32 + ".json")
        for name in ("ledger/unknown.json", "ledger/name-correction-backups/unknown.json",
                     "ledger/name-correction-backups/../company-ledger.v1.json",
                     "ledger/name-correction-backups/before-" + "a" * 32 + ".exe"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                check_member(name)

    def test_restore_rejects_backup_belonging_to_another_company(self):
        from deployment.company_archive import encode_zip
        from deployment.archive_validation import digest
        self.router.post(ROUTE, self.payload())
        archive = backup_companies(self.directory, self.directory / "temp/packages", company_key="haisi")["archive"]
        with zipfile.ZipFile(archive) as source:
            manifest = json.loads(source.read("manifest.json"))
            content = {name: source.read(name) for name in source.namelist() if name != "manifest.json"}
        name = next(name for name in content if "name-correction-backups" in name)
        snapshot = json.loads(content[name])
        snapshot["company"]["id"] = "Canothercompany"
        content[name] = json.dumps(snapshot).encode("utf-8")
        record = next(record for record in manifest["files"] if record["path"] == name)
        record.update(size=len(content[name]), sha256=digest(content[name]))
        changed = self.directory / "temp/foreign-backup.zip"
        changed.write_bytes(encode_zip(manifest, content))
        target = self.directory / "restore-rejected"
        (target / "config").mkdir(parents=True)
        (target / "config/defaults.json").write_bytes((self.directory / "config/defaults.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "备份与当前账本身份"):
            restore_companies(target, changed, company_key="haisi")
        self.assertFalse(CompanyRegistry(target, self.config).ledger_path("haisi").exists())
