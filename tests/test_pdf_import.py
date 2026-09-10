import base64
import copy
import io
from pypdf import PdfWriter
from fault_support import FaultCase, invoice_row
from pdf_fixtures import payment, statement, pdf_upload, pdf_bytes
from mybank_fixtures import workbook_upload
from reconcile.company_registry import CompanyRegistry
from reconcile.import_reader import read_uploads


class PdfImportTests(FaultCase):
    def pages(self, transactions=None, **options):
        return statement(self.config["company_name"], transactions, **options)

    def import_pdf(self, pages=None, **options):
        return self.import_files(bank=pdf_upload(pages if pages is not None else self.pages(), **options))

    def test_income_expense_controls_and_balance(self):
        result = self.import_pdf()
        payment_row, receipt = result["bank"]
        self.assertEqual((payment_row["amount_cents"], payment_row["direction"]), (1200000, "支出"))
        self.assertEqual(receipt["status"], "excluded")
        self.assertEqual(receipt["credit_cents"], 2500000)
        self.assertEqual(payment_row["balance_cents"], 298800000)
        self.assertEqual((payment_row["pdf_page"], payment_row["row"]), (1, 3))
        self.assertEqual(len(result["last_import"]["controls"]), 2)
        self.assertTrue(all(c["passed"] and c["expected_count"] == 1 for c in result["last_import"]["controls"]))

    def test_page_rotations_keep_same_fields(self):
        expected = None
        for rotation in (0, 90, 180, 270):
            rows = read_uploads({"bank": pdf_upload(self.pages(), rotation=rotation)}, self.config)["bank"]
            fields = [(r["party"], r["date"], r["reference"], r["amount_cents"]) for r in rows]
            expected = expected or fields
            self.assertEqual(fields, expected)

    def test_wrapped_names_notes_and_long_references(self):
        pages = self.pages()
        pages[0]["rows"][1][5:8] = ["测试供应\n商有限公司", "网银证书\n服务年费", "ABC123456789012\n345678901234567"]
        pages[0]["rows"][2][5] = "Example Global\nPay Company\nLimited"
        result = self.import_pdf(pages)
        self.assertEqual(result["bank"][0]["party"], "测试供应商有限公司")
        self.assertEqual(result["bank"][0]["reference"], "ABC123456789012345678901234567")
        self.assertEqual(result["bank"][0]["status"], "excluded")
        self.assertEqual(result["bank"][1]["party"], "Example Global Pay Company Limited")

    def test_multi_page_carry_balances_and_all_rows(self):
        pages = self.pages([payment(f"ROW{i:04d}", i + 100) for i in range(25)])
        result = self.import_pdf(pages)
        self.assertEqual(len(result["bank"]), 25)
        self.assertEqual(result["bank"][-1]["reference"], "ROW0024")
        self.assertEqual(result["bank"][-1]["pdf_page"], 4)

    def test_missing_repeated_or_reordered_pages_rejected(self):
        original = self.pages([payment(f"R{i}", 1000) for i in range(17)])
        for pages in (original[1:], original[:-1], [original[0], original[0], *original[1:]], [original[1], original[0], original[2]]):
            self.assert_rejected_unchanged(lambda: self.import_pdf(pages))

    def test_missing_duplicate_and_gapped_transactions_rejected(self):
        for change in (lambda rows: rows.pop(), lambda rows: rows.append(copy.deepcopy(rows[-1])), lambda rows: rows[1].__setitem__(0, "3")):
            pages = self.pages()
            change(pages[0]["rows"])
            self.assert_rejected_unchanged(lambda: self.import_pdf(pages))

    def test_amount_balance_or_opening_tampering_rejected(self):
        for row, column, amount in ((1, 2, "12000.01"), (1, 4, "2988000.01"), (0, 4, "3000000.01")):
            pages = self.pages()
            pages[0]["rows"][row][column] = amount
            self.assert_rejected_unchanged(lambda: self.import_pdf(pages))

    def test_missing_opening_balance_rejected(self):
        pages = self.pages()
        pages[0]["rows"].pop(0)
        self.assert_rejected_unchanged(lambda: self.import_pdf(pages))

    def test_footer_amount_count_missing_or_inconsistent_rejected(self):
        for label in self.config["pdf_import"]["bocom"]["control_labels"]:
            for changed in (None, "999"):
                pages = self.pages()
                if changed is None:
                    pages[0]["controls"].pop(label)
                else:
                    pages[0]["controls"][label] = changed
                self.assert_rejected_unchanged(lambda: self.import_pdf(pages))

    def test_footer_conflict_on_earlier_page_rejected(self):
        pages = self.pages([payment(f"R{i}", 1000) for i in range(10)])
        pages[0]["controls"] = {"本月累计借方发生数": "999"}
        self.assert_rejected_unchanged(lambda: self.import_pdf(pages))

    def test_identity_missing_wrong_company_or_currency_rejected(self):
        for key, value in (("户名", "另一个公司"), ("户名", None), ("账号", None), ("账号", "4.43e20"), ("币种", "USD"), ("年份", "invalid"), ("月份", "13"), ("页码", "第2页")):
            pages = self.pages()
            if value is None:
                pages[0]["identity"].pop(key)
            else:
                pages[0]["identity"][key] = value
            self.assert_rejected_unchanged(lambda: self.import_pdf(pages))

    def test_different_company_account_or_period_across_pages_rejected(self):
        for key, value in (("户名", "其他公司"), ("账号", "443000000000000000002"), ("月份", "09"), ("年份", "2025"), ("页码", "本月第2份-第2页")):
            pages = self.pages([payment("FIRST"), payment("SECOND")], per_page=1)
            pages[1]["identity"][key] = value
            self.assert_rejected_unchanged(lambda: self.import_pdf(pages))

    def test_invalid_date_or_direction_rejected(self):
        for column, value in ((1, "20260832"), (1, "20260703"), (1, ""), (2, "0.001"), (2, "NaN"), (2, "-1.00"), (3, "1.00")):
            pages = self.pages()
            pages[0]["rows"][1][column] = value
            self.assert_rejected_unchanged(lambda: self.import_pdf(pages))

    def test_missing_header_duplicate_columns_unknown_bank_or_grid_rejected(self):
        for change in (lambda p: p["headers"].__setitem__(2, "未知金额"), lambda p: p["headers"].__setitem__(6, "余额"), lambda p: p.__setitem__("title", "其他银行对账单")):
            pages = self.pages()
            change(pages[0])
            self.assert_rejected_unchanged(lambda: self.import_pdf(pages))
        self.assert_rejected_unchanged(lambda: self.import_pdf(draw_grid=False))

    def test_file_level_column_reordering_is_supported(self):
        pages = self.pages()
        for row in [pages[0]["headers"], *pages[0]["rows"]]:
            row[2], row[3] = row[3], row[2]
        self.assertEqual(self.import_pdf(pages)["stats"]["debit_cents"], 1200000)

    def test_encrypted_empty_damaged_or_disguised_files_rejected(self):
        self.assert_rejected_unchanged(lambda: self.import_pdf(encrypted=True))
        empty = PdfWriter()
        empty.add_blank_page(width=842, height=595)
        buffer = io.BytesIO()
        empty.write(buffer)
        for content, name in ((buffer.getvalue(), "scan.pdf"), (b"%PDF-1.7\nbroken", "bad.pdf"), (b"not a PDF", "bad.pdf"), (pdf_bytes(self.pages())[:120], "cut.pdf"), (pdf_bytes(self.pages()), "bad.xlsx")):
            item = {"name": name, "content": base64.b64encode(content).decode("ascii")}
            self.assert_rejected_unchanged(lambda: self.import_files(bank=item))

    def test_file_limits_cancel_whole_import(self):
        for field, value in (("max_pages", 1), ("max_stream_bytes_per_page", 1), ("max_stream_bytes_total", 1), ("max_text_parts_per_page", 1), ("max_path_segments_per_page", 1)):
            config = copy.deepcopy(self.config)
            config["pdf_import"][field] = value
            with self.assertRaises(ValueError):
                read_uploads({"bank": pdf_upload(self.pages(per_page=1))}, config)
        self.service.config = dict(self.config, max_rows=1)
        self.assert_rejected_unchanged(lambda: self.import_pdf())

    def test_pdf_for_invoice_or_bad_combined_file_rolls_back(self):
        self.assert_rejected_unchanged(lambda: self.import_files(invoice=pdf_upload(self.pages())))
        self.assert_rejected_unchanged(lambda: self.import_files(bank=pdf_upload(self.pages()), invoice={"name": "bad.xlsx", "content": base64.b64encode(b"bad").decode("ascii")}))

    def test_duplicate_pdf_and_excel_do_not_duplicate_amounts(self):
        pages = self.pages()
        result = self.import_pdf(pages)
        ids = [row["id"] for row in result["bank"]]
        for bank in (pdf_upload(pages), workbook_upload([{"name": "明细", "rows": [["交通银行明细对账单"], ["账号：", "443000000000000000001", "户名：", self.config["company_name"], "币种：", "人民币"], pages[0]["headers"], *pages[0]["rows"][1:]]}])):
            result = self.import_files(bank=bank)
            self.assertEqual([row["id"] for row in result["bank"]], ids)
            self.assertEqual(result["last_import"]["counts"]["bank"], {"new": 0, "duplicate": 2, "conflict": 0})

    def test_same_reference_changed_content_is_conflict_not_overwrite(self):
        self.import_pdf()
        pages = self.pages([payment(amount=1200001), payment("PDF0002", 2500000, "测试客户", income=True)])
        result = self.import_pdf(pages)
        self.assertEqual(result["bank"][0]["amount_cents"], 1200000)
        self.assertEqual(result["last_import"]["counts"]["bank"]["conflict"], 1)

    def test_missing_reference_uses_existing_review_flow(self):
        pages = self.pages()
        pages[0]["rows"][1][7] = ""
        self.import_pdf(pages)
        result = self.import_pdf(pages)
        self.assertEqual(result["last_import"]["counts"]["bank"]["conflict"], 1)

    def test_cross_month_partial_allocations_survive_restart_and_reimport(self):
        result = self.import_pdf(self.pages([payment(amount=5000000)]))
        bank_id = result["bank"][0]["id"]
        result = self.import_files(invoice=self.invoice_file([invoice_row(amount=2000000)]))
        result = self.allocate([{"bank_id": bank_id, "invoice_id": result["invoices"][0]["id"], "amount_cents": 2000000}])
        self.assertEqual(result["bank"][0]["status"], "partial")
        self.service = type(self.service)(self.directory, self.config)
        result = self.import_pdf(self.pages([payment(amount=5000000)]))
        self.assertEqual(result["bank"][0]["allocated_cents"], 2000000)
        result = self.import_files(invoice=self.invoice_file([invoice_row(number="LATER", amount=3000000, date="2026-10-03")]))
        self.assertEqual(result["bank"][0]["status"], "matched")

    def test_all_company_profiles_support_pdf(self):
        registry = CompanyRegistry(self.directory, self.config)
        for profile in self.config["companies"]:
            key = profile["key"]
            company = self.config["company_name"] if key == "moderate" else profile["label"] + "格式测试有限公司"
            service = registry.get(key) if key == "moderate" else registry.activate({"company_key": key, "legal_name": company})
            result = service.import_files({"revision": service.ledger()["revision"], "bank": pdf_upload(statement(company))})
            self.assertEqual(result["stats"]["bank_count"], 2)

    def test_many_pages_thousand_records_not_truncated(self):
        pages = self.pages([payment(f"PDFLONG{i:025d}", 100) for i in range(1000)])
        result = self.import_pdf(pages)
        self.assertEqual(result["stats"]["bank_count"], 1000)
        self.assertEqual(result["stats"]["debit_cents"], 100000)
        self.assertEqual(result["bank"][-1]["reference"], "PDFLONG0000000000000000000000999")
