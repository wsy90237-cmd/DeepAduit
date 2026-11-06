import unittest
from dataclasses import replace
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
    def test_question_keeps_unchanged_cost_zero_details_and_depreciation_together(self):
        cost = replace(ledger()[3], opening=Decimal(500000), ending=Decimal(500000))
        zero = replace(cost, code="160101", name="房屋建筑物", path="固定资产/房屋建筑物", level=2,
                       opening=Decimal(0), ending=Decimal(0))
        depreciation = replace(cost, code="1602", name="累计折旧", path="累计折旧", direction="贷",
                               opening=Decimal(0), ending=Decimal("52248.72"))
        unrelated = [replace(cost, code=str(9000 + i), name=f"合成科目{i}", path=f"合成科目{i}",
                             opening=Decimal(0), ending=Decimal(1000000)) for i in range(350)]
        facts = analysis.build_facts([cost, zero, depreciation, *unrelated], question="固定资产今年有什么异常吗")
        self.assertEqual({item["code"] for item in facts["accounts"]}, {"1601", "160101", "1602"})
        self.assertFalse(facts["account_scope"]["truncated"])
        self.assertEqual(facts["report_totals"][0]["ending"], "447751.28")
        self.assertIn(Decimal(500000), analysis.fact_amounts(facts))
        self.assertIn(Decimal("447751.28"), analysis.fact_amounts(facts))
        followup = analysis.build_facts([cost, zero, depreciation, *unrelated], question="那需要查哪些资料",
                    history=[{"role": "user", "content": "固定资产今年有什么异常吗"}])
        self.assertEqual(followup["accounts"], facts["accounts"])

    def test_profit_loss_facts_use_occurrences_when_closing_balances_are_zero(self):
        source = replace(ledger()[-1], opening=Decimal(0), ending=Decimal(0), is_profit_loss=True,
                         debit=Decimal(999999), credit=Decimal(999999), period_amount=Decimal(12000),
                         previous_period_amount=Decimal(10000), monthly_amounts=(Decimal(2000),) * 6 + (None,) * 6)
        facts = analysis.build_facts([source], question="管理费用今年为什么增加")
        self.assertEqual(facts["movements"][0]["change"], "2000")
        self.assertEqual(facts["accounts"][0]["current_period"], "12000")
        self.assertFalse({"opening", "ending", "debit", "credit"} & facts["accounts"][0].keys())
        self.assertFalse({"opening", "ending", "debit", "credit"} & facts["movements"][0].keys())
        self.assertEqual(facts["totals"], {})

    def test_question_scope_marks_truncation_and_new_explicit_question_replaces_history(self):
        root = ledger()[3]
        details = [replace(root, code=f"1601{i:03}", name=f"合成设备{i}", path=f"固定资产/合成设备{i}", level=2)
                   for i in range(305)]
        facts = analysis.build_facts([root, *details], question="固定资产", account_limit=300)
        self.assertTrue(facts["account_scope"]["truncated"])
        self.assertEqual(facts["account_scope"]["available"], 306)
        facts = analysis.build_facts(ledger(), question="银行存款要怎么审计",
                    history=[{"role": "user", "content": "固定资产有什么异常"}])
        self.assertEqual({item["code"] for item in facts["accounts"]}, {"1001", "1002", "100201"})

    def test_custom_account_question_keeps_zero_children_even_when_it_is_the_only_family(self):
        root = replace(ledger()[0], code="9001", name="合成专用项目", path="合成专用项目")
        child = replace(root, code="900101", name="合成零额明细", path="合成专用项目/合成零额明细", level=2,
                        opening=Decimal(0), ending=Decimal(0))
        facts = analysis.build_facts([root, child], question="合成专用项目有哪些明细")
        self.assertTrue(facts["account_scope"]["focused"])
        self.assertEqual(len(facts["accounts"]), 2)

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

    def test_strict_validation_recognizes_known_dotted_code_but_not_a_fake_amount(self):
        options = dict(strict=True, tolerance=Decimal("0.005"))
        self.assertEqual(analysis.suspicious_numbers("职工薪酬（6602.07）待核实。", {Decimal("100")}, {"660207"}, **options), [])
        self.assertEqual(analysis.suspicious_numbers("发生额 6602.07元。", {Decimal("100")}, {"660207"}, **options), ["6602.07元"])

    def test_strict_validation_checks_small_amounts_rates_and_display_rounding(self):
        options = dict(strict=True, tolerance=Decimal("0.005"), rates={Decimal("25.00")})
        self.assertEqual(analysis.suspicious_numbers("金额 100.00元，变动率 25.00%。", {Decimal("100.004")}, (), **options), [])
        self.assertEqual(analysis.suspicious_numbers("金额 99.00元，变动率 26.00%。", {Decimal("100")}, (), **options), ["99.00元", "26.00%"])
        self.assertEqual(analysis.suspicious_numbers("金额 100.40元。", {Decimal("100")}, (), **options), ["100.40元"])
        self.assertEqual(analysis.suspicious_numbers("余额为 99.00。", {Decimal("100")}, (), **options), ["99.00"])
        self.assertEqual(analysis.suspicious_numbers("金额 2025元。", {Decimal("100")}, (), **options), ["2025元"])

    def test_fact_difference_is_available_to_the_validator(self):
        self.assertIn(Decimal("74"), analysis.fact_amounts({"book_check": {"absolute_difference_sum": "74"}}, include_counts=False))

    def test_scaled_amounts_counts_and_account_names_are_checked(self):
        options = dict(strict=True, tolerance=Decimal("0.005"), counts={1000},
                       code_names={"600102": {"资产出租业务"}})
        amounts = {Decimal("333333.33")}
        codes = {"600102"}
        self.assertEqual(analysis.suspicious_numbers("金额33.33万元，共1000笔。6001.02资产出租业务。",
                                                     amounts, codes, **options), [])
        self.assertEqual(analysis.suspicious_numbers("金额33.34万元，共1001笔。金额6001.02元。",
                                                     amounts, codes, **options),
                         ["33.34万元", "1001笔", "6001.02元"])


if __name__ == "__main__":
    unittest.main()
