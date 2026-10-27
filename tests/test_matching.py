import unittest
from dataclasses import replace
from decimal import Decimal

from tb_autofill.matching import match_fields, normalize, resolve_match, validate_claims
from tb_autofill.models import LedgerRow, TargetField


def row(code="1001", name="库存现金", **kwargs):
    values = dict(code=code, name=name, opening=Decimal("5"), ending=Decimal("12"), subject_id="S1", year=2026, month=6)
    values.update(kwargs)
    return LedgerRow(**values)


def target(label="库存现金", **kwargs):
    values = dict(sheet="期末", cell="D10", label=label, period="ending")
    values.update(kwargs)
    return TargetField(**values)


class MatchingTests(unittest.TestCase):
    def test_normalize_full_width_and_numbering(self):
        self.assertEqual(normalize(" 一、 现金（库存现金） "), "现金(库存现金)")

    def test_alias_and_real_zero(self):
        result = match_fields([target("现金（库存现金）")], [row(ending=Decimal(0))])[0]
        self.assertEqual((result.status, result.amount, result.selected), ("matched", Decimal(0), True))

    def test_formula_is_protected_even_if_flag_missing(self):
        result = match_fields([target(value="=SUM(D11:D14)")], [row()])[0]
        self.assertEqual(result.status, "protected")
        self.assertIsNone(result.amount)

    def test_existing_value_is_not_selected(self):
        result = match_fields([target(value=0)], [row()])[0]
        self.assertFalse(result.selected)
        self.assertFalse(result.overwrite)
        self.assertEqual(result.difference, Decimal(12))

    def test_missing_not_zero(self):
        result = match_fields([target()], [row(ending=None)])[0]
        self.assertEqual(result.status, "missing")
        self.assertIsNone(result.amount)

    def test_audited_cannot_use_book(self):
        result = match_fields([target(basis="audited")], [row()])[0]
        self.assertEqual(result.status, "basis_mismatch")

    def test_unknown_basis_not_assumed(self):
        self.assertEqual(resolve_match(target(basis="unknown"), [row()]).status, "basis_mismatch")

    def test_two_periods_can_claim_same_source(self):
        results = match_fields([target(period="opening", cell="C10"), target()], [row()])
        self.assertEqual([x.amount for x in results], [Decimal(5), Decimal(12)])
        self.assertEqual([x.status for x in results], ["matched", "matched"])

    def test_same_name_under_different_parent_is_ambiguous(self):
        rows = [row("660201", "其他", level=2), row("660301", "其他", level=2)]
        self.assertEqual(match_fields([target("其他")], rows)[0].status, "ambiguous")

    def test_parent_limits_same_name(self):
        rows = [row("6602", "管理费用"), row("660201", "其他", level=2, path="管理费用/其他"), row("6603", "财务费用"), row("660301", "其他", level=2, path="财务费用/其他")]
        result = match_fields([target("其他", parent="管理费用")], rows)[0]
        self.assertEqual(result.source_ids, ["660201"])

    def test_embedded_parent_prefix_limits_leaf(self):
        rows = [row("2221", "应交税费"), row("222101", "应交增值税", level=2, path="应交税费/应交增值税"), row("640301", "应交增值税", level=2)]
        result = match_fields([target("应交税费—应交增值税")], rows)[0]
        self.assertEqual(result.source_ids, ["222101"])

    def test_ambiguous_parent_not_guessed_from_only_matching_leaf(self):
        rows = [row("6602", "管理费用"), row("660201", "房租", level=2, path="管理费用/房租"), row("5301", "管理费用")]
        result = match_fields([target("房租", parent="管理费用")], rows)[0]
        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(result.amount)

    def test_profit_loss_uses_direction_side_not_net(self):
        source = row("6001", "主营业务收入", ending=Decimal(0), debit=Decimal(90), credit=Decimal(90), direction="贷", is_profit_loss=True)
        result = match_fields([target("主营业务收入", measure="profit_loss")], [source])[0]
        self.assertEqual(result.amount, Decimal(90))
        fallback = match_fields([target("主营业务收入")], [source])[0]
        self.assertEqual(fallback.amount, Decimal(90))

    def test_opening_does_not_use_current_occurrence(self):
        source = row("6001", "主营业务收入", credit=Decimal(90), direction="贷", is_profit_loss=True)
        result = resolve_match(target("主营业务收入", period="opening", measure="profit_loss"), [source])
        self.assertEqual(result.status, "missing")

    def test_cost_inventory_keeps_balance(self):
        source = row("5001", "生产成本", ending=Decimal(10), debit=Decimal(100), credit=Decimal(90))
        self.assertEqual(resolve_match(target("生产成本"), [source]).amount, Decimal(10))

    def test_negative_natural_balance_splits_sides(self):
        source = row("2202", "应付账款", direction="贷", ending=Decimal(-6))
        debit = target("应付账款", balance_side="借", cell="D10")
        credit = target("应付账款", balance_side="贷", cell="E10")
        results = match_fields([debit, credit], [source])
        self.assertEqual([x.amount for x in results], [Decimal(6), Decimal(0)])
        self.assertEqual([x.status for x in results], ["matched", "matched"])

    def test_credit_balance_not_unconditionally_negated(self):
        self.assertEqual(resolve_match(target("应付账款"), [row("2202", "应付账款", direction="贷")]).amount, Decimal(12))

    def test_same_source_multiple_targets_all_flagged(self):
        results = match_fields([target(cell="D10"), target(cell="D20")], [row()])
        self.assertEqual([x.status for x in results], ["duplicate", "duplicate"])
        self.assertTrue(all(not x.selected for x in results))

    def test_parent_child_targets_do_not_double_count(self):
        rows = [row("1002", "银行存款"), row("100201", "基本户", level=2, path="银行存款/基本户")]
        results = match_fields([target("银行存款"), target("基本户", parent="银行存款", cell="D11")], rows)
        self.assertEqual([x.status for x in results], ["duplicate", "duplicate"])

    def test_manual_sources_cannot_combine_parent_and_child(self):
        result = resolve_match(target("银行存款"), [row("1002", "银行存款"), row("100201", "基本户", level=2)])
        self.assertEqual(result.status, "duplicate")
        self.assertIsNone(result.amount)

    def test_manual_can_sum_disjoint_sources(self):
        result = resolve_match(target("合计"), [row("1001", "库存现金"), row("1002", "银行存款")])
        self.assertEqual(result.amount, Decimal(24))

    def test_multiple_subjects_or_periods_rejected(self):
        for second in [row("1002", "银行存款", subject_id="S2"), row("1002", "银行存款", year=2025), row("1002", "银行存款", month=5)]:
            with self.subTest(second=second):
                with self.assertRaises(ValueError):
                    match_fields([target()], [row(), second])

    def test_duplicate_code_rejected(self):
        with self.assertRaises(ValueError):
            match_fields([target()], [row(), row(name="现金")])

    def test_important_net_qualifier_not_removed(self):
        self.assertEqual(match_fields([target("固定资产（净额）")], [row("1601", "固定资产")])[0].status, "unmatched")

    def test_parenthesized_currency_or_component_not_erased(self):
        for name in ["银行存款（美元）", "银行存款（人民币）", "银行存款（总部）"]:
            with self.subTest(name=name):
                self.assertEqual(match_fields([target(name)], [row("1002", "银行存款")])[0].status, "unmatched")

    def test_explicit_note_can_be_removed(self):
        result = match_fields([target("库存现金（附注：1）")], [row()])[0]
        self.assertEqual(result.status, "matched")

    def test_unit_factor_is_applied_without_rounding(self):
        result = resolve_match(target(unit_factor=Decimal(10000)), [row(ending=Decimal("12345.67"))])
        self.assertEqual(result.amount, Decimal("1.234567"))
        for factor in [Decimal(0), Decimal(-1), Decimal(100), Decimal("NaN")]:
            with self.subTest(factor=factor):
                self.assertEqual(resolve_match(target(unit_factor=factor), [row()]).status, "invalid")

    def test_unmatched_not_allocated_to_other(self):
        result = match_fields([target("未知费用")], [row("6602", "管理费用")])[0]
        self.assertEqual(result.status, "unmatched")
        self.assertIsNone(result.amount)

    def test_invalid_direction_and_nonfinite_money(self):
        result = resolve_match(target(measure="profit_loss"), [row(direction="未知", debit=Decimal(1))])
        self.assertEqual(result.status, "invalid")
        self.assertEqual(resolve_match(target(), [row(ending=Decimal("NaN"))]).status, "invalid")

    def test_manual_ai_share_money_guards_and_claim_validation(self):
        sources = [row()]
        a = resolve_match(target(), sources, "ai")
        b = resolve_match(target(cell="D11"), sources, "manual")
        self.assertEqual([x.status for x in validate_claims([a, b])], ["duplicate", "duplicate"])
        b.sources.clear()
        b.amount = None
        b.status = "unmatched"
        self.assertEqual(validate_claims([a, b])[0].status, "matched")


if __name__ == "__main__":
    unittest.main()
