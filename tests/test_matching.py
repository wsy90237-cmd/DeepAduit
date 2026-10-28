import unittest
from dataclasses import replace
from decimal import Decimal
from time import perf_counter

from tb_autofill.matching import apply_ai_suggestions, match_fields, normalize, resolve_match, validate_claims
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
    def test_vat_summary_does_not_use_zero_component_as_complete_tax_balance(self):
        field=target('应交税费-增值税',statement='balance_sheet',presentation_direction='贷')
        zero=row('222101','增值税',path='应交税费/增值税',direction='贷',level=2,ending=Decimal(0))
        unpaid=row('222102','未交增值税',path='应交税费/未交增值税',direction='贷',level=2,ending=Decimal(-30))
        result=match_fields([field],[row('2221','应交税费'),zero,unpaid])[0]
        self.assertEqual(result.status,'ambiguous')
        self.assertIsNone(result.amount)
        self.assertEqual(resolve_match(field,[zero]).status,'missing')
        self.assertEqual(resolve_match(field,[zero,unpaid]).status,'missing')
    def test_tax_details_use_tax_root_and_specific_tax_name(self):
        parent=row('2221','应交税金',direction='贷')
        details=[('城建税','应交城市维护建设税'),('土增税','土地增值税'),
                 ('个人所得税','应交个人所得税'),('印花税','应交印花税')]
        sources=[parent]+[row(f'22210{i}',source,path=f'应交税金/{source}',level=2,
                       direction='贷',ending=Decimal(i)) for i,(_,source) in enumerate(details,1)]
        for i,(label,_) in enumerate(details,1):
            field=target('应交税费-'+label,statement='balance_sheet',presentation_direction='贷')
            result=match_fields([field],sources)[0]
            self.assertEqual((result.status,result.amount,result.source_ids),('matched',Decimal(i),[f'22210{i}']))

    def test_tax_detail_cannot_use_unrelated_root_or_wrong_tax(self):
        field=target('应交税费-印花税',statement='balance_sheet',presentation_direction='贷')
        wrong=row('222101','应交个人所得税',path='应交税费/应交个人所得税',level=2,direction='贷')
        expense=row('640101','应交印花税',path='管理费用/应交印花税',level=2,direction='借')
        self.assertEqual(resolve_match(field,[wrong]).status,'missing')
        self.assertEqual(resolve_match(field,[expense]).status,'missing')
        self.assertEqual(match_fields([field],[row('2221','应交税费'),wrong,expense])[0].status,'unmatched')

    def test_multiple_tax_roots_or_disjoint_details_are_ambiguous(self):
        field=target('应交税费-印花税',statement='balance_sheet')
        parent=row('2221','应交税费')
        a=row('222101','应交印花税',path='应交税费/应交印花税',level=2)
        b=row('222102','印花税',path='应交税费/印花税',level=2)
        self.assertEqual(match_fields([field],[parent,a,b])[0].status,'ambiguous')
        other=row('2222','应交税金')
        self.assertEqual(match_fields([field],[parent,other,a])[0].status,'ambiguous')

    def test_other_receivable_allowance_standalone_name_uses_its_own_balance(self):
        field=target("其他应收款坏账准备",parent="其他应收款",statement="balance_sheet",presentation_direction="贷")
        allowance=row("120","坏账准备-其他应收款",direction="贷",ending=Decimal("23"),level=1)
        unrelated=row("114","坏账准备",direction="贷",ending=Decimal("7"),level=1)
        result=match_fields([field],[unrelated,allowance])[0]
        self.assertEqual((result.status,result.amount,result.source_ids),("matched",Decimal("23"),["120"]))

    def test_allowance_loss_name_is_valid_only_under_asset_allowance(self):
        field=target("应收账款坏账准备",parent="应收账款",statement="balance_sheet",presentation_direction="贷")
        root=row("1231","坏账准备",direction="贷",ending=Decimal("10"),level=1)
        child=row("123101","应收账款坏账损失",path="坏账准备/应收账款坏账损失",direction="贷",ending=Decimal("10"),level=2)
        expense=row("670101","应收账款坏账损失",path="信用减值损失/应收账款坏账损失",direction="借",ending=Decimal("0"),level=2,is_profit_loss=True)
        result=match_fields([field],[root,child,expense])[0]
        self.assertEqual((result.status,result.amount,result.source_ids),("matched",Decimal("10"),["123101"]))
        self.assertEqual(resolve_match(field,[expense]).status,"missing")

    def test_allowance_never_assigns_an_unspecified_total_or_principal(self):
        field=target("应收账款坏账准备",parent="应收账款",statement="balance_sheet",presentation_direction="贷")
        root=row("1231","坏账准备",direction="贷",ending=Decimal("40"))
        principal=row("1122","应收账款",direction="借",ending=Decimal("500"))
        self.assertEqual(match_fields([field],[root,principal])[0].status,"unmatched")
        for source in (root,principal):
            self.assertEqual(resolve_match(field,[source]).status,"missing")

    def test_allowance_two_disjoint_components_are_not_guessed(self):
        field=target("应收账款坏账准备",statement="balance_sheet")
        a=row("123101","应收账款坏账准备",direction="贷",level=2)
        b=row("123201","应收账款坏账准备",direction="贷",level=2)
        result=match_fields([field],[a,b])[0]
        self.assertEqual(result.status,"ambiguous")
        self.assertIsNone(result.amount)

    def test_allowance_component_root_wins_over_same_named_child(self):
        field=target("应收账款坏账准备",statement="balance_sheet",presentation_direction="贷")
        root=row("123101","应收账款坏账准备",direction="贷",ending=Decimal("40"),level=2)
        child=row("12310101","应收账款坏账准备",direction="贷",ending=Decimal("10"),level=3)
        result=match_fields([field],[child,root])[0]
        self.assertEqual((result.status,result.amount,result.source_ids),("matched",Decimal("40"),["123101"]))

    def test_allowance_categories_are_separate_and_ai_cannot_replace_with_principal(self):
        receivable=target("应收账款坏账准备",cell="D50",statement="balance_sheet",presentation_direction="贷")
        other=target("其他应收款坏账准备",cell="D70",statement="balance_sheet",presentation_direction="贷")
        root=row("1231","坏账准备",direction="贷",ending=Decimal("40"),level=1)
        one=row("123101","应收账款坏账损失",path="坏账准备/应收账款坏账损失",direction="贷",ending=Decimal("10"),level=2)
        two=row("123102","其他应收款坏账损失",path="坏账准备/其他应收款坏账损失",direction="贷",ending=Decimal("30"),level=2)
        principal=row("1122","应收账款",ending=Decimal("500"))
        rows=[root,one,two,principal]
        results=match_fields([receivable,other],rows)
        self.assertEqual([(r.status,r.amount,r.source_ids) for r in results],[("matched",Decimal("10"),["123101"]),("matched",Decimal("30"),["123102"])])
        applied,_=apply_ai_suggestions(results,[{"target_id":receivable.id,"source_codes":["1122"]}],rows)
        self.assertEqual(applied,0)
        self.assertEqual(results[0].amount,Decimal("10"))

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


class ScalingTests(unittest.TestCase):
    """防止匹配退化为逐目标全量排序（曾出现 800 目标 × 2000 科目耗时 60 秒）。"""

    def test_large_batch_completes_quickly(self):
        rows = [LedgerRow(code=f"{6000 + i}", name=f"明细项目{i}", path=f"管理费用/明细项目{i}",
                          opening=Decimal(i), ending=Decimal(i * 2), debit=Decimal(i), credit=Decimal(0),
                          level=2, subject_id="S1", year=2026, month=6) for i in range(1000)]
        targets = [TargetField("期末", f"D{4 + i}", f"未知项目{i}", "ending") for i in range(400)]
        started = perf_counter()
        results = match_fields(targets, rows)
        elapsed = perf_counter() - started
        self.assertEqual(len(results), 400)
        self.assertLess(elapsed, 5.0, f"匹配耗时 {elapsed:.2f}s，疑似退回逐目标全量排序")

    def test_parent_prefix_still_splits_after_indexing(self):
        rows = [row("2221", "应交税费"), row("222101", "应交增值税", level=2, path="应交税费/应交增值税")]
        result = match_fields([target("应交税费—应交增值税")], rows)[0]
        self.assertEqual(result.source_ids, ["222101"])


if __name__ == "__main__":
    unittest.main()
