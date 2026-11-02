"""A complete other-income account cannot be replaced by a same-name detail."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tb_autofill import fields, matching, workbook
from tb_autofill.models import LedgerRow, MatchResult, TargetField


def rows():
    return [LedgerRow('R', '其他收益', path='其他收益', direction='贷', is_profit_loss=True,
                      credit=Decimal(120), ending=Decimal(0)),
            LedgerRow('RA', '其他收益', path='其他收益/其他收益', direction='贷', is_profit_loss=True,
                      level=2, credit=Decimal(40), ending=Decimal(0)),
            LedgerRow('RB', '政府补助', path='其他收益/政府补助', direction='贷', is_profit_loss=True,
                      level=2, credit=Decimal(80), ending=Decimal(0))]


def target(**kwargs):
    return replace(TargetField('TB期末', 'F25', '加：（一）其他收益', 'ending',
                               measure='profit_loss', statement='profit_loss'), **kwargs)


class OtherIncomeTotalTests(unittest.TestCase):
    def test_unique_top_level_account_is_used_once(self):
        result = matching.match_fields([target()], rows())[0]
        self.assertEqual((result.status, result.source_ids, result.amount), ('matched', ['R'], Decimal(120)))

    def test_zero_missing_and_reverse_values_are_kept_separate(self):
        for value, status, selected in ((None, 'missing', False), (Decimal(0), 'matched', False),
                                        (Decimal(-9), 'matched', True)):
            with self.subTest(value=value):
                source = [replace(rows()[0], credit=value), *rows()[1:]]
                result = matching.match_fields([target()], source)[0]
                self.assertEqual((result.status, result.amount, result.selected), (status, value, selected))

    def test_missing_duplicate_or_unrelated_parent_does_not_choose_a_detail(self):
        for source in (rows()[1:], [*rows(), replace(rows()[0], code='X')],
                       [*rows(), replace(rows()[1], code='XA', path='其他业务收入/其他收益')]):
            with self.subTest(codes=[r.code for r in source]):
                result = matching.match_fields([target()], source)[0]
                self.assertFalse(result.selected)
                self.assertIsNone(result.amount)

    def test_shared_resolver_and_write_guard_reject_a_child_as_total(self):
        for method in ('manual', 'ai', 'ai-review', 'exact'):
            with self.subTest(method=method):
                result = matching.resolve_match(target(), [rows()[1]], method, rows=rows())
                self.assertFalse(result.selected)
                self.assertIsNone(result.amount)

    def test_formula_and_audited_opening_keep_their_guards(self):
        formula = target(value='=1+1', is_formula=True)
        self.assertEqual(matching.match_fields([formula], rows())[0].status, 'protected')
        result = matching.match_fields([target(period='opening', basis='audited')], rows())[0]
        self.assertFalse(result.selected)
        self.assertIsNone(result.amount)
        self.assertNotEqual(matching.match_fields([target()], [replace(rows()[0], is_profit_loss=False)])[0].status, 'matched')

    def test_context_and_amounts_cannot_choose_a_child(self):
        filled = target(value=40)
        result = matching.match_fields([filled], rows())[0]
        self.assertEqual((result.source_ids, result.amount, result.selected), (['R'], Decimal(120), False))
        nested = target(label='其他收益', parent='营业外收入')
        self.assertFalse(matching.match_fields([nested], rows())[0].selected)

    def test_layout_changes_keep_total_scope_and_protect_adjustments(self):
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
        with TemporaryDirectory(dir='.') as folder:
            path = Path(folder)/'synthetic.xlsx'
            for title in ('试算平衡表(期末)', '自定义页'):
                for dr, dc in ((0, 0), (43, 5)):
                    for split in (False, True):
                        for unit, factor in (('元', Decimal(1)), ('万元', Decimal(10000))):
                            with self.subTest(title=title, offset=(dr,dc), split=split, unit=unit):
                                book = Workbook()
                                sheet = book.active
                                sheet.title = title
                                def put(r,c,v): sheet.cell(r+dr,c+dc,v)
                                put(1,1,'利润表试算平衡表（期末）')
                                put(2,1,'金额单位：'+unit)
                                put(6,1,'报表项目')
                                put(6,4,'期末' if split else '期末未审数')
                                if split:
                                    sheet.merge_cells(start_row=6+dr,start_column=4+dc,end_row=6+dr,end_column=5+dc)
                                    put(7,4,'未审数')
                                    put(7,5,'审定数')
                                put(6,6,'本期审计调整')
                                put(8,1,'加：（一）其他收益')
                                put(8,6,999)
                                col=get_column_letter(4+dc)
                                put(9,1,'营业利润')
                                put(9,4,f'={col}{8+dr}')
                                book.save(path)
                                book.close()
                                scan=fields.scan_template(path,('opening','ending'))
                                results=matching.match_fields(scan.fields,rows())
                                chosen=[r for r in results if r.selected]
                                self.assertEqual([(r.source_ids,r.amount) for r in chosen],[(['R'],Decimal(120)/factor)])
                                writes=workbook.planned_writes(scan,results,rows=rows())
                                self.assertEqual(writes,[(chosen[0].target,Decimal(120)/factor)])
                                forged=MatchResult(chosen[0].target,[rows()[1]],Decimal(40)/factor,
                                                   status='matched',method='exact',selected=True)
                                with self.assertRaises(ValueError):workbook.planned_writes(scan,[forged],rows=rows())
                                self.assertTrue(any(r.status=='protected' for r in results))


if __name__=='__main__':unittest.main()
