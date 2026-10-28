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


if __name__ == "__main__":
    unittest.main()
