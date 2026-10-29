"""Synthetic standalone detail boundaries; no customer data."""
from dataclasses import replace
from decimal import Decimal
import unittest

from tb_autofill import matching
from tb_autofill.models import LedgerRow, TargetField


class StandaloneDetailsTests(unittest.TestCase):
    def target(self, label, parent, **kw):
        return TargetField('试算平衡表(期末)', 'Q91', label, 'ending', parent=parent,
                           statement='balance_sheet', presentation_direction='贷', **kw)

    def test_tax_prefixes_stay_in_the_specific_tax_root(self):
        root=LedgerRow('T','应交税金',direction='贷')
        for label in ('教育费附加','地方教育费附加','房产税','企业所得税','防洪费'):
            with self.subTest(label=label):
                row=LedgerRow('T01','应交'+label,path='应交税金/应交'+label,direction='贷',ending=Decimal('-7'),level=2)
                expense=replace(row,code='E01',path='税金及附加/应交'+label,is_profit_loss=True)
                target=self.target('其他应交-'+label,'应交税费')
                result=matching.match_fields([target],[root,row,expense])[0]
                self.assertEqual((result.status,result.amount,result.source_ids),('matched',Decimal('-7'),['T01']))
                self.assertNotEqual(matching.resolve_match(target,[expense],rows=[root,row,expense]).status,'matched')
                wrong=replace(target,parent='其他流动资产',label='应交税费-'+label)
                self.assertNotEqual(matching.resolve_match(wrong,[row],rows=[root,row]).status,'matched')

    def test_national_local_education_and_income_tax_scopes_are_distinct(self):
        root=LedgerRow('T','应交税费',direction='贷')
        local=LedgerRow('T01','地方教育费附加',path='应交税费/地方教育费附加',direction='贷',ending=Decimal(9),level=2)
        ordinary=self.target('其他应交-教育费附加','应交税费')
        self.assertNotEqual(matching.match_fields([ordinary],[root,local])[0].status,'matched')
        unspecified=replace(local,name='应交所得税',path='应交税费/应交所得税')
        corporate=self.target('应交税费-企业所得税','应交税费')
        self.assertNotEqual(matching.match_fields([corporate],[root,unspecified])[0].status,'matched')
        missing=replace(local,ending=None)
        self.assertNotEqual(matching.match_fields([self.target('其他应交-地方教育费附加','应交税费')],[root,missing])[0].status,'matched')

    def test_tax_name_under_another_tax_category_cannot_be_selected(self):
        root=LedgerRow('T','应交税费',direction='贷')
        wrong=LedgerRow('T0101','教育费附加',path='应交税费/个人所得税/教育费附加',direction='贷',ending=Decimal(7),level=3)
        target=self.target('其他应交-教育费附加','应交税费')
        for source in (wrong,replace(wrong,level=2),replace(wrong,level=2,path='应交税费/个人所得税')):
            rows=[root,source]
            results=matching.match_fields([target],rows)
            self.assertNotEqual(results[0].status,'matched')
            self.assertNotEqual(matching.resolve_match(target,[source],rows=rows).status,'matched')
            applied,_=matching.apply_ai_suggestions(results,[{'target_id':target.id,'source_codes':[source.code]}],rows)
            self.assertEqual(applied,0)

    def test_tax_detail_accepts_only_explicit_other_tax_group(self):
        root=LedgerRow('T','应交税费',direction='贷')
        target=self.target('应交税费-印花税','应交税费')
        row=LedgerRow('T0101','印花税',path='应交税费/其他应缴税费/印花税',direction='贷',ending=Decimal(4),level=3)
        for middle in ('其他应缴税费','其他应交税费'):
            self.assertEqual(matching.match_fields([target],[root,replace(row,path='应交税费/'+middle+'/印花税')])[0].amount,Decimal(4))
        for path in ('应交税费/工程类/印花税','应交税费/个人所得税/印花税','应交税费/其他应缴税费/工程类/印花税'):
            self.assertNotEqual(matching.match_fields([target],[root,replace(row,path=path)])[0].status,'matched')

    def test_flood_fee_is_not_automatically_equated_to_water_fund(self):
        root=LedgerRow('T','应交税费',direction='贷')
        flood=LedgerRow('T0101','防洪费',path='应交税费/其他应缴税费/防洪费',direction='贷',ending=Decimal(0),level=3)
        fund=replace(flood,code='T0102',name='水利建设基金',path='应交税费/其他应缴税费/水利建设基金',ending=Decimal(6))
        target=self.target('其他应交-应交防洪费','应交税费')
        results=matching.match_fields([target],[root,flood,fund])
        self.assertEqual(results[0].status,'ambiguous')
        self.assertFalse(results[0].selected)
        self.assertIsNone(results[0].amount)
        applied,_=matching.apply_ai_suggestions(results,[{'target_id':target.id,'source_codes':['T0101']}],[root,flood,fund])
        self.assertEqual(applied,0)
        self.assertNotEqual(matching.resolve_match(target,[fund],rows=[root,flood,fund]).status,'matched')
        self.assertEqual(matching.match_fields([target],[root,flood])[0].amount,Decimal(0))

    def test_independent_contra_roots_and_reversals(self):
        for label,parent in (('使用权资产累计折旧','使用权资产'),('合同资产减值准备','合同资产'),('债权投资减值准备','债权投资成本')):
            with self.subTest(label=label):
                target=self.target(label,parent)
                row=LedgerRow('C',label,path=label,direction='贷',ending=Decimal('-3'))
                expense=replace(row,code='E',path='管理费用/'+label,is_profit_loss=True)
                asset=replace(row,code='A',name=parent,path=parent,direction='借',ending=Decimal(20))
                result=matching.match_fields([target],[row,expense,asset])[0]
                self.assertEqual((result.status,result.amount,result.source_ids),('matched',Decimal('-3'),['C']))
                for wrong in (expense,asset,replace(row,level=2,path=parent+'/'+label)):
                    self.assertNotEqual(matching.resolve_match(target,[wrong],rows=[row,wrong]).status,'matched')
                duplicates=[row,replace(row,code='C2')]
                self.assertEqual(matching.match_fields([target],duplicates)[0].status,'ambiguous')
                self.assertNotEqual(matching.resolve_match(replace(target,basis='audited'),[row],rows=[row]).status,'matched')

    def test_welfare_requires_direct_payroll_category_without_guessing_workers(self):
        root=LedgerRow('P','应付职工薪酬',direction='贷')
        row=LedgerRow('P01','应付福利费',path='应付职工薪酬/应付福利费',direction='贷',ending=Decimal('-8'),level=2)
        standalone=replace(row,code='S',path='应付福利费',level=1)
        expense=replace(row,code='E01',path='管理费用/应付福利费',is_profit_loss=True)
        target=self.target('应付薪酬-职工福利','应付职工薪酬')
        result=matching.match_fields([target],[root,row,standalone,expense])[0]
        self.assertEqual((result.status,result.amount,result.source_ids),('matched',Decimal('-8'),['P01']))
        for wrong in (standalone,expense,replace(row,code='P0201',path='应付职工薪酬/劳务派遣/应付福利费',level=3),
                      replace(row,code='P0201',path='应付职工薪酬/劳务派遣/应付福利费',level=2),
                      replace(row,code='P02',path='应付职工薪酬/工资')):
            self.assertNotEqual(matching.resolve_match(target,[wrong],rows=[root,row,wrong]).status,'matched')
        other=replace(row,code='P02',name='职工福利费',path='应付职工薪酬/职工福利费')
        self.assertEqual(matching.match_fields([target],[root,row,other])[0].status,'ambiguous')
        ai=matching.match_fields([target],[root,row,other]);applied,_=matching.apply_ai_suggestions(ai,[{'target_id':target.id,'source_codes':['P01']}],[root,row,other])
        self.assertEqual(applied,0)


if __name__=='__main__':unittest.main()
