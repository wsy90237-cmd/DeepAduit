from dataclasses import replace
from datetime import date
from decimal import Decimal
import unittest

from tb_autofill.bank_evidence import BankBalance, complete_amount_at, reconcile_bank_balance
from tb_autofill.bank_evidence_ui import _amount_in_excerpt, _bank_rows, _bank_summary, _evidence_date, _found_in_excerpt, _normalize_amount_text
from tb_autofill.models import LedgerRow, Subject


class BankEvidenceTests(unittest.TestCase):
    book = BankBalance("合成公司", "00010002", "CNY", date(2025, 12, 31), Decimal("1234.50"))

    def compare(self, evidence, kind="confirmation"):
        return reconcile_bank_balance(self.book, evidence, evidence_kind=kind)

    def test_matching_confirmation_accepts_unambiguous_notation(self):
        evidence = BankBalance(" 合成公司 ", "0001 0002", "人民币", "2025-12-31", "1,234.50")
        result = self.compare(evidence)
        self.assertEqual((result.status, result.difference, result.issues), ("matched", Decimal("0.00"), ()))
        self.assertIn("收函控制", result.recommendation)
        self.assertIn("对账单来源", self.compare(evidence, "statement").recommendation)

    def test_amount_difference_is_signed_and_exact(self):
        result = self.compare(replace(self.book, balance="1,234.499"), "statement")
        self.assertEqual((result.status, result.difference), ("difference", Decimal("-0.001")))
        self.assertIn("余额调节表", result.recommendation)

    def test_different_account_currency_or_date_suppresses_difference(self):
        evidence = BankBalance("其他公司", "10002", "USD", "2025-12-30", "1")
        result = self.compare(evidence)
        self.assertEqual((result.status, result.difference), ("mismatch", None))
        self.assertEqual(result.issues, ("户名不一致", "完整账号不一致", "币种不一致", "截止日不一致"))

    def test_missing_and_invalid_fields_remain_pending(self):
        evidence = BankBalance(account_number="****0002", cutoff_date="2025-02-29", balance="12,34.50")
        result = self.compare(evidence)
        self.assertEqual((result.status, result.difference), ("pending", None))
        self.assertIn("银行证据缺少户名", result.issues)
        self.assertIn("银行证据完整账号格式不符合要求", result.issues)
        self.assertIn("银行证据余额格式不符合要求", result.issues)
        for amount in ("¥100", "1e3", "100万元", float(1), True, Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(amount=amount):
                self.assertEqual(self.compare(replace(self.book, balance=amount)).status, "pending")
        self.assertEqual(self.compare(replace(self.book, balance=0)).difference, Decimal("-1234.50"))


class BankEvidenceSelectionTests(unittest.TestCase):
    def test_scope_excludes_bank_totals_other_periods_and_missing_balances(self):
        scope = dict(subject_id="co", ledger_id="book", year=2025, month=12)
        root = LedgerRow("1002", "银行存款", "银行存款", ending=Decimal("100"), **scope)
        eligible = replace(root, code="100201", name="合成账户", path="银行存款/合成账户", level=2)
        rows = [root, eligible, replace(eligible, code="100202", month=11),
                replace(eligible, code="100203", ending=None),
                replace(eligible, code="100204"), replace(eligible, code="10020401", level=3, ending=None)]
        subject = Subject("co", "合成公司", 2025, 12, "", ledger_id="book")
        self.assertEqual(_bank_rows(rows, subject), (eligible,))
        self.assertEqual(_bank_rows([root], subject), ())
        self.assertEqual(_bank_summary([root], subject), root)
        self.assertIsNone(_bank_summary(rows, subject))

    def test_excerpt_matching_rejects_partial_amounts_and_spaced_account_numbers(self):
        excerpt = "户名 合 成 公 司\n账号 0001 0002\n余额 1,234.50"
        self.assertTrue(_found_in_excerpt("合成公司", excerpt, ""))
        self.assertTrue(_found_in_excerpt("00010002", excerpt, "0-9*＊"))
        self.assertFalse(_found_in_excerpt("0002", excerpt, "0-9*＊"))
        self.assertFalse(_found_in_excerpt("234.50", excerpt, "0-9.,+-"))
        self.assertTrue(_found_in_excerpt("1,234.50", excerpt, "0-9.,+-"))
        self.assertEqual(_evidence_date("2025年12月31日"), date(2025, 12, 31))

    def test_amount_text_only_joins_at_explicit_decimal_or_group_separator(self):
        self.assertEqual(_normalize_amount_text("４５６ ． ７８"), "456.78")
        self.assertEqual(_normalize_amount_text("４５６ ７８"), "456 78")
        self.assertTrue(_amount_in_excerpt("456.78", "余额 ４５６ ． ７８"))
        self.assertFalse(_amount_in_excerpt("45678", "余额 ４５６ ７８"))
        self.assertFalse(_amount_in_excerpt("78", "余额 ４５６ ． ７８"))

    def test_complete_amount_rejects_omitted_sign_parentheses_and_space_thousands(self):
        for excerpt, nested in (
            ("余额 − 1,234.50", "1,234.50"),
            ("余额（1,234.50）", "1,234.50"),
            ("余额 1 234.56", "234.56"),
            ("余额 456 ． 78", "456"),
            ("余额 456 ． 78", "78"),
            ("余额 1 234", "1"),
        ):
            with self.subTest(excerpt=excerpt, nested=nested):
                begin = excerpt.index(nested)
                self.assertFalse(complete_amount_at(excerpt, begin, begin + len(nested)))
                self.assertFalse(_amount_in_excerpt(nested, excerpt))
        excerpt = "余额 456 ． 78 元"
        self.assertTrue(complete_amount_at(excerpt, 3, 11))
        self.assertEqual(_normalize_amount_text("（１，２３４．５０）"), "-1,234.50")
        self.assertEqual(_normalize_amount_text("− 1234.50"), "-1234.50")
