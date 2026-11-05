"""Synthetic parent paths must separate period expenses from capitalized costs."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tb_autofill import matching
from tb_autofill.models import LedgerRow, TargetField


def sources():
    return [
        LedgerRow('C', '在建工程', path='在建工程'),
        LedgerRow('CF', '财务费用', path='在建工程/财务费用', level=2),
        LedgerRow('CFI', '利息支出', path='在建工程/财务费用/利息支出', level=3,
                  ending=Decimal(900), debit=Decimal(900)),
        LedgerRow('F', '财务费用', path='财务费用', is_profit_loss=True),
        LedgerRow('FI', '利息支出', path='财务费用/利息支出', level=2,
                  ending=Decimal(0), debit=Decimal(23), credit=Decimal(23), is_profit_loss=True),
    ]


def field(**kwargs):
    return replace(TargetField('TB期末', 'G31', '其中：财务费用-利息支出', 'ending',
                               parent='（四）财务费用', statement='profit_loss', measure='profit_loss'), **kwargs)


class ParentPathTests(unittest.TestCase):
    def test_full_parent_path_excludes_capitalized_namesakes(self):
        result = matching.match_fields([field()], sources())[0]
        self.assertEqual((result.status, result.source_ids, result.amount), ('matched', ['FI'], Decimal(23)))

    def test_explicit_nested_path_keeps_its_own_balance(self):
        target = field(label='利息支出', parent='在建工程/财务费用',
                       statement='balance_sheet', measure='balance', presentation_direction='借')
        result = matching.match_fields([target], sources())[0]
        self.assertEqual((result.status, result.source_ids, result.amount), ('matched', ['CFI'], Decimal(900)))

    def test_missing_period_expense_parent_cannot_use_capitalized_cost(self):
        result = matching.match_fields([field()], sources()[:3])[0]
        self.assertFalse(result.selected)
        self.assertIsNone(result.amount)

    def test_two_complete_parent_paths_remain_ambiguous(self):
        rows = sources() + [LedgerRow('F2', '财务费用', path='财务费用', is_profit_loss=True)]
        result = matching.match_fields([field()], rows)[0]
        self.assertEqual(result.status, 'ambiguous')
        self.assertFalse(result.selected)

    def test_duplicate_leaf_within_correct_parent_remains_ambiguous(self):
        rows = sources() + [replace(sources()[-1], code='FI2')]
        result = matching.match_fields([field()], rows)[0]
        self.assertEqual(result.status, 'ambiguous')
        self.assertFalse(result.selected)

    def test_shared_resolver_rejects_the_capitalized_source(self):
        for method in ('manual', 'ai', 'code', 'ai-review'):
            with self.subTest(method=method):
                result = matching.resolve_match(field(), [sources()[2]], method, rows=sources())
                self.assertFalse(result.selected)
                self.assertIsNone(result.amount)

    def test_missing_zero_and_reversed_occurrence_keep_their_meaning(self):
        for value, status, selected in ((None, 'missing', False), (Decimal(0), 'matched', False),
                                        (Decimal(-7), 'matched', True)):
            with self.subTest(value=value):
                rows = sources()[:-1] + [replace(sources()[-1], debit=value)]
                result = matching.match_fields([field()], rows)[0]
                self.assertEqual((result.status, result.selected, result.amount), (status, selected, value))

    def test_opening_audited_target_still_rejects_book_source(self):
        result = matching.match_fields([field(period='opening', basis='audited')], sources())[0]
        self.assertEqual(result.status, 'basis_mismatch')
        self.assertFalse(result.selected)

    def test_interest_income_sign_and_mixed_turnovers_are_preserved(self):
        target = field(label='财务费用-利息收入（以“+”值填列）', parent='')
        source = replace(sources()[-1], name='利息收入', path='财务费用/利息收入',
                         debit=Decimal(-13), credit=Decimal(0))
        rows = sources()[:-1] + [source]
        result = matching.match_fields([target], rows)[0]
        self.assertEqual((result.source_ids, result.amount), (['FI'], Decimal(13)))
        mixed = replace(source, debit=Decimal(9), credit=Decimal(13))
        result = matching.match_fields([target], rows[:-1]+[mixed])[0]
        self.assertEqual(result.status, 'missing')
        self.assertFalse(result.selected)

    def test_parent_path_survives_layout_header_and_unit_variations(self):
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
        from tb_autofill import fields, workbook
        with TemporaryDirectory() as folder:
            path = Path(folder)/'synthetic.xlsx'
            for title in ('试算平衡表(期末)', '自定义页'):
                for dr, dc in ((0, 0), (37, 3), (101, 8)):
                    for split in (False, True):
                        for unit, factor in (('元', Decimal(1)), ('万元', Decimal(10000))):
                            with self.subTest(title=title, offset=(dr,dc), split=split, unit=unit):
                                book = Workbook()
                                sheet = book.active
                                sheet.title = title
                                def put(r, c, value): sheet.cell(r+dr, c+dc, value)
                                put(1, 1, '利润表试算平衡表（期末）')
                                put(2, 1, '金额单位：'+unit)
                                put(6, 1, '报表项目')
                                put(6, 4, '期末' if split else '期末未审数')
                                if split:
                                    sheet.merge_cells(start_row=6+dr, start_column=4+dc,
                                                      end_row=6+dr, end_column=5+dc)
                                    put(7, 4, '未审数')
                                    put(7, 5, '审定数')
                                put(6, 6, '本期审计调整')
                                put(8, 1, '四、财务费用')
                                col = get_column_letter(4+dc)
                                put(8, 4, f'=SUM({col}{9+dr}:{col}{10+dr})')
                                put(9, 1, '其中：财务费用-利息支出')
                                put(9, 6, 999)
                                book.save(path)
                                book.close()
                                digest = fields.file_digest(path)
                                scan = fields.scan_template(path, ('opening', 'ending'))
                                results = matching.match_fields(scan.fields, sources())
                                selected = [r for r in results if r.selected]
                                self.assertEqual(len(selected), 1)
                                result = selected[0]
                                self.assertEqual((result.source_ids, result.amount, result.target.cell),
                                                 (['FI'], Decimal(23)/factor, f'{col}{9+dr}'))
                                self.assertEqual((result.target.period, result.target.basis), ('ending', 'book'))
                                self.assertEqual(workbook.planned_writes(scan, results, rows=sources()),
                                                 [(result.target, Decimal(23)/factor)])
                                self.assertTrue(any(r.status == 'protected' for r in results))
                                self.assertEqual(fields.file_digest(path), digest)


if __name__ == '__main__':
    unittest.main()
