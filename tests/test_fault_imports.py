import base64
import io
import zipfile
from fault_support import FaultCase, edit_upload, bank_row, invoice_row
from reconcile.normalize import cents


class ImportFaults(FaultCase):
    def test_foreign_currency_header_is_rejected(self):
        file = edit_upload(self.bank_file(), lambda b: setattr(b.active["F2"], "value", "美元"))
        self.assert_rejected_unchanged(lambda: self.import_files(bank=file))

    def test_mixed_currency_repeated_header_is_rejected(self):
        def edit(b):
            b.active.append(["币种：", "USD"])
        self.assert_rejected_unchanged(lambda: self.import_files(bank=edit_upload(self.bank_file(), edit)))

    def test_blank_bank_account_not_replaced_with_next_label(self):
        self.assert_rejected_unchanged(lambda: self.import_files(bank=edit_upload(self.bank_file(), lambda b: setattr(b.active["B2"], "value", None))))

    def test_numeric_long_bank_account_precision_is_rejected(self):
        self.assert_rejected_unchanged(lambda: self.import_files(bank=edit_upload(self.bank_file(), lambda b: setattr(b.active["B2"], "value", 443000000000000000001))))

    def test_numeric_long_bank_reference_precision_is_rejected(self):
        self.assert_rejected_unchanged(lambda: self.import_files(bank=edit_upload(self.bank_file(), lambda b: setattr(b.active["G4"], "value", 12345678901234567890))))

    def test_later_invoice_page_cannot_mix_another_company(self):
        def edit(b):
            rows = list(b.active.values)
            b.active.append(["另一家公司进项发票清单"])
            b.active.append(rows[1])
            new = list(rows[2])
            new[1] = "FOREIGN002"
            b.active.append(new)
        self.assert_rejected_unchanged(lambda: self.import_files(invoice=edit_upload(self.invoice_file(), edit)))

    def test_explicit_invoice_foreign_currency_is_rejected(self):
        def edit(b):
            b.active["J2"] = "币种"
            b.active["J3"] = "USD"
        self.assert_rejected_unchanged(lambda: self.import_files(invoice=edit_upload(self.invoice_file(), edit)))

    def test_unknown_nonempty_sheet_rejects_entire_batch(self):
        self.import_files(bank=self.bank_file())
        bad = edit_upload(self.invoice_file(), lambda b: b.create_sheet("错误页").append(["无法识别的交易记录"]))
        self.assert_rejected_unchanged(lambda: self.import_files(bank=self.bank_file([bank_row("R2")]), invoice=bad))

    def test_formula_without_cached_amount_is_not_zero(self):
        bad = edit_upload(self.bank_file(), lambda b: setattr(b.active["D4"], "value", "=6000+6000"))
        self.assert_rejected_unchanged(lambda: self.import_files(bank=bad))

    def test_invalid_leap_date_rejects_whole_file(self):
        bad = edit_upload(self.bank_file(), lambda b: setattr(b.active["B4"], "value", "2026-02-29"))
        self.assert_rejected_unchanged(lambda: self.import_files(bank=bad))

    def test_negative_or_both_sides_bank_amount_rejected(self):
        for debit, credit in [(-1, 0), (1, 1), (0, 0)]:
            with self.subTest(debit=debit, credit=credit):
                def edit(b):
                    b.active["D4"], b.active["E4"] = debit, credit
                self.assert_rejected_unchanged(lambda: self.import_files(bank=edit_upload(self.bank_file(), edit)))

    def test_subcent_input_does_not_silently_change_amount(self):
        for value in ("100.005", "0.001", "1.999"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    cents(value)
        self.assertEqual(cents(0.1 + 0.2), 30)

    def test_invalid_amount_values_do_not_commit(self):
        for amount in ("NaN", "Infinity", "abc", "100000000000000"):
            with self.subTest(amount=amount):
                bad = edit_upload(self.invoice_file(), lambda b: setattr(b.active["E3"], "value", amount))
                self.assert_rejected_unchanged(lambda: self.import_files(invoice=bad))

    def test_empty_wrong_extension_base64_and_truncated_archives(self):
        data = self.invoice_file()
        bad_files = [dict(data, name="bill.txt"), dict(data, content=""), dict(data, content="!!!"),
                     dict(data, content=base64.b64encode(b"PK\x03\x04broken").decode("ascii")),
                     dict(data, content=base64.b64encode(bytes.fromhex("d0cf11e0a1b11ae1") + b"broken").decode("ascii"))]
        for n, file in enumerate(bad_files):
            with self.subTest(case=n):
                self.assert_rejected_unchanged(lambda: self.import_files(invoice=file))

    def test_zip_without_workbook_is_validation_error(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            z.writestr("nothing.txt", "不包含工作簿")
        file = {"name": "invalid.xlsx", "content": base64.b64encode(data.getvalue()).decode("ascii")}
        self.assert_rejected_unchanged(lambda: self.import_files(invoice=file))

    def test_duplicate_invoice_in_same_file_counted_once(self):
        result = self.import_files(invoice=self.invoice_file([invoice_row(), invoice_row()]))
        self.assertEqual(len(result["invoices"]), 1)
        self.assertEqual(result["last_import"]["counts"]["invoices"]["duplicate"], 1)

    def test_duplicate_reference_different_amount_is_quarantined(self):
        result = self.import_files(bank=self.bank_file([bank_row(), bank_row(amount=100)]))
        self.assertEqual(len(result["bank"]), 1)
        self.assertEqual(result["bank"][0]["status"], "review")
        self.assertEqual(result["bank"][0]["amount_cents"], 1200000)

    def test_upload_limit_and_physical_row_limit_are_atomic(self):
        self.import_files(bank=self.bank_file())
        self.service.config = dict(self.config, max_upload_mb=0.001)
        self.assert_rejected_unchanged(lambda: self.import_files(invoice=self.invoice_file()))
        self.service.config = dict(self.config, max_rows=3)
        self.assert_rejected_unchanged(lambda: self.import_files(bank=self.bank_file()))

    def test_true_blank_sheet_does_not_hide_valid_data(self):
        good = edit_upload(self.invoice_file(), lambda b: b.create_sheet("空白页"))
        self.assertEqual(len(self.import_files(invoice=good)["invoices"]), 1)
