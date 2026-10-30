"""Synthetic layout changes must preserve TB scope and financial meaning."""
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

from tb_autofill import fields, matching, workbook
from tb_autofill.models import LedgerRow


class TemplateTransferTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'synthetic.xlsx'

    def make_book(self, title='试算平衡表(期末)', period='ending', split=False,
                  offset=(0, 0), duplicate=False, caption=None, extra_header=None):
        book = Workbook()
        sheet = book.active
        sheet.title = title
        dr, dc = offset
        def put(row, col, value):
            sheet.cell(row + dr, col + dc, value)
        put(1, 1, caption if caption is not None else '试算平衡表（期末数）' if period == 'ending' else '试算平衡表（期初数）')
        put(2, 1, '金额单位：元')
        sheet.merge_cells(start_row=6+dr, start_column=1+dc, end_row=7+dr, end_column=2+dc)
        put(6, 1, '报表项目')
        put(6, 3, '索引号')
        wanted = '期末未审数' if period == 'ending' else '上期审定数'
        put(6, 4, ('期末' if period == 'ending' else '上期') if split else wanted)
        if split:
            sheet.merge_cells(start_row=6+dr, start_column=4+dc, end_row=6+dr, end_column=5+dc)
            put(7, 4, '未审数' if period == 'ending' else '审定数')
            put(7, 5, '审定数' if period == 'ending' else '账面数')
        else:
            sheet.merge_cells(start_row=6+dr, start_column=4+dc, end_row=7+dr, end_column=4+dc)
            put(6, 5, '期末审定数')
        put(6, 6, '本期审计调整')
        put(7, 6, '借方')
        if duplicate:
            put(6, 7, wanted)
        if extra_header:
            put(7, 4, extra_header) if split else None
        put(8, 1, '流动资产：')
        put(9, 1, '货币资金')
        col = get_column_letter(4+dc)
        put(9, 4, f'=SUM({col}{10+dr}:{col}{11+dr})')
        put(10, 1, '其中：')
        put(10, 2, '现 金（库存现金）')
        put(11, 2, '银行存款')
        put(12, 1, '流动负债：')
        put(13, 1, '专项应付款')
        aux = book.create_sheet('余额辅助页')
        aux.append(['科目名称', '期末余额'])
        aux.append(['银行存款', None])
        aux['D1'] = '金额单位：万元'
        book.save(self.path)
        book.close()
        return {f'{col}{r+dr}' for r in (9, 10, 11, 13)}

    @staticmethod
    def rows():
        return [LedgerRow('C', '库存现金', ending=Decimal(0), opening=Decimal(7)),
                LedgerRow('B', '银行存款', ending=Decimal(25), opening=Decimal(9)),
                LedgerRow('L', '专项应付款', ending=Decimal(3), direction='借')]

    def test_layout_variations_keep_target_scope_amounts_and_directions(self):
        for title in ('试算平衡表(期末)', '2025年试算平衡表-期末', 'TB期末', '自定义页'):
            for offset in ((0, 0), (37, 3), (101, 8)):
                for split in (False, True):
                    with self.subTest(title=title, offset=offset, split=split):
                        expected = self.make_book(title, split=split, offset=offset)
                        digest = fields.file_digest(self.path)
                        scan = fields.scan_template(self.path, ('opening', 'ending'))
                        self.assertEqual(scan.unit, '元')
                        self.assertEqual({f.cell for f in scan.fields}, expected)
                        self.assertEqual({f.sheet for f in scan.fields}, {title})
                        self.assertTrue(all((f.period, f.basis, f.statement) == ('ending', 'book', 'balance_sheet') for f in scan.fields))
                        results = matching.match_fields(scan.fields, self.rows())
                        actual = {matching._canonical(r.target.label): (r.status, r.amount) for r in results}
                        self.assertEqual(actual, {'货币资金': ('protected', None), '库存现金': ('matched', Decimal(0)),
                                                 '银行存款': ('matched', Decimal(25)), '专项应付款': ('matched', Decimal(-3))})
                        writes = workbook.planned_writes(scan, results, rows=self.rows())
                        self.assertEqual(len(writes), 2)   # 库存现金为 0，默认不写入空白格
                        self.assertEqual(fields.file_digest(self.path), digest)

    def test_renamed_split_opening_still_requires_audited_source(self):
        self.make_book('TB期初', period='opening', split=True, offset=(23, 4))
        scan = fields.scan_template(self.path, ('opening', 'ending'))
        self.assertEqual({(f.period, f.basis) for f in scan.fields}, {('opening', 'audited')})
        results = matching.match_fields(scan.fields, self.rows())
        self.assertTrue(all(r.status == 'basis_mismatch' for r in results if not r.target.is_formula))
        self.assertFalse(any(r.selected for r in results))

    def test_renamed_duplicate_target_columns_are_rejected(self):
        for split in (False, True):
            self.make_book('TB期末', split=split, duplicate=True)
            scan = fields.scan_template(self.path)
            self.assertEqual(scan.fields, [])
            self.assertTrue(any('多列目标' in w for w in scan.warnings))

    def test_mixed_period_or_adjustment_fragments_are_rejected(self):
        for fragment in ('期初未审数', '期末未审数', '审计调整未审数'):
            self.make_book('TB期末', split=True, extra_header=fragment)
            self.assertEqual(fields.scan_template(self.path).fields, [])

    def test_consolidation_rejection_survives_renaming(self):
        for filename, title, caption in (
            ('synthetic(合并).xlsx', 'TB期末', None),
            ('合并试算平衡表.xlsx', '自定义页', None),
            ('synthetic.xlsx', '试算平衡表(期末)', '合并资产负债表试算平衡表（期末）'),
            ('synthetic.xlsx', '合并试算平衡表(期末)', None),
            ('synthetic.xlsx', '抵消试算平衡表(期末)', None),
            ('synthetic.xlsx', 'TB期末(合并)', None),
            ('synthetic.xlsx', 'TB期末(抵消)', None),
            ('synthetic.xlsx', '合并汇总', None),
        ):
            with self.subTest(filename=filename, title=title):
                self.path = Path(self.folder.name) / filename
                self.make_book(title, caption=caption)
                scan = fields.scan_template(self.path)
                self.assertEqual(scan.fields, [])
                self.assertTrue(any('合并' in w or '抵消' in w for w in scan.warnings))

    def test_conflicting_tb_period_cannot_fall_back_to_auxiliary_sheets(self):
        self.make_book('试算平衡表(期末)', caption='试算平衡表（期初）')
        scan = fields.scan_template(self.path, ('opening', 'ending'))
        self.assertEqual(scan.fields, [])
        self.assertTrue(any('冲突' in w for w in scan.warnings))

    def test_short_tb_consolidation_without_body_caption_is_blocked(self):
        for title in ('TB期末(合并)', 'TB期末(抵消)'):
            with self.subTest(title=title):
                self.make_book(title, caption='普通标题')
                scan = fields.scan_template(self.path, unit='元')
                self.assertEqual(scan.fields, [])
                self.assertTrue(any('合并' in w or '抵消' in w for w in scan.warnings))

    def test_unsupported_audit_headers_do_not_fall_back_to_auxiliary_sheets(self):
        for top, bottom in (('期末', '期初未审数'), ('期初', '未审数'), ('期初未审数', '')):
            with self.subTest(top=top, bottom=bottom):
                book = Workbook()
                sheet = book.active
                sheet.title = '试算平衡表'
                sheet.append(['报表项目', top])
                if bottom:
                    sheet.append([None, bottom])
                sheet.append(['银行存款', None])
                aux = book.create_sheet('辅助页')
                aux.append(['科目名称', '期末余额'])
                aux.append(['银行存款', None])
                book.save(self.path)
                book.close()
                scan = fields.scan_template(self.path, ('opening', 'ending'), unit='元')
                self.assertEqual(scan.fields, [])
                self.assertTrue(scan.warnings)

    def test_oversized_tb_does_not_fall_back_to_auxiliary_sheets(self):
        # 极限行数按实际内容丢弃整页；远处零散格式只收窄扫描范围，不丢弃已确认的TB页。
        cases = [((title, caption), address, expectation)
                 for title, caption in (('试算平衡表(期末)', None), ('自定义页', '试算平衡表(期末)'))
                 for address, expectation in (('A50001', 'dropped'), ('SG1', 'narrowed'))]
        for (title, caption), address, expectation in cases:
            with self.subTest(title=title, address=address):
                book = Workbook()
                sheet = book.active
                sheet.title = title
                if caption:
                    sheet.append([caption])
                sheet.append(['报表项目', '期末未审数'])
                sheet.append(['银行存款', None])
                sheet[address] = '备注'
                aux = book.create_sheet('辅助页')
                aux.append(['科目名称', '期末余额'])
                aux.append(['银行存款', None])
                book.save(self.path)
                book.close()
                scan = fields.scan_template(self.path, unit='元')
                self.assertNotIn('辅助页', {f.sheet for f in scan.fields})
                if expectation == 'dropped':
                    self.assertEqual(scan.fields, [])
                    self.assertTrue(any('范围' in w for w in scan.warnings))
                else:
                    self.assertEqual({f.sheet for f in scan.fields}, {title})
                    self.assertTrue(all(f.cell in {'B2', 'B3'} for f in scan.fields))

    def test_tb_without_period_caption_uses_only_explicit_target_headers(self):
        for split in (False, True):
            self.make_book('试算平衡表', split=split, caption='试算平衡表')
            scan = fields.scan_template(self.path)
            self.assertEqual({f.sheet for f in scan.fields}, {'试算平衡表'})
            self.assertEqual(len(scan.fields), 4)
            self.assertTrue(all(f.statement == 'balance_sheet' for f in scan.fields))

    def test_plain_balance_table_keeps_generic_scan(self):
        book = Workbook()
        sheet = book.active
        sheet.title = '普通余额表'
        sheet.append(['科目名称', '期末余额'])
        sheet.append(['银行存款', None])
        book.save(self.path)
        book.close()
        scan = fields.scan_template(self.path, unit='元')
        self.assertEqual([(f.label, f.period, f.statement) for f in scan.fields], [('银行存款', 'ending', '')])

    def test_offset_unit_is_detected_and_conflicts_are_rejected(self):
        for dr, dc in ((101, 8), (0, 45)):
            with self.subTest(offset=(dr, dc)):
                self.make_book(offset=(dr, dc))
                book = load_workbook(self.path)
                sheet = book['试算平衡表(期末)']
                sheet.cell(2 + dr, 1 + dc, '金额单位：万元')
                book.save(self.path)
                book.close()
                scan = fields.scan_template(self.path)
                self.assertEqual(scan.unit, '万元')
                bank = next(f for f in scan.fields if matching._canonical(f.label) == '银行存款')
                result = matching.match_fields([bank], [LedgerRow('B', '银行存款', ending=Decimal(25000))])[0]
                self.assertEqual(result.amount, Decimal('2.5'))
        self.make_book(offset=(101, 8))
        book = load_workbook(self.path)
        book['试算平衡表(期末)']['I103'] = '金额单位：万元'
        book['试算平衡表(期末)']['J104'] = '金额单位：元'
        book.save(self.path)
        book.close()
        with self.assertRaises(ValueError):
            fields.scan_template(self.path)


if __name__ == '__main__':
    unittest.main()
