"""Synthetic regressions for TB inputs and separate DXN accounting datasets."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from openpyxl import Workbook

from tb_autofill import dxn, fields, matching, workbook
from tb_autofill.models import LedgerRow, Subject, TargetField


class TbInputTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "synthetic.xlsx"

    def make_book(self, shift=False, duplicate=False):
        book = Workbook()
        book.remove(book.active)
        for title, header in (("试算平衡表（期初）", "上期审定数"), ("试算平衡表(期末)", "期末未审数")):
            sheet = book.create_sheet(title)
            sheet["A1"] = "试算平衡表"
            sheet["K4"] = "=封面!J15"
            sheet.merge_cells("A6:B7")
            sheet["A6"] = "报表项目"
            sheet["C6"] = "索引号"
            column = 6 if shift else 4
            sheet.cell(6, column, header)
            sheet.cell(6, column + 1, "审计调整")
            sheet.cell(7, column + 1, "借方")
            sheet.cell(6, column + 2, "期末审定数")
            if duplicate:
                sheet.cell(6, column + 3, header)
            sheet["A8"] = "流动资产："
            sheet["A9"] = "银行存款"
            sheet["C9"] = "QB"
            sheet.cell(10, 1, "其他货币资金")
            sheet.cell(10, column, f"=SUM({sheet.cell(11,column).coordinate})")
            sheet["A11"] = "其中："
            sheet["B11"] = "外埠存款"
            sheet["A12"] = "被审计单位：合成公司"
            sheet["A13"] = "会计期间：2026年1-12月"
        extra = book.create_sheet("调整分录")
        extra.append(["科目名称", "期末余额"])
        extra.append(["银行存款", 999])
        extra["D1"] = "金额单位：万元"
        cover = book.create_sheet("封面")
        cover["G7"] = "元"
        cover["J15"] = '="金额单位："&G7'
        book.save(self.path)
        book.close()

    def test_only_requested_columns_and_labels_with_moved_column(self):
        for shift in (False, True):
            with self.subTest(shift=shift):
                self.make_book(shift=shift)
                before = fields.file_digest(self.path)
                scan = fields.scan_template(self.path, ("opening", "ending"))
                self.assertEqual(scan.unit, "元")
                self.assertEqual(len(scan.fields), 6)
                self.assertEqual({t.cell[0] for t in scan.fields}, {"F" if shift else "D"})
                self.assertEqual({(t.period,t.basis) for t in scan.fields}, {("opening","audited"),("ending","book")})
                self.assertEqual({t.label for t in scan.fields}, {"银行存款","其他货币资金","外埠存款"})
                self.assertEqual(fields.file_digest(self.path), before)

    def test_opening_book_data_is_blocked_and_formula_preserved(self):
        self.make_book()
        scan = fields.scan_template(self.path, ("opening", "ending"))
        rows = [LedgerRow("1002","银行存款",opening=Decimal(4),ending=Decimal(9))]
        results = matching.match_fields(scan.fields, rows)
        by_id = {r.target.id:r for r in results}
        self.assertEqual(by_id["试算平衡表（期初）!D9"].status,"basis_mismatch")
        self.assertEqual(by_id["试算平衡表(期末)!D9"].amount,Decimal(9))
        self.assertEqual(by_id["试算平衡表(期末)!D10"].status,"protected")
        writes = workbook.planned_writes(scan,results)
        self.assertEqual([(t.id,a) for t,a in writes],[("试算平衡表(期末)!D9",Decimal(9))])

    def test_duplicate_input_columns_require_review(self):
        self.make_book(duplicate=True)
        scan = fields.scan_template(self.path,("ending",))
        self.assertEqual(scan.fields,[])
        self.assertTrue(any("多列" in warning for warning in scan.warnings))

    def test_consolidation_headers_are_not_book_inputs(self):
        self.make_book()
        from openpyxl import load_workbook
        book=load_workbook(self.path)
        for sheet in book.worksheets[:2]:
            sheet["D6"]="子公司甲"
        book.save(self.path)
        book.close()
        self.assertEqual(fields.scan_template(self.path,("opening","ending")).fields,[])

    def test_elimination_workbook_is_not_filled_from_a_single_company(self):
        self.path=self.path.with_stem("synthetic（合并抵消）")
        self.make_book()
        scan=fields.scan_template(self.path,("opening","ending"))
        self.assertEqual(scan.fields,[])
        self.assertTrue(any("合并或抵消" in w for w in scan.warnings))

    def test_company_name_containing_yuan_is_not_a_unit(self):
        book=Workbook()
        sheet=book.active
        sheet.append(["被审计单位：","元丰公司"])
        sheet.append(["金额单位：","万元"])
        sheet.append(["科目名称","期末余额"])
        sheet.append(["银行存款",None])
        book.save(self.path)
        book.close()
        self.assertEqual(fields.scan_template(self.path).unit,"万元")

    def test_internal_label_reference_is_recognized_without_executing_formulas(self):
        book=Workbook()
        opening=book.active
        opening.title='试算平衡表(期初)'
        opening['A35']='其他应付款'
        opening['C35']=15
        ending=book.create_sheet('试算平衡表(期末)')
        ending['A40']='报表项目'
        ending['C40']='索引号'
        ending['F40']='期末未审数'
        ending['A41']='流动负债：'
        ending['A42']="='试算平衡表(期初)'!A35"
        ending['C42']="='试算平衡表(期初)'!C35"
        ending['A43']="='[old.xlsx]试算平衡表(期初)'!A35"
        book.save(self.path)
        book.close()
        scan=fields.scan_template(self.path,('ending',),'元')
        self.assertEqual([(f.cell,f.label,f.presentation_direction,f.index_code) for f in scan.fields], [('F42','其他应付款','贷','15')])
        source=LedgerRow('2241','其他应付款',ending=Decimal(900),direction='贷')
        result=matching.match_fields(scan.fields,[source])[0]
        self.assertEqual((result.status,result.amount),('matched',Decimal(900)))

    def test_new_primary_account_resets_parent_without_an_index_or_formula(self):
        book=Workbook();sheet=book.active;sheet.title='试算平衡表(期末)'
        for address,value in {'A6':'报表项目','D6':'期末未审数','A8':'流动资产：',
            'A9':'货币资金','D9':'=SUM(D10)','A10':'其中：','B10':'银行存款',
            'A12':'应收账款','A13':'其中：','B13':'甲公司'}.items():sheet[address]=value
        book.save(self.path);book.close()
        scan=fields.scan_template(self.path,('ending',),'元')
        by_label={f.label:f for f in scan.fields}
        self.assertEqual(by_label['银行存款'].parent,'货币资金')
        self.assertEqual(by_label['应收账款'].parent,'')
        self.assertEqual(by_label['甲公司'].parent,'应收账款')

    def test_asset_contra_and_loan_fields_survive_row_and_column_moves(self):
        for moved in (False,True):
            with self.subTest(moved=moved):
                book=Workbook()
                sheet=book.active
                sheet.title='试算平衡表(期末)'
                for address,value in {'A6':'报表项目','C6':'索引号','F6':'期末未审数',
                    'A8':'非流动资产：','A9':'固定资产原价','C9':'ZC1','A10':'减：','B10':'累计折旧',
                    'A12':'非流动负债：','A13':'长期借款','C13':'FA1','F13':'=SUM(F14)',
                    'A14':'长期借款-借款本金'}.items():sheet[address]=value
                if moved:sheet.move_range('A6:F14',rows=37,cols=3,translate=True)
                book.save(self.path);book.close()
                scan=fields.scan_template(self.path,('ending',),'元')
                rows=[LedgerRow('1601','固定资产',ending=Decimal(80)),
                      LedgerRow('1602','累计折旧',direction='贷',ending=Decimal(20)),
                      LedgerRow('2501','长期借款',direction='贷',ending=Decimal(90)),
                      LedgerRow('250101','本金',path='长期借款/本金',direction='贷',ending=Decimal(90),level=2)]
                results=matching.match_fields(scan.fields,rows)
                by_label={r.target.label:r for r in results}
                self.assertEqual((by_label['累计折旧'].status,by_label['累计折旧'].amount),('matched',Decimal(20)))
                self.assertEqual((by_label['长期借款-借款本金'].status,by_label['长期借款-借款本金'].amount),('matched',Decimal(90)))
                self.assertEqual(by_label['长期借款'].status,'protected')

    def test_statement_sections_and_deduction_rows_set_presentation_direction(self):
        book=Workbook()
        sheet=book.active
        sheet.title="试算平衡表(期末)"
        for address,value in {"A6":"报表项目","D6":"期末未审数","A8":"流动资产：",
                              "A9":"存货","A10":"减：","B10":"存货跌价准备",
                              "A11":"流动负债：","A12":"专项应付款","A13":"减：",
                              "B13":"未确认的融资费用","A14":"股东（所有者）权益：",
                              "A15":"减：","B15":"库存股","A16":"利润表试算平衡",
                              "A17":"报表项目","D17":"期末未审数","A18":"主营业务成本"}.items():
            sheet[address]=value
        book.save(self.path)
        book.close()
        scan=fields.scan_template(self.path,("ending",),"元")
        actual={f.cell:(f.statement,f.presentation_direction) for f in scan.fields}
        self.assertEqual(actual,{"D9":("balance_sheet","借"),"D10":("balance_sheet","贷"),
                                 "D12":("balance_sheet","贷"),"D13":("balance_sheet","借"),
                                 "D15":("balance_sheet","借"),"D18":("profit_loss","")})

    def test_property_cost_role_requires_separate_fair_value_row(self):
        self.make_book()
        from openpyxl import load_workbook
        book=load_workbook(self.path)
        sheet=book['试算平衡表(期末)']
        for address,value in {"A12":"投资性房地产","A13":"减：","B13":"投资性房地产累计折旧",
                              "A14":"投资性房地产净值","D14":"=D12-D13","A15":"加：",
                              "B15":"投资性房地产公允价值变动","A16":"投资性房地产成本（或公允价值）",
                              "D16":"=D14+D15"}.items():
            sheet[address]=value
        book.save(self.path)
        book.close()
        scan=fields.scan_template(self.path,("ending",),"元")
        by_cell={f.cell:f for f in scan.fields}
        self.assertEqual(by_cell['D12'].component,'cost')
        self.assertEqual(by_cell['D15'].component,'fair_value_change')
        self.assertEqual(by_cell['D16'].component,'')
        self.assertTrue(by_cell['D16'].is_formula)
        book=load_workbook(self.path)
        sheet=book['试算平衡表(期末)']
        sheet.move_range('A15:D16',rows=30,cols=0)
        book.save(self.path)
        book.close()
        moved=fields.scan_template(self.path,("ending",),"元")
        self.assertEqual(next(f for f in moved.fields if f.label=='投资性房地产').component,'cost')
        book=load_workbook(self.path)
        book['封面']['Z99']='投资性房地产公允价值变动'
        book['试算平衡表(期末)']['B45']='=封面!Z99'
        book.save(self.path)
        book.close()
        linked=fields.scan_template(self.path,("ending",),"元")
        self.assertEqual(next(f for f in linked.fields if f.label=='投资性房地产').component,'cost')
        self.assertEqual(next(f for f in linked.fields if f.label=='投资性房地产公允价值变动').component,'fair_value_change')

    def test_cash_and_bank_identification_survives_row_and_column_movement(self):
        self.make_book()
        from openpyxl import load_workbook
        book=load_workbook(self.path)
        sheet=book['试算平衡表(期末)']
        sheet['A9']='其中：'
        sheet['B9']='现  金（库存现金）'
        sheet['A10']='货币资金'
        sheet['A11']=''
        sheet['B11']='银行存款'
        sheet.move_range('D1:H13',rows=0,cols=3)
        sheet.insert_rows(1,37)
        book.save(self.path)
        book.close()
        scan=fields.scan_template(self.path,("ending",),"元")
        sources=[LedgerRow('1001','库存现金',ending=Decimal(0)),LedgerRow('1002','银行存款',ending=Decimal(25))]
        results=matching.match_fields(scan.fields,sources)
        amounts={matching._canonical(r.target.label):r.amount for r in results if r.status=='matched'}
        self.assertEqual(amounts['库存现金'],Decimal(0))
        self.assertEqual(amounts['银行存款'],Decimal(25))
        self.assertTrue(all(r.target.cell.startswith('G') for r in results))


class PresentationTests(unittest.TestCase):
    def target(self,label,**kwargs):
        return TargetField("期末","D10",label,"ending",measure="profit_loss",**kwargs)

    def test_nested_numbering_matches_cost(self):
        row=LedgerRow("6401","主营业务成本",debit=Decimal(20),credit=Decimal(20),is_profit_loss=True)
        result=matching.match_fields([self.target("减：（一）主营业务成本")],[row])[0]
        self.assertEqual((result.status,result.amount),("matched",Decimal(20)))

    def test_interest_income_presentation_keeps_reversal_sign(self):
        target=self.target('财务费用-利息收入（以“+”值填列）')
        parent=LedgerRow("6603","财务费用",debit=Decimal(50),is_profit_loss=True)
        row=LedgerRow("660302","利息收入",path="财务费用/利息收入",debit=Decimal(-3),is_profit_loss=True)
        result=matching.match_fields([target],[parent,row])[0]
        self.assertEqual((result.status,result.amount),("matched",Decimal(3)))
        self.assertEqual(matching.resolve_match(target,[replace(row,debit=Decimal(3))]).amount,Decimal(-3))
        self.assertEqual(matching.resolve_match(target,[replace(row,direction="贷",credit=Decimal(3))]).amount,Decimal(3))

    def test_impairment_loss_presentation_inherits_parent_sign(self):
        target=self.target("信用减值-其他应收款坏账损失",parent='信用减值损失（损失以“－”号填列）')
        row=LedgerRow("670202","其他应收款坏账损失",debit=Decimal(7),is_profit_loss=True)
        self.assertEqual(matching.resolve_match(target,[row]).amount,Decimal(-7))
        self.assertEqual(matching.resolve_match(target,[replace(row,debit=Decimal(-7))]).amount,Decimal(7))
        self.assertEqual(matching._canonical('信用减值损失（损失以“－”号填列）'),"信用减值损失")

    def test_mixed_dataset_ids_are_rejected(self):
        a=LedgerRow("1001","库存现金",ledger_id="1")
        b=LedgerRow("1002","银行存款",ledger_id="2")
        with self.assertRaisesRegex(ValueError,"主体"):
            matching.match_fields([], [a,b])

    def test_balance_account_is_not_a_profit_loss_occurrence(self):
        row=LedgerRow("4104","未分配利润",debit=Decimal(90),ending=Decimal(100))
        result=matching.resolve_match(self.target("未分配利润"),[row])
        self.assertEqual(result.status,"missing")
        self.assertFalse(result.selected)

    def test_profit_subtotal_note_does_not_reverse_following_expenses(self):
        target=self.target("（二）管理费用",parent='三、主营业务利润（亏损以“-”号填列）')
        row=LedgerRow("521","管理费用",debit=Decimal(30),is_profit_loss=True)
        self.assertEqual(matching.resolve_match(target,[row]).amount,Decimal(30))

    def test_bad_debt_component_is_scoped_to_allowance_account(self):
        source=LedgerRow("123101","应收账款",path="坏账准备/应收账款",ending=Decimal(3),level=2,direction="贷")
        rows=[LedgerRow("1122","应收账款",ending=Decimal(100)),LedgerRow("1231","坏账准备"),source]
        target=TargetField("期末","D55","应收账款坏账准备","ending",parent="应收账款")
        result=matching.match_fields([target],rows)[0]
        self.assertEqual((result.status,result.source_ids,result.amount),("matched",["123101"],Decimal(3)))

    def test_deferred_finance_cost_uses_debit_presentation_without_abs(self):
        target=TargetField("期末","D350","未确认融资费用","ending",parent="租赁负债")
        source=LedgerRow("227101","未确认融资费用",direction="贷",ending=Decimal(-6))
        self.assertEqual(matching.resolve_match(target,[source]).amount,Decimal(6))
        self.assertEqual(matching.resolve_match(target,[replace(source,ending=Decimal(6))]).amount,Decimal(-6))
        self.assertEqual(matching.resolve_match(target,[replace(source,direction="借",ending=Decimal(6))]).amount,Decimal(6))

    def test_interest_income_credit_without_closing_uses_consistent_movement(self):
        target=self.target('财务费用-利息收入（以“+”值填列）')
        source=LedgerRow("52207","利息收入",direction="借",opening=Decimal(0),ending=Decimal(-7),
                         debit=Decimal(0),credit=Decimal(7),is_profit_loss=True)
        self.assertEqual(matching.resolve_match(target,[source]).amount,Decimal(7))
        self.assertEqual(matching.resolve_match(target,[replace(source,ending=Decimal(-8))]).status,"missing")

    def test_interest_income_with_both_positive_sides_requires_review(self):
        target=self.target('财务费用-利息收入（以“+”值填列）')
        source=LedgerRow("52202","利息收入",direction="借",debit=Decimal(7),credit=Decimal(7),is_profit_loss=True)
        result=matching.resolve_match(target,[source])
        self.assertEqual(result.status,"missing")
        self.assertIsNone(result.amount)
        self.assertFalse(result.selected)
        self.assertIn("结转方式",result.reason)


def record(code="1001",name="库存现金",month=1,level=1):
    return dict(kmbh=code,kmmc=name,kjmonth=month,kmjc=level,jdfx="借",qcs="10",qms="12",jffse="2",dffse="0")


class DatasetTests(unittest.TestCase):
    def test_draft_binds_both_company_and_dataset(self):
        book=MagicMock()
        book.sheet_names.return_value=["dxnsjtempsheet","数据表"]
        meta=MagicMock(nrows=3,ncols=2)
        data=[["单位名称","合成公司"],["会计年度期末",2026],["会计期间期末",6]]
        meta.cell_value.side_effect=lambda r,c:data[r][c]
        main=MagicMock(nrows=2)
        main.cell_value.side_effect=lambda r,c:["I1D2N18Z0L0","I2D2N18Z0L0"][r]
        book.sheet_by_name.side_effect=lambda name:meta if name=="dxnsjtempsheet" else main
        with patch("xlrd.open_workbook",return_value=book):
            subject=dxn._subject_from_draft(Path("synthetic.xls"),Path("project"))
        self.assertEqual((subject.id,subject.ledger_id),("2","18"))

    def test_read_filters_dataset_instead_of_combining_same_month(self):
        subject=Subject("2","合成公司",2026,1,"synthetic",ledger_id="18")
        db=object.__new__(dxn.DxnLedgerDB)
        db.data=Path("synthetic/data")
        db.connection=MagicMock()
        cursor=db.connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value=[record()]
        with patch.object(dxn,"_data_dir",return_value=db.data):
            rows=db.read(subject)
        sql,params=cursor.execute.call_args.args
        self.assertIn("ndid=%s",sql)
        self.assertEqual(params,(2,18,2026,1,1))
        self.assertEqual(rows[0].ledger_id,"18")

    def test_missing_dataset_id_never_selects_one_of_multiple_versions(self):
        subject=Subject("2","合成公司",2026,1,"synthetic")
        db=object.__new__(dxn.DxnLedgerDB)
        db.data=Path("synthetic/data")
        db.connection=MagicMock()
        cursor=db.connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value=[{"ndid":18},{"ndid":19}]
        with patch.object(dxn,"_data_dir",return_value=db.data),self.assertRaisesRegex(ValueError,"唯一账套"):
            db.read(subject)

    def test_missing_months_do_not_produce_complete_occurrence(self):
        subject=Subject("2","合成公司",2026,3,"synthetic",ledger_id="18")
        rows=dxn._aggregate([record(month=1),record(month=3)],subject)
        self.assertIsNone(rows[0].debit)
        self.assertIsNone(rows[0].credit)
        self.assertEqual(rows[0].ending,Decimal(12))

    def test_capitalized_interest_inherits_cost_root(self):
        subject=Subject("2","合成公司",2026,1,"synthetic")
        rows=dxn._aggregate([record("5001","开发成本"),record("500101","利息支出",level=2)],subject)
        self.assertFalse(any(r.is_profit_loss for r in rows))

    def test_legacy_profit_loss_codes_and_manufacturing_cost_are_distinct(self):
        subject=Subject("2","合成公司",2026,1,"synthetic")
        rows=dxn._aggregate([record("521","管理费用"),record("52101","办公费",level=2),
                            record("5101","制造费用"),record("510101","折旧费",level=2)],subject)
        self.assertEqual({r.code:r.is_profit_loss for r in rows},
                         {"521":True,"52101":True,"5101":False,"510101":False})


class StatementSourceTests(unittest.TestCase):
    def test_bank_total_uses_semantic_root_even_when_child_comes_first(self):
        field=TargetField('期末','Q57','银行存款','ending',parent='货币资金',statement='balance_sheet',presentation_direction='借')
        root=LedgerRow('B1','银行存款',ending=Decimal(12),level=1)
        child=LedgerRow('B101','银行存款',path='银行存款/银行存款',ending=Decimal(5),level=2)
        results=matching.match_fields([field],[child,root])
        self.assertEqual((results[0].status,results[0].amount,results[0].source_ids),('matched',Decimal(12),['B1']))
        applied,_=matching.apply_ai_suggestions(results,[{'target_id':field.id,'source_codes':['B101']}],[child,root])
        self.assertEqual(applied,0)
        self.assertEqual(results[0].amount,Decimal(12))

    def test_bank_total_does_not_guess_when_only_an_account_detail_is_available(self):
        field=TargetField('期末','Q57','银行存款','ending',statement='balance_sheet')
        child=LedgerRow('B101','银行存款',path='银行存款/银行存款',ending=Decimal(5),level=2)
        self.assertEqual(matching.match_fields([field],[child])[0].status,'ambiguous')
        self.assertEqual(matching.resolve_match(field,[child]).status,'missing')

    def target(self,**kwargs):
        return TargetField("试算平衡表(期末)","D178","投资性房地产","ending",statement="balance_sheet",
                           presentation_direction="借",**kwargs)

    def test_profit_component_cannot_be_used_as_property_balance(self):
        source=LedgerRow("610103","投资性房地产",path="公允价值变动损益/投资性房地产",
                         direction="贷",ending=Decimal(0),credit=Decimal(0),is_profit_loss=True)
        target=self.target()
        for result in (matching.match_fields([target],[source])[0],matching.resolve_match(target,[source])):
            self.assertEqual(result.status,"missing")
            self.assertIsNone(result.amount)
            self.assertFalse(result.selected)

    def test_asset_source_is_used_when_a_profit_component_has_the_same_name(self):
        asset=LedgerRow("1521","投资性房地产",ending=Decimal(100))
        profit=LedgerRow("610103","投资性房地产",path="公允价值变动损益/投资性房地产",
                         direction="贷",credit=Decimal(8),is_profit_loss=True)
        results=matching.match_fields([self.target()],[asset,profit])
        self.assertEqual((results[0].status,results[0].amount,results[0].source_ids),("matched",Decimal(100),["1521"]))
        applied,notes=matching.apply_ai_suggestions(results,[{"target_id":self.target().id,"source_codes":["610103"]}],[asset,profit])
        self.assertEqual(applied,0)
        self.assertEqual(results[0].amount,Decimal(100))

    def test_profit_parent_name_does_not_hide_a_valid_asset_component(self):
        parent=LedgerRow("1529","投资性房地产（公允计价）")
        asset=LedgerRow("152902","投资性房地产累计折旧",path="投资性房地产（公允计价）/投资性房地产累计折旧",
                        direction="贷",ending=Decimal(10),level=2)
        profit=LedgerRow("610103","投资性房地产",path="公允价值变动损益/投资性房地产",is_profit_loss=True)
        field=TargetField("期末","D179","投资性房地产累计折旧","ending",parent="投资性房地产",
                          statement="balance_sheet",presentation_direction="贷")
        result=matching.match_fields([field],[parent,asset,profit])[0]
        self.assertEqual((result.status,result.amount,result.source_ids),("matched",Decimal(10),["152902"]))

    def test_asset_deduction_uses_credit_direction_and_preserves_reversal(self):
        target=TargetField("期末","D127","减：一年内到期的未确认融资收益","ending",
                           statement="balance_sheet",presentation_direction="贷")
        source=LedgerRow("190402","一年内到期的未确认融资收益",ending=Decimal(-6),direction="借")
        self.assertEqual(matching.resolve_match(target,[source]).amount,Decimal(6))
        self.assertEqual(matching.resolve_match(target,[replace(source,ending=Decimal(6))]).amount,Decimal(-6))

    def test_liability_presentation_does_not_assume_ledger_nature_is_credit(self):
        target=TargetField("期末","D363","专项应付款","ending",statement="balance_sheet",presentation_direction="贷")
        source=LedgerRow("276","专项应付款",direction="借",ending=Decimal(15))
        self.assertEqual(matching.resolve_match(target,[source]).amount,Decimal(-15))

    def test_liability_deduction_handles_explicit_deferred_finance_spelling(self):
        target=TargetField("期末","D360","减：未确认的融资费用","ending",statement="balance_sheet",presentation_direction="借")
        source=LedgerRow("280102","未确认的融资费用",direction="贷",ending=Decimal(9))
        self.assertEqual(matching.resolve_match(target,[source]).amount,Decimal(-9))

    def test_invalid_statement_and_presentation_are_rejected_before_writing(self):
        row=LedgerRow("1521","投资性房地产",ending=Decimal(100))
        for field in (replace(self.target(),statement="unknown"),replace(self.target(),presentation_direction="unknown"),
                      replace(self.target(),component="unknown")):
            self.assertEqual(matching.resolve_match(field,[row]).status,"invalid")

    def test_property_cost_and_valuation_are_separate_from_carrying_value_and_profit(self):
        carrying=LedgerRow("1529","投资性房地产（公允计价）",ending=Decimal(90))
        cost=LedgerRow("152901","投资性房地产-成本",path="投资性房地产（公允计价）/投资性房地产-成本",ending=Decimal(100),level=2)
        value_change=LedgerRow("152902","投资性房地产-公允价值变动",path="投资性房地产（公允计价）/投资性房地产-公允价值变动",
                              ending=Decimal(10),direction="贷",level=2)
        profit=LedgerRow("610103","投资性房地产",path="公允价值变动损益/投资性房地产",credit=Decimal(0),is_profit_loss=True)
        targets=[self.target(component="cost"),replace(self.target(),cell="D182",label="投资性房地产公允价值变动",component="fair_value_change")]
        results=matching.match_fields(targets,[carrying,cost,value_change,profit])
        self.assertEqual([(r.status,r.amount,r.source_ids) for r in results],
                         [("matched",Decimal(100),["152901"]),("matched",Decimal(-10),["152902"])])
        self.assertEqual(matching.resolve_match(targets[0],[carrying]).status,"missing")
        self.assertEqual(matching.resolve_match(targets[0],[replace(carrying,name="投资性房地产")]).status,"missing")
        self.assertEqual(matching.resolve_match(self.target(),[carrying]).amount,Decimal(90))


if __name__ == "__main__":
    unittest.main()
