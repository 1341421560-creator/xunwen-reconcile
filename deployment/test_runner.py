import json
import os
import time
import unittest
from pathlib import Path
from uuid import uuid4


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def startTest(self, test):
        self.started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        failures = [detail for case, detail in self.failures + self.errors if case == test or getattr(case, "test_case", None) == test]
        skips = [reason for case, reason in self.skipped if case == test]
        self.records.append({"test": test.id(), "status": "failed" if failures else "skipped" if skips else "passed",
                             "seconds": round(time.perf_counter() - self.started, 3), "failures": failures, "skip_reasons": skips})
        super().stopTest(test)


def run_tests(root, pattern):
    root = Path(root)
    directory = root / "temp" / ("tests-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6])
    directory.mkdir(parents=True)
    os.environ["RECON_FAULT_TEMP"] = str(directory / "cases")
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=pattern)
    with (directory / "test-output.txt").open("w", encoding="utf-8") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=RecordedResult).run(suite)
    summary = {"tests": result.testsRun, **{status: sum(r["status"] == status for r in result.records) for status in ("passed", "failed", "skipped")}, "records": result.records}
    (directory / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "records"}, ensure_ascii=False))
    print("逐项结果：" + str(directory))
    for record in result.records:
        if record["status"] == "failed":
            print(record["test"] + "\n" + "\n".join(record["failures"]))
    return 0 if result.wasSuccessful() else 1
