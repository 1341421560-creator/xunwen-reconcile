from pathlib import Path
import unittest
from unittest.mock import call, patch
from reconcile.atomic_write import replace_snapshot


class AtomicWriteTests(unittest.TestCase):
    def test_exact_transient_retry_sequence_without_external_file_interference(self):
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                error = PermissionError("模拟短暂占用")
                error.winerror = code
                stage, target = Path("temp/staged.json"), Path("temp/ledger.json")
                with patch("reconcile.atomic_write.os.replace", side_effect=[error, error, None]) as replace, patch("reconcile.atomic_write.time.sleep") as sleep:
                    replace_snapshot(stage, target)
                self.assertEqual(replace.call_args_list, [call(stage, target)] * 3)
                self.assertEqual(sleep.call_args_list, [call(0.05), call(0.1)])

    def test_persistent_failure_stops_after_six_attempts(self):
        error = PermissionError("模拟持续占用")
        error.winerror = 32
        with patch("reconcile.atomic_write.os.replace", side_effect=error) as replace, patch("reconcile.atomic_write.time.sleep") as sleep:
            with self.assertRaises(PermissionError):
                replace_snapshot(Path("temp/staged.json"), Path("temp/ledger.json"))
        self.assertEqual(replace.call_count, 6)
        self.assertEqual(sleep.call_args_list, [call(0.05), call(0.1), call(0.2), call(0.4), call(0.8)])

    def test_non_retryable_errors_are_raised_immediately(self):
        for error in (PermissionError("非 Windows 占用"), OSError("磁盘故障")):
            with self.subTest(error=type(error).__name__):
                with patch("reconcile.atomic_write.os.replace", side_effect=error) as replace, patch("reconcile.atomic_write.time.sleep") as sleep:
                    with self.assertRaises(type(error)):
                        replace_snapshot(Path("temp/staged.json"), Path("temp/ledger.json"))
                self.assertEqual(replace.call_count, 1)
                sleep.assert_not_called()
