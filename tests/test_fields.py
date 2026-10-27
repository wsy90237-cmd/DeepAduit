import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from tb_autofill import fields
from tb_autofill.matching import match_fields
from tb_autofill.models import LedgerRow

TEMPLATE = Path(__file__).parent / "fixtures" / "试算平衡表-合成.xlsx"


def ledger():
    def row(code, name, opening=None, ending=None, direction="借", level=1, path=""):
        return LedgerRow(code=code, name=name, path=path or name, direction=direction, level=level,
                         opening=opening, ending=ending, subject_id="S1", year=2026, month=6)

    return [
        row("1001", "库存现金", Decimal("5000"), Decimal("12000")),
        row("1002", "银行存款", Decimal("800000"), Decimal("1230000")),
        row("1122", "应收账款", Decimal("300000"), Decimal("260000")),
        row("1221", "其他应收款", Decimal("20000"), Decimal("15000")),
        row("1601", "固定资产", Decimal("500000"), Decimal("500000")),
        row("2202", "应付账款", Decimal("120000"), Decimal("150000"), direction="贷"),
        row("2203", "预收账款", None, Decimal("80000"), direction="贷"),
        row("2221", "应交税费", Decimal("12000"), Decimal("18000"), direction="贷"),
        row("4001", "实收资本", Decimal("1000000"), Decimal("1000000"), direction="贷"),
        row("4104", "未分配利润", Decimal("500000"), Decimal("723000"), direction="贷"),
    ]


class TemplateScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scan = fields.scan_template(TEMPLATE, ("opening", "ending"), "自动")

    def test_unit_and_field_count(self):
        self.assertEqual(self.scan.unit, "元")
        self.assertEqual(len(self.scan.fields), 24)
        self.assertEqual(self.scan.warnings, [])

    def test_audited_and_adjustment_columns_excluded(self):
        self.assertTrue(all(field.basis == "book" for field in self.scan.fields))
        self.assertTrue(all(field.sheet == "试算平衡表" for field in self.scan.fields))

    def test_formula_cell_detected(self):
        formula = [field for field in self.scan.fields if field.is_formula]
        self.assertEqual([field.id for field in formula], ["试算平衡表!D15"])

    def test_existing_value_kept(self):
        cell = next(field for field in self.scan.fields if field.id == "试算平衡表!D14")
        self.assertEqual(cell.value, 700000)

    def test_period_selection_limits_fields(self):
        only_opening = fields.scan_template(TEMPLATE, ("opening",), "自动")
        self.assertEqual(len(only_opening.fields), 12)
        self.assertTrue(all(field.period == "opening" for field in only_opening.fields))

    def test_unit_override_changes_factor(self):
        wan = fields.scan_template(TEMPLATE, ("ending",), "万元")
        self.assertEqual({field.unit_factor for field in wan.fields}, {Decimal(10000)})

    def test_legacy_xls_rejected(self):
        with tempfile.NamedTemporaryFile(suffix=".xls", delete=False) as handle:
            path = Path(handle.name)
        try:
            with self.assertRaises(ValueError):
                fields.scan_template(path)
        finally:
            path.unlink(missing_ok=True)


class MatchOnFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scan = fields.scan_template(TEMPLATE, ("opening", "ending"), "自动")
        cls.results = match_fields(cls.scan.fields, ledger())
        cls.by_label = {}
        for result in cls.results:
            cls.by_label.setdefault(result.target.label, {})[result.target.period] = result

    def test_formula_is_protected(self):
        self.assertEqual(self.by_label["资产总计"]["ending"].status, "protected")

    def test_aliased_and_plain_names_matched(self):
        for label in ("库存现金", "银行存款", "应收账款", "其他应收款", "固定资产", "实收资本"):
            with self.subTest(label=label):
                self.assertEqual(self.by_label[label]["ending"].status, "matched")
                self.assertIsNotNone(self.by_label[label]["ending"].amount)

    def test_unknown_name_stays_unmatched(self):
        self.assertEqual(self.by_label["存货"]["ending"].status, "unmatched")

    def test_missing_opening_does_not_become_zero(self):
        self.assertEqual(self.by_label["预收款项"]["opening"].status, "missing")
        self.assertIsNone(self.by_label["预收款项"]["opening"].amount)
        self.assertEqual(self.by_label["预收款项"]["ending"].status, "matched")

    def test_existing_value_not_selected_and_difference_reported(self):
        result = self.by_label["未分配利润"]["ending"]
        self.assertEqual(result.status, "matched")
        self.assertFalse(result.selected)
        self.assertEqual(result.difference, Decimal("23000"))

    def test_credit_balance_keeps_natural_sign(self):
        self.assertEqual(self.by_label["应付账款"]["ending"].amount, Decimal("150000"))


if __name__ == "__main__":
    unittest.main()
