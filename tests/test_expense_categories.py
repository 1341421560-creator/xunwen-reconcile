import copy
import csv
from pathlib import Path
from fault_support import FaultCase, CONFIG, bank_row
from reconcile.expense_categories import describe_expense


class ExpenseCategoryTests(FaultCase):
    def test_four_categories_match_only_the_requested_keywords(self):
        cases = [("缴税", "tax"), ("2026年8月缴税付款", "tax"),
                 ("代运营服务", "operations"), ("8月代运营服务费", "operations"),
                 ("报销款", "reimbursement"), ("员工差旅报销款补付", "reimbursement"),
                 ("租金", "rent"), ("补付9月租金", "rent"),
                 ("税款", "other"), ("运营费", "other"), ("代运营", "other"),
                 ("报销", "other"), ("租赁费", "other"), ("普通货款", "goods")]
        for summary, category in cases:
            with self.subTest(summary=summary):
                result = describe_expense(dict(bank_row(), summary=summary), self.config["expense_categories"])
                self.assertEqual(result["expense_category"], category)

    def test_new_categories_win_over_existing_categories_without_order_dependence(self):
        cases = [("采购货款缴税", "tax", "缴税"),
                 ("人工工资代运营服务", "operations", "代运营服务"),
                 ("物流运输费报销款", "reimbursement", "报销款"),
                 ("租金水电费", "rent", "租金")]
        for categories in (self.config["expense_categories"], list(reversed(self.config["expense_categories"]))):
            for summary, category, keyword in cases:
                with self.subTest(summary=summary, first_category=categories[0]["id"]):
                    result = describe_expense(dict(bank_row(), summary=summary), categories)
                    self.assertEqual(result, {"expense_category": category, "expense_category_reason": "摘要命中：" + keyword})

    def test_equal_highest_priority_is_ambiguous_and_same_category_keywords_are_not(self):
        for summary in ("报销款租金", "代运营服务缴税及货款", "工资物流费"):
            with self.subTest(summary=summary):
                result = describe_expense(dict(bank_row(), summary=summary), self.config["expense_categories"])
                self.assertEqual(result["expense_category"], "other")
                self.assertIn("多个最高优先级类别", result["expense_category_reason"])
        result = describe_expense(dict(bank_row(), summary="货款采购"), self.config["expense_categories"])
        self.assertEqual(result["expense_category"], "goods")

    def test_manual_category_wins_and_income_has_no_expense_category(self):
        bank = dict(bank_row(), summary="报销款租金", expense_classification={"category": "labor", "note": "确认代付人工", "updated_at": "2026-09-09"})
        result = describe_expense(bank, self.config["expense_categories"])
        self.assertEqual(result, {"expense_category": "labor", "expense_category_reason": "人工分类：确认代付人工"})
        bank["direction"] = "收入"
        self.assertEqual(describe_expense(bank, self.config["expense_categories"])["expense_category"], "")

    def test_new_categories_statistics_dates_exclusion_and_export_are_consistent(self):
        cases = [("缴税货款", "tax", "缴税", 10001), ("代运营服务人工费", "operations", "运营", 20002),
                 ("报销款物流费", "reimbursement", "报销款", 30003), ("租金水电费", "rent", "租金", 40004)]
        rows = [dict(bank_row("NEW" + str(index), amount, "服务公司", "2026-08-15"), summary=summary)
                for index, (summary, _, _, amount) in enumerate(cases)]
        rows.append(dict(bank_row("NEXT", 50005, "服务公司", "2026-09-01"), summary="租金"))
        rows.append(dict(bank_row("IN", 60006), summary="报销款", direction="收入", debit_cents=0, credit_cents=60006))
        view = self.import_files(bank=self.bank_file(rows))
        self.assertEqual(view["bank"][0]["status"], "excluded")
        before = self.saved()
        stats = self.service.expense_statistics({"start_date": "2026-08-01", "end_date": "2026-08-31"})
        totals = {item["id"]: item["debit_cents"] for item in stats["categories"]}
        for _, category, _, amount in cases:
            self.assertEqual(totals[category], amount)
        self.assertEqual((stats["count"], stats["debit_cents"]), (4, 100010))
        exported = self.service.export({"revision": view["revision"], "month": "2026-08"})
        with (Path(exported["directory"]) / "流水对账结果.csv").open(encoding="utf-8-sig", newline="") as stream:
            exported_rows = {row["摘要"]: row for row in csv.DictReader(stream)}
        for summary, _, label, _ in cases:
            self.assertEqual(exported_rows[summary]["支出类别"], label)
            self.assertTrue(exported_rows[summary]["支出分类依据"].startswith("摘要命中："))
        self.assertEqual(self.saved(), before)

    def test_upgrade_recalculates_history_and_preserves_saved_notes_and_manual_category(self):
        current = copy.deepcopy(CONFIG)
        old_config = copy.deepcopy(current)
        old_config["expense_categories"] = [item for item in old_config["expense_categories"] if item["id"] not in {"tax", "operations", "reimbursement", "rent"}]
        self.service = type(self.service)(self.directory, old_config)
        view = self.import_files(bank=self.bank_file([dict(bank_row("AUTO", 11000), summary="报销款物流费"),
                                                     dict(bank_row("MANUAL", 22000), summary="租金水电费")]))
        first, second = [row["id"] for row in view["bank"]]
        view = self.service.bank_note({"revision": view["revision"], "bank_id": first, "note": "更新前已保存\n保留备注"})
        view = self.service.expense_category({"revision": view["revision"], "bank_id": second, "category": "goods", "note": "人工确认"})
        self.assertEqual([row["expense_category"] for row in view["bank"]], ["logistics", "goods"])
        before = self.saved()
        self.service = type(self.service)(self.directory, current)
        view = self.ledger()
        self.assertEqual([row["expense_category"] for row in view["bank"]], ["reimbursement", "goods"])
        self.assertEqual(view["bank"][0]["manual_note"], "更新前已保存\n保留备注")
        self.assertEqual(view["bank"][0]["id"], first)
        self.assertEqual(self.saved(), before)
        view = self.service.expense_category({"revision": view["revision"], "bank_id": second, "category": "auto"})
        self.assertEqual(view["bank"][1]["expense_category"], "rent")
        self.service = type(self.service)(self.directory, current)
        self.assertEqual(self.ledger()["bank"][0]["manual_note"], "更新前已保存\n保留备注")

    def test_each_new_category_can_be_manually_selected_then_returned_to_auto(self):
        view = self.import_files(bank=self.bank_file([dict(bank_row(), summary="未分类支出")]))
        bank_id = view["bank"][0]["id"]
        for category in ("tax", "operations", "reimbursement", "rent"):
            view = self.service.expense_category({"revision": view["revision"], "bank_id": bank_id, "category": category, "note": "人工确认"})
            self.assertEqual(view["bank"][0]["expense_category"], category)
            self.assertEqual(view["bank"][0]["expense_category_reason"], "人工分类：人工确认")
        view = self.service.expense_category({"revision": view["revision"], "bank_id": bank_id, "category": "auto"})
        self.assertEqual(view["bank"][0]["expense_category"], "other")
