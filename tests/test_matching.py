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
    def test_missing_receivable_parent_never_falls_back_to_payable_customer(self):
        field=target('甲公司',parent='应收账款',statement='balance_sheet')
        wrong=row('220201','甲公司',path='应付账款/甲公司',direction='贷',level=2,ending=Decimal(100))
        self.assertEqual(match_fields([field],[wrong])[0].status,'ambiguous')
        self.assertEqual(resolve_match(field,[wrong],rows=[wrong]).status,'missing')
        correct_parent=row('1122','应收账款')
        correct=row('112201','甲公司',path='应收账款/甲公司',level=2,ending=Decimal(30))
        self.assertEqual(match_fields([field],[correct_parent,correct,wrong])[0].source_ids,['112201'])

    def test_inventory_rollup_only_uses_known_inventory_roots(self):
        field=target('开发成本',parent='存货',statement='balance_sheet')
        wrong=row('220201','开发成本',path='应付账款/开发成本',level=2)
        self.assertEqual(match_fields([field],[row('2202','应付账款'),wrong])[0].status,'ambiguous')
        parent=row('5001','生产成本')
        correct=row('500101','开发成本',path='生产成本/开发成本',level=2,ending=Decimal(40))
        self.assertEqual(match_fields([field],[parent,correct,wrong])[0].source_ids,['500101'])
        unrelated_inventory=row('1403','原材料')
        self.assertEqual(match_fields([field],[unrelated_inventory,parent,correct,wrong])[0].source_ids,['500101'])

    def test_tb_manual_and_ai_cannot_assign_unrelated_source(self):
        field=target('应收账款',statement='balance_sheet')
        correct=row('1122','应收账款',ending=Decimal(10))
        wrong=row('1002','银行存款',ending=Decimal(100))
        self.assertEqual(resolve_match(field,[wrong],rows=[correct,wrong]).status,'missing')
        self.assertEqual(resolve_match(field,[wrong]).status,'missing')
        self.assertEqual(resolve_match(field,[correct],rows=[correct,wrong]).amount,Decimal(10))
        results=match_fields([field],[correct,wrong])
        applied,_=apply_ai_suggestions(results,[{'target_id':field.id,'source_codes':['1002']}],[correct,wrong])
        self.assertEqual(applied,0)
        self.assertEqual((results[0].source_ids,results[0].amount),(['1122'],Decimal(10)))

    def test_tb_manual_parent_scope_uses_full_ledger(self):
        field=target('其他',parent='管理费用',statement='profit_loss',measure='profit_loss')
        parents=[row('6602','管理费用',is_profit_loss=True),row('6601','销售费用',is_profit_loss=True)]
        correct=row('660201','其他',path='管理费用/其他',level=2,is_profit_loss=True,debit=Decimal(10))
        wrong=row('660101','其他',path='销售费用/其他',level=2,is_profit_loss=True,debit=Decimal(20))
        context=parents+[correct,wrong]
        self.assertEqual(resolve_match(field,[wrong],rows=context).status,'missing')
        self.assertEqual(resolve_match(field,[wrong]).status,'missing')
        self.assertEqual(resolve_match(field,[correct],rows=context).amount,Decimal(10))

    def test_tb_ai_does_not_choose_one_of_ambiguous_sources(self):
        field=target('其他',parent='管理费用',statement='profit_loss',measure='profit_loss')
        context=[row('6602','管理费用',is_profit_loss=True),
            row('660201','其他',path='管理费用/其他',level=2,is_profit_loss=True,debit=Decimal(10)),
            row('660202','其他',path='管理费用/其他',level=2,is_profit_loss=True,debit=Decimal(20))]
        results=match_fields([field],context)
        applied,_=apply_ai_suggestions(results,[{'target_id':field.id,'source_codes':['660201']}],context)
        self.assertEqual(applied,0)
        self.assertEqual(results[0].status,'ambiguous')
        self.assertEqual(resolve_match(field,[context[1]],rows=context).status,'matched')

    def test_tb_index_code_cannot_override_conflicting_text(self):
        field=target('应收账款',index_code='1002',statement='balance_sheet')
        context=[row('1122','应收账款'),row('1002','银行存款')]
        self.assertEqual(match_fields([field],context)[0].status,'missing')

    def test_asset_contra_is_independent_of_asset_parent_scope(self):
        cases=[('累计折旧','固定资产原价','累计折旧'),('无形资产累计摊销','无形资产','累计摊销'),
               ('固定资产减值准备','固定资产原价','固定资产减值准备'),
               ('无形资产减值准备','无形资产','无形资产减值准备'),
               ('在建工程减值准备','在建工程','在建工程减值准备')]
        for label,parent,source_name in cases:
            with self.subTest(label=label):
                field=target(label,parent=parent,statement='balance_sheet',presentation_direction='贷')
                original=row('1601',parent,ending=Decimal(100))
                contra=row('1602',source_name,direction='贷',ending=Decimal(30))
                expense=row('660201',source_name,path='管理费用/'+source_name,level=2,is_profit_loss=True)
                result=match_fields([field],[original,contra,expense])[0]
                self.assertEqual((result.status,result.amount,result.source_ids),('matched',Decimal(30),['1602']))
                self.assertEqual(resolve_match(field,[original]).status,'missing')
                self.assertEqual(resolve_match(field,[expense]).status,'missing')

    def test_asset_contra_reversal_and_multiple_roots_are_preserved(self):
        field=target('累计折旧',parent='固定资产',statement='balance_sheet',presentation_direction='贷')
        a=row('1602','累计折旧',ending=Decimal(-4),direction='贷')
        b=row('1603','固定资产累计折旧',ending=Decimal(8),direction='贷')
        self.assertEqual(match_fields([field],[a])[0].amount,Decimal(-4))
        self.assertEqual(match_fields([field],[a,b])[0].status,'ambiguous')

    def test_inventory_total_uses_unique_root_without_repeating_same_named_child(self):
        for label,source_name in [('原材料','原材料'),('库存商品(产成品)','库存商品'),('发出商品','发出商品')]:
            with self.subTest(label=label):
                field=target(label,parent='存货',statement='balance_sheet',presentation_direction='借')
                root=row('1405',source_name,ending=Decimal(60))
                child=row('140501',source_name,path=source_name+'/'+source_name,level=2,ending=Decimal(20))
                result=match_fields([field],[child,root])[0]
                self.assertEqual((result.status,result.amount,result.source_ids),('matched',Decimal(60),['1405']))
                self.assertEqual(resolve_match(field,[child]).status,'missing')

    def test_deferred_asset_cannot_take_development_cost_child_with_same_name(self):
        field=target('待摊费用',statement='balance_sheet',presentation_direction='借')
        source=row('500101','待摊费用',path='开发成本/待摊费用',level=2)
        self.assertEqual(match_fields([field],[source])[0].status,'ambiguous')
        self.assertEqual(resolve_match(field,[source]).status,'missing')
        root=row('1801','待摊费用',ending=Decimal(50))
        self.assertEqual(match_fields([field],[root,source])[0].status,'ambiguous')

    def test_deferred_asset_does_not_create_false_duplicate_inventory_claim(self):
        fields=[target('开发成本',parent='存货',cell='D20',statement='balance_sheet'),
                target('待摊费用',cell='D40',statement='balance_sheet')]
        root=row('5001','开发成本',ending=Decimal(70))
        child=row('500101','待摊费用',path='开发成本/待摊费用',level=2)
        results=match_fields(fields,[root,child])
        self.assertEqual([r.status for r in results],['matched','ambiguous'])

    def test_loan_principal_and_accrued_interest_use_explicit_components(self):
        fields=[target('长期借款-借款本金',cell='D30',statement='balance_sheet',presentation_direction='贷'),
                target('长期借款-应计利息',cell='D31',statement='balance_sheet',presentation_direction='贷')]
        sources=[row('2501','长期借款',ending=Decimal(105),direction='贷'),
                 row('250101','本金',path='长期借款/本金',ending=Decimal(100),direction='贷',level=2),
                 row('250102','应计利息',path='长期借款/应计利息',ending=Decimal(5),direction='贷',level=2),
                 row('250103','利息调整',path='长期借款/利息调整',ending=Decimal(1),direction='贷',level=2)]
        results=match_fields(fields,sources)
        self.assertEqual([(r.status,r.amount,r.source_ids) for r in results],[('matched',Decimal(100),['250101']),('matched',Decimal(5),['250102'])])
        for field in fields:
            self.assertEqual(resolve_match(field,[sources[0]]).status,'missing')
            self.assertEqual(resolve_match(field,[sources[3]]).status,'missing')

    def test_loan_component_does_not_cross_categories_or_guess_disjoint_principals(self):
        field=target('长期借款-借款本金',statement='balance_sheet')
        short=row('200101','本金',path='短期借款/本金',level=2)
        a=row('250101','本金',path='长期借款/本金',level=2)
        b=row('250102','借款本金',path='长期借款/借款本金',level=2)
        self.assertEqual(match_fields([field],[short])[0].status,'unmatched')
        self.assertEqual(resolve_match(field,[short]).status,'missing')
        self.assertEqual(match_fields([field],[a,b])[0].status,'ambiguous')
        parents=[row('2501','长期借款'),row('2502','长期借款')]
        self.assertEqual(match_fields([field],parents+[a])[0].status,'ambiguous')
        due=target('一年内到期的长期借款-借款本金',statement='balance_sheet')
        self.assertEqual(match_fields([due],[a])[0].status,'unmatched')
        conflicting=target('长期借款-借款本金',parent='一年内到期的非流动负债',statement='balance_sheet')
        explicit=row('250101','借款本金',path='长期借款/借款本金',level=2)
        self.assertEqual(match_fields([conflicting],[explicit])[0].status,'ambiguous')
        self.assertEqual(resolve_match(conflicting,[explicit]).status,'missing')
        unrelated=row('224101','长期借款-本金',path='其他应付款/长期借款-本金',level=2)
        self.assertEqual(resolve_match(field,[unrelated]).status,'missing')

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
