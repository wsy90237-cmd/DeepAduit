from datetime import date
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from tb_autofill.bank_listing import HEADERS, SHEET, read_listing
from tb_autofill.evidence_draft import confirmed_rows
from tb_autofill.evidence_project import ProjectStore


class BankListingTests(unittest.TestCase):
    cutoff = date(2025, 12, 31)

    def row(self, number="00001", amount="10.02", **changes):
        cells = dict(entity="合成公司", day=self.cutoff, code="1002", currency="CNY",
                     unit="元", holder="合成公司", number=number, amount=amount)
        cells.update(changes)
        return tuple(cells.values())

    def workbook(self, *rows):
        book = Workbook()
        book.active.title = SHEET
        book.active.append(HEADERS)
        for row in rows:
            book.active.append(row)
        buffer = BytesIO()
        book.save(buffer)
        book.close()
        return buffer.getvalue()

    def read(self, content):
        return read_listing(content, entity="合成公司", cutoff=self.cutoff,
                            code="1002", currency="人民币", ledger_balance=Decimal("20.02"))

    def test_exact_total_and_original_source_row(self):
        content = self.workbook(self.row(), (None,) * 8, self.row("00002", 10))
        accounts = self.read(content)
        self.assertEqual([(a.row, a.balance.account_number, a.balance.balance) for a in accounts],
                         [(2, "00001", Decimal("10.02")), (4, "00002", Decimal("10"))])

    def test_rejects_duplicate_scope_mismatch_formula_and_unbalanced_total(self):
        for row in (self.row("00001", 10), self.row("00002", 10, currency="USD"),
                    self.row("00002", 10, entity="其他公司"), self.row("00002", 10, unit="千元"),
                    self.row("00002", 10, code=1002), self.row("00002", 10, day="2025-12-30"),
                    self.row(2, 10), self.row("00002", "=5+5"), self.row("00002", "10.01")):
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.read(self.workbook(self.row(), row))

    def test_project_snapshots_retain_bytes_and_invalidate_old_exports(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            store = ProjectStore(root / "projects", "合成公司", 2025)
            source = root / "first.xlsx"
            original = self.workbook(self.row(), self.row("00002", 10))
            source.write_bytes(original)
            listing_id, digest, name, _ = store.import_bank_listing(source, "1002", self.read)
            self.assertEqual(store.current_bank_listing("1002"), (listing_id, digest, name, original))
            self.assertEqual(store.import_bank_listing(source, "1002", self.read)[0], listing_id)

            pdf = root / "synthetic.pdf"
            pdf.write_bytes(b"%PDF-1.4\nsynthetic")
            document = store.import_pdf(pdf, lambda _: ["余额 10.02"])
            store.mark_reviewed(document["id"])
            row = {"paper": "1-010", "context": "synthetic", "comparison_listing": listing_id}
            suggestion = store.add_suggestion(json.dumps(row), [document["pages"][0]["evidence_id"]])
            store.confirm_suggestion(suggestion)
            self.assertEqual(confirmed_rows(store, "1-010", "synthetic"), [row])

            second = root / "replacement.xlsx"
            second.write_bytes(self.workbook(self.row(amount="11.02"), self.row("00002", 9)))
            replacement_id = store.import_bank_listing(second, "1002", self.read)[0]
            self.assertNotEqual(replacement_id, listing_id)
            self.assertTrue(store.verify_bank_listing(replacement_id))
            self.assertFalse(store.verify_bank_listing(listing_id))
            self.assertEqual(confirmed_rows(store, "1-010", "synthetic"), [])
