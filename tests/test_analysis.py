import unittest
from decimal import Decimal

from tb_autofill import analysis
from tb_autofill.models import LedgerRow


def ledger():
    def row(code, name, opening=None, ending=None, level=1, path=None):
        return LedgerRow(code=code, name=name, path=path or name, level=level, opening=opening, ending=ending,
                         subject_id="S1", year=2026, month=6)

    return [
        row("1001", "库存现金", Decimal("5000"), Decimal("12000")),
        row("1002", "银行存款", Decimal("800000"), Decimal("1230000")),
        row("100201", "基本户", Decimal("800000"), Decimal("1230000"), level=2, path="银行存款/基本户"),
        row("1601", "固定资产", Decimal("500000"), Decimal("450000")),
        row("6602", "管理费用", None, Decimal("120000")),
    ]


class MovementTests(unittest.TestCase):
    def test_change_and_rate(self):
        items = {item.code: item for item in analysis.movements(ledger())}
        self.assertEqual(items["1001"].change, Decimal("7000"))
        self.assertEqual(items["1001"].rate, Decimal("1.4"))
        self.assertEqual(items["1601"].change, Decimal("-50000"))
        self.assertEqual(items["1601"].rate, Decimal("-0.1"))

    def test_missing_opening_has_no_change(self):
        items = {item.code: item for item in analysis.movements(ledger())}
        self.assertIsNone(items["6602"].change)
        self.assertIsNone(items["6602"].rate)

    def test_parent_detection_is_prefix_based(self):
        self.assertEqual(analysis._parents(["1001", "1002", "100201", "1601"]), {"1002"})

    def test_zero_opening_rate_is_none(self):
        rows = [LedgerRow(code="1001", name="库存现金", opening=Decimal(0), ending=Decimal("10"),
                          subject_id="S1", year=2026, month=6)]
        self.assertIsNone(analysis.movements(rows)[0].rate)


class FactsTests(unittest.TestCase):
    def test_parent_excluded_from_ranking(self):
        facts = analysis.build_facts(ledger())
        self.assertEqual([item["code"] for item in facts["movements"]], ["100201", "1601", "1001"])
        self.assertNotIn("1002", [item["code"] for item in facts["movements"]])

    def test_counts_and_period(self):
        facts = analysis.build_facts(ledger())
        self.assertEqual(facts["subject"], {"id": "S1", "year": 2026, "month": 6})
        self.assertEqual(facts["counts"], {"accounts": 5, "leaves": 4})

    def test_mixed_subject_rejected(self):
        rows = ledger() + [LedgerRow(code="1001", name="库存现金", subject_id="S2", year=2026, month=6)]
        with self.assertRaises(ValueError):
            analysis.build_facts(rows)

    def test_summary_is_deterministic(self):
        text = analysis.summarize(analysis.build_facts(ledger()))
        self.assertIn("100201 基本户 +430,000（53.75%）", text)
        self.assertIn("1601 固定资产 -50,000", text)


class SuspiciousNumberTests(unittest.TestCase):
    def setUp(self):
        self.facts = analysis.build_facts(ledger())
        self.amounts = analysis.fact_amounts(self.facts)
        self.codes = analysis.fact_codes(self.facts)

    def test_known_amounts_pass(self):
        text = "银行存款（100201）期末 1,230,000，较期初增加 430,000。"
        self.assertEqual(analysis.suspicious_numbers(text, self.amounts, self.codes), [])

    def test_invented_amount_flagged(self):
        text = "管理费用增加 9999999，主要系人员扩张。"
        self.assertEqual(analysis.suspicious_numbers(text, self.amounts, self.codes), ["9999999"])

    def test_percentage_and_year_ignored(self):
        text = "2026 年增长 40%，涉及 3 个科目。"
        self.assertEqual(analysis.suspicious_numbers(text, self.amounts, self.codes), [])

    def test_account_number_inside_name_not_flagged(self):
        rows = [LedgerRow(code="1002.01", name="农业银行43128", path="银行存款/农业银行43128",
                          opening=Decimal("1000"), ending=Decimal("2000"),
                          subject_id="S1", year=2026, month=6)]
        facts = analysis.build_facts(rows)
        self.assertEqual(analysis.suspicious_numbers(
            "银行存款（1002.01，农业银行43128）增加 1000。",
            analysis.fact_amounts(facts), analysis.fact_codes(facts),
            extra=analysis.fact_tokens(facts)), [])

    def test_decimal_amount_not_treated_as_code(self):
        self.assertEqual(analysis.unknown_codes("余额 1000.50 元，占比 12.5%。", {"1001"}), [])

    def test_tolerance_absorbs_rounding(self):
        text = "期末余额 1,230,000.40。"
        self.assertEqual(analysis.suspicious_numbers(text, self.amounts, self.codes), [])


if __name__ == "__main__":
    unittest.main()
