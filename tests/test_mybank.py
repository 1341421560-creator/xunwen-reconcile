import base64
import copy
from fault_support import FaultCase, bank_row, invoice_row, upload
from mybank_fixtures import mybank_row, mybank_sheet, workbook_upload
from reconcile.company_registry import CompanyRegistry
from reconcile.excel_reader import read_sheets
from reconcile.import_reader import read_uploads


class MybankTests(FaultCase):
    def test_duplicate_header_columns_rejected(self):
        for index in (0, 4, 5, 7):
            sheet = self.sheet()
            sheet["rows"][4].append(sheet["rows"][4][index])
            self.assert_rejected_unchanged(lambda: self.import_sheet(sheet))

    def sheet(self, rows=None, **kwargs):
        return mybank_sheet(self.config["company_name"], rows, **kwargs)

    def import_sheet(self, sheet):
        return self.import_files(bank=workbook_upload([sheet]))

    def test_receipt_payment_direction_and_trade_time(self):
        sheet = self.sheet()
        sheet["rows"][5][1] = "2026-01-01 00:00:00"
        result = self.import_sheet(sheet)
        payment, receipt = result["bank"]
        self.assertEqual((payment["debit_cents"], payment["credit_cents"], payment["direction"]), (1200000, 0, "支出"))
        self.assertEqual((receipt["debit_cents"], receipt["credit_cents"], receipt["direction"]), (0, 2500000, "收入"))
        self.assertEqual(receipt["status"], "excluded")
        self.assertEqual(payment["date"], "2026-03-30")
        self.assertEqual(payment["reference"], sheet["rows"][5][0])
        self.assertEqual(payment["account"], "8888000000000001")
        self.assertEqual(payment["bank"], "测试对方银行")
        self.assertEqual(payment["source_bank"], "网商银行")
        self.assertEqual(payment["row"], 6)
        self.assertEqual(len(result["last_import"]["controls"]), 2)
        self.assertTrue(all(c["expected_count"] == c["actual_count"] == 1 for c in result["last_import"]["controls"]))

    def test_each_company_supports_mybank_and_cross_month_invoice(self):
        registry = CompanyRegistry(self.directory, self.config)
        services = {}
        for profile in self.config["companies"]:
            key = profile["key"]
            name = self.config["company_name"] if key == "moderate" else "格式验证" + profile["label"] + "有限公司"
            service = registry.get(key) if key == "moderate" else registry.activate({"company_key": key, "legal_name": name})
            sheet = mybank_sheet(name)
            result = service.import_files({"revision": service.ledger()["revision"], "bank": workbook_upload([sheet])})
            result = service.import_files({"revision": result["revision"], "invoice": upload([invoice_row()], "invoice", name)})
            self.assertEqual(result["bank"][0]["status"], "matched")
            self.assertEqual(result["last_import"]["historical_matched_count"], 1)
            services[key] = service
        snapshots = {key: service.store.path.read_bytes() for key, service in services.items()}
        with self.assertRaisesRegex(ValueError, "当前公司"):
            services["haisi"].import_files({"revision": services["haisi"].ledger()["revision"], "bank": workbook_upload([self.sheet()])})
        self.assertTrue(all(service.store.path.read_bytes() == snapshots[key] for key, service in services.items()))

    def test_arbitrary_company_and_account_not_bound_to_sample(self):
        config = dict(self.config, company_name="任意新主体测试有限公司")
        parsed = read_uploads({"bank": workbook_upload([mybank_sheet(config["company_name"], account="0099000000000042")])}, config)
        self.assertEqual({row["account"] for row in parsed["bank"]}, {"0099000000000042"})

    def test_same_file_and_overlapping_months_deduplicated(self):
        self.import_sheet(self.sheet())
        result = self.import_sheet(self.sheet())
        self.assertEqual((len(result["bank"]), result["last_import"]["counts"]["bank"]["duplicate"]), (2, 2))
        rows = [mybank_row(), mybank_row("NEXT", payment="20.00", date="2026-04-01 10:00:00")]
        result = self.import_sheet(self.sheet(rows))
        self.assertEqual(len(result["bank"]), 3)
        self.assertEqual(result["last_import"]["counts"]["bank"], {"new": 1, "duplicate": 1, "conflict": 0})

    def test_same_reference_changed_amount_is_a_conflict(self):
        self.import_sheet(self.sheet([mybank_row()]))
        result = self.import_sheet(self.sheet([mybank_row(payment="12001.00")]))
        self.assertEqual(len(result["bank"]), 1)
        self.assertEqual(result["bank"][0]["amount_cents"], 1200000)
        self.assertEqual(result["last_import"]["counts"]["bank"]["conflict"], 1)

    def test_mybank_and_bocom_can_share_a_file_and_ledger(self):
        bocom = self.bank_file([bank_row(reference="SAME")])
        sheets = read_sheets(base64.b64decode(bocom["content"]), bocom["name"], self.config["max_rows"])
        sheets.append(self.sheet([mybank_row(reference="SAME")]))
        result = self.import_files(bank=workbook_upload(sheets))
        self.assertEqual(len(result["bank"]), 2)
        self.assertEqual(len(set(row["id"] for row in result["bank"])), 2)
        self.assertEqual(len(result["company"]["accounts"]), 2)
        self.assertEqual(result["stats"]["debit_cents"], 2400000)

    def test_controls_reject_missing_duplicate_and_changed_rows(self):
        self.import_sheet(self.sheet())
        for change in (lambda rows: rows.pop(), lambda rows: rows.append(copy.deepcopy(rows[-1])), lambda rows: rows[5].__setitem__(5, "12000.01")):
            sheet = self.sheet()
            change(sheet["rows"])
            self.assert_rejected_unchanged(lambda: self.import_sheet(sheet))

    def test_count_mismatch_rejected_even_when_total_is_correct(self):
        sheet = self.sheet()
        sheet["rows"][2][1] = "2笔"
        self.assert_rejected_unchanged(lambda: self.import_sheet(sheet))

    def test_missing_control_is_not_assumed_zero(self):
        for row, column in ((2, 1), (2, 5), (3, 1), (3, 5)):
            with self.subTest(row=row, column=column):
                sheet = self.sheet()
                sheet["rows"][row][column] = None
                self.assert_rejected_unchanged(lambda: self.import_sheet(sheet))

    def test_zero_side_is_valid_with_explicit_zero_controls(self):
        result = self.import_sheet(self.sheet([mybank_row()]))
        self.assertEqual(result["stats"]["credit_cents"], 0)
        self.assertEqual(result["last_import"]["controls"][0]["actual_count"], 0)

    def test_wrong_missing_and_conflicting_company_rejected(self):
        for change in (lambda rows: rows[1].__setitem__(1, "别的公司"), lambda rows: rows[1].__setitem__(1, None), lambda rows: rows.append(["企业名称", "别的公司"])):
            sheet = self.sheet()
            change(sheet["rows"])
            self.assert_rejected_unchanged(lambda: self.import_sheet(sheet))

    def test_account_currency_and_precision_validation(self):
        for account in (None, "企业名称", "8888000000000001", "8888000000000001(USD)", 8888000000000001, "8.888e15(人民币)"):
            with self.subTest(account=account):
                sheet = self.sheet()
                sheet["rows"][1][5] = account
                self.assert_rejected_unchanged(lambda: self.import_sheet(sheet))

    def test_mixed_accounts_on_repeated_page_rejected(self):
        sheet = self.sheet()
        sheet["rows"].append(["企业账号", "8888000000000002(人民币)"])
        self.assert_rejected_unchanged(lambda: self.import_sheet(sheet))

    def test_reference_precision_and_bad_dates_rejected(self):
        for column, value in ((0, 12345678901234567890123456789), (2, "2026-02-29 12:00:00"), (2, None)):
            sheet = self.sheet()
            sheet["rows"][5][column] = value
            self.assert_rejected_unchanged(lambda: self.import_sheet(sheet))

    def test_invalid_amount_and_direction_rejected(self):
        for income, payment in (("1", "1"), (None, "0"), (None, "-1"), (None, "0.001"), (None, "NaN"), (None, None)):
            with self.subTest(income=income, payment=payment):
                sheet = self.sheet()
                sheet["rows"][5][4:6] = [income, payment]
                self.assert_rejected_unchanged(lambda: self.import_sheet(sheet))

    def test_title_and_income_expense_labels_are_required(self):
        for row, column, value in ((0, 0, "未知银行明细"), (4, 4, "借方金额"), (4, 5, "贷方金额")):
            sheet = self.sheet()
            sheet["rows"][row][column] = value
            self.assert_rejected_unchanged(lambda: self.import_sheet(sheet))

    def test_fullwidth_parentheses_and_label_colons_are_accepted(self):
        sheet = self.sheet()
        sheet["rows"][1] = ["企业名称：" + self.config["company_name"], None, None, None, "企业账号：8888000000000001（人民币）"]
        sheet["rows"][4][4:6] = ["借方金额（收）", "贷方金额（支）"]
        self.assertEqual(len(self.import_sheet(sheet)["bank"]), 2)

    def test_repeated_header_and_blank_rows_do_not_drop_transactions(self):
        sheet = self.sheet()
        sheet["rows"].insert(6, copy.deepcopy(sheet["rows"][4]))
        sheet["rows"].insert(7, [])
        sheet["rows"].extend(copy.deepcopy(sheet["rows"][:5]))
        self.assertEqual(len(self.import_sheet(sheet)["bank"]), 2)

    def test_reordered_page_header_rejected_before_reversing_amounts(self):
        sheet = self.sheet()
        header = list(sheet["rows"][4])
        header[4], header[5] = header[5], header[4]
        sheet["rows"].insert(6, header)
        self.assert_rejected_unchanged(lambda: self.import_sheet(sheet))

    def test_column_order_can_differ_for_a_whole_file(self):
        sheet = self.sheet()
        for row in sheet["rows"][4:]:
            row[4], row[5] = row[5], row[4]
        self.assertEqual(self.import_sheet(sheet)["stats"]["debit_cents"], 1200000)

    def test_transaction_note_equal_to_header_label_remains_data(self):
        for note in ("交易时间", "账务流水号"):
            self.assertEqual(len(read_uploads({"bank": workbook_upload([self.sheet([mybank_row(note=note)])])}, self.config)["bank"]), 1)

    def test_thousand_rows_and_physical_row_limit(self):
        rows = [mybank_row(reference=f"LONG{n:025d}", payment=str(n + 1)) for n in range(1000)]
        sheet = self.sheet(rows)
        sheet["rows"].insert(505, copy.deepcopy(sheet["rows"][4]))
        result = self.import_sheet(sheet)
        self.assertEqual(len(result["bank"]), 1000)
        self.assertEqual(result["stats"]["debit_cents"], 50050000)
        self.assertEqual(result["bank"][-1]["reference"], rows[-1][0])
        self.service.config = dict(self.config, max_rows=1005)
        self.assert_rejected_unchanged(lambda: self.import_sheet(sheet))

    def test_unknown_sheet_or_invalid_second_file_cancels_whole_import(self):
        bad_sheets = [self.sheet(), {"name": "未知页", "rows": [["未知内容"]]}]
        self.assert_rejected_unchanged(lambda: self.import_files(bank=workbook_upload(bad_sheets)))
        self.assert_rejected_unchanged(lambda: self.import_files(bank=workbook_upload([self.sheet()]), invoice={"name": "bad.xlsx", "content": base64.b64encode(b"bad").decode("ascii")}))
