import io
import json
import shutil
import zipfile
from pathlib import Path
from unittest.mock import patch
from fault_support import FaultCase, ROOT, bank_row, invoice_row
from deployment.archive_validation import LEDGER_PATH, digest, read_archive
from deployment.data_backup import backup_data
from deployment.data_restore import restore_data


class DistributionTests(FaultCase):
    def setUp(self):
        super().setUp()
        (self.directory / "config").mkdir()
        (self.directory / "config" / "defaults.json").write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        self.target = self.directory / "new-computer"
        (self.target / "config").mkdir(parents=True)
        shutil.copy2(self.directory / "config" / "defaults.json", self.target / "config" / "defaults.json")

    def make_backup(self):
        self.import_files(bank=self.bank_file(), invoice=self.invoice_file())
        self.service.export({"revision": self.ledger()["revision"]})
        (self.directory / "temp" / "private-cache.txt").write_text("不应进入迁移包", encoding="utf-8")
        return Path(backup_data(self.directory, self.directory / "data-transfer")["archive"])

    def rewrite(self, archive, change):
        with zipfile.ZipFile(archive) as source:
            entries = [(name, source.read(name)) for name in source.namelist()]
        change(entries)
        output = self.directory / ("changed-" + str(len(list(self.directory.glob('changed-*.zip')))) + ".zip")
        with zipfile.ZipFile(output, "x") as destination:
            for name, value in entries:
                destination.writestr(name, value)
        return output

    def test_backup_restore_roundtrip_and_idempotence(self):
        archive = self.make_backup()
        before = self.saved()
        original = self.ledger()
        restored = restore_data(self.target, archive)
        self.assertEqual(restored["bank_count"], 1)
        self.assertEqual((self.target / LEDGER_PATH).read_bytes(), before)
        self.assertEqual(restore_data(self.target, archive), restored)
        other = type(self.service)(self.target, self.config)
        self.assertEqual(other.ledger()["allocations"], original["allocations"])
        self.assertEqual(other.ledger()["audit"], original["audit"])

    def test_backup_contains_only_data_and_preserves_source(self):
        archive = self.make_backup()
        original = self.saved()
        manifest, content = read_archive(archive, self.config["company_name"])
        self.assertTrue(all(name.split('/')[0] in ("ledger", "sessions", "reports") for name in content))
        self.assertFalse(any('private-cache' in name for name in content))
        self.assertEqual(manifest["revision"], self.ledger()["revision"])
        self.assertEqual(self.saved(), original)

    def test_existing_different_ledger_is_not_overwritten(self):
        archive = self.make_backup()
        other = type(self.service)(self.target, self.config)
        other.import_files({"revision": 0, "bank": self.bank_file([bank_row("DIFFERENT", 500)])})
        original = other.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "不覆盖"):
            restore_data(self.target, archive)
        self.assertEqual(other.store.path.read_bytes(), original)

    def test_restore_interrupted_before_ledger_publish_can_retry(self):
        archive = self.make_backup()
        from deployment import data_restore
        original = data_restore.replace_snapshot
        def interrupted(source, target):
            if Path(target).name == "company-ledger.v1.json":
                raise OSError("模拟恢复发布前中断")
            return original(source, target)
        with patch.object(data_restore, "replace_snapshot", interrupted):
            with self.assertRaises(OSError):
                restore_data(self.target, archive)
        self.assertFalse((self.target / LEDGER_PATH).exists())
        restore_data(self.target, archive)
        self.assertEqual((self.target / LEDGER_PATH).read_bytes(), self.saved())

    def test_tampered_archive_is_rejected_before_writing(self):
        archive = self.make_backup()
        def change(entries):
            index = next(i for i, (name, _) in enumerate(entries) if name == LEDGER_PATH)
            entries[index] = (LEDGER_PATH, b'{}')
        changed = self.rewrite(archive, change)
        with self.assertRaisesRegex(ValueError, "校验失败"):
            restore_data(self.target, changed)
        self.assertFalse((self.target / LEDGER_PATH).exists())

    def test_wrong_company_is_rejected(self):
        archive = self.make_backup()
        config = dict(self.config, company_name="另一家公司")
        (self.target / "config" / "defaults.json").write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "公司"):
            restore_data(self.target, archive)
        self.assertFalse((self.target / LEDGER_PATH).exists())

    def test_traversal_and_windows_reserved_names_are_rejected(self):
        from deployment.archive_validation import check_member
        for name in ("../outside.json", "reports/../outside", "reports/a:stream", "reports/CON.txt", "reports/a.", "reports//a", "reports\\a", "/ledger/a", "config/defaults.json", "ledger/other.json"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                check_member(name)

    def test_extra_file_not_in_manifest_is_rejected(self):
        archive = self.make_backup()
        changed = self.rewrite(archive, lambda entries: entries.append(("reports/extra.txt", b"extra")))
        with self.assertRaises(ValueError):
            restore_data(self.target, changed)
        self.assertFalse((self.target / LEDGER_PATH).exists())

    def test_case_insensitive_duplicate_is_rejected(self):
        archive = self.make_backup()
        changed = self.rewrite(archive, lambda entries: entries.append(("LEDGER/company-ledger.v1.json", self.saved())))
        with self.assertRaisesRegex(ValueError, "重复文件名"):
            restore_data(self.target, changed)

    def test_unknown_preexisting_data_blocks_restore(self):
        archive = self.make_backup()
        directory = self.target / "sessions"
        directory.mkdir()
        (directory / "unknown.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "之外的数据"):
            restore_data(self.target, archive)
        self.assertFalse((self.target / LEDGER_PATH).exists())

    def test_empty_ledger_backup_has_clear_error(self):
        with self.assertRaisesRegex(ValueError, "没有正式账本"):
            backup_data(self.directory, self.directory / "data-transfer")

    def test_dependency_archives_match_locked_hashes(self):
        runtime = json.loads((ROOT / "config" / "runtime.json").read_text(encoding="utf-8"))
        for item in runtime["archives"]:
            with self.subTest(file=item["file"]):
                content = (ROOT / item["file"]).read_bytes()
                self.assertEqual(len(content), item["size"])
                self.assertEqual(digest(content), item["sha256"])
