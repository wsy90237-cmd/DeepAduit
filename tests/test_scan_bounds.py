"""扫描范围契约：按实际内容限定边界，超大辅助页不参与解析。"""
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from tb_autofill import fields


def tb_page(book, title='试算平衡表(期末)'):
    sheet = book.create_sheet(title)
    sheet.append(['试算平衡表(期末数)'])
    sheet.append(['报表项目', '期末未审数', '上期审定数'])
    for label, amount in (('货币资金', 1200), ('应收账款', 3400), ('固定资产', 5600)):
        sheet.append([label, amount, amount - 100])
    return sheet


def heavy_page(book, title):
    """数字密集的辅助账页，工作表XML体积超过解析阈值。"""
    sheet = book.create_sheet(title)
    for row in range(1, 9000):
        for col in range(1, 11):
            sheet.cell(row, col, row * col)
    return sheet


class ScanBoundsTests(unittest.TestCase):
    def test_unsupported_metadata_formula_does_not_traverse_financial_references(self):
        from types import SimpleNamespace
        class NoReferences:
            @property
            def sheetnames(self):
                raise AssertionError("Unsupported formulas must not inspect dependency sheets")
        sheet = SimpleNamespace(title="Synthetic")
        for formula in ('=IF(A1>0,"金额单位：万元",A2)', '=ROUND(A1,2)', '=SUM(A1,B1)'):
            with self.subTest(formula=formula):
                result = fields._unit_text(NoReferences(), sheet, formula)
                self.assertIsInstance(result, fields._UnresolvedText)
        self.assertIn("金额单位", fields._unit_text(NoReferences(), sheet, '=IF(A1>0,"金额单位：万元",A2)').hint)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'bounds.xlsx'

    def build(self, filler):
        book = Workbook()
        book.remove(book.active)
        tb_page(book)
        filler(book)
        book.save(self.path)
        book.close()
        return self.path

    def test_declared_extent_from_formatting_is_not_auxiliary(self):
        def stray(book):
            sheet = book.create_sheet('辅助-说明')
            sheet.append(['说明'])
            far = sheet.cell(4, 900)
            far.number_format = '@'
        from openpyxl import load_workbook
        book = load_workbook(self.build(stray))
        try:
            sheet = book['辅助-说明']
            self.assertGreaterEqual(sheet.max_column, 900)
            _, _, auxiliary = fields._scan_bounds(sheet)
            self.assertFalse(auxiliary, '仅格式膨胀的表页不应判为辅助账页')
        finally:
            book.close()

    def test_populated_auxiliary_page_is_excluded_from_scan(self):
        def dense(book):
            sheet = book.create_sheet('辅助-往来明细')
            for row in range(1, fields.AUXILIARY_ROW_LIMIT + 2):
                sheet.cell(row, 1, row)
        from openpyxl import load_workbook
        book = load_workbook(self.build(dense))
        try:
            sheet = book['辅助-往来明细']
            self.assertTrue(fields._scan_bounds(sheet)[2])
        finally:
            book.close()

    def test_heavy_auxiliary_part_is_projected_out(self):
        def heavy(book):
            heavy_page(book, '辅助-原始数据')
        path = self.build(heavy)
        before = fields.file_digest(path)
        drops = fields._heavy_auxiliary_parts(path)
        self.assertEqual(list(drops), ['辅助-原始数据'])
        scan = fields.scan_template(path, ('opening', 'ending'), unit='元')
        self.assertEqual(len(scan.fields), 3)
        self.assertEqual({field.sheet for field in scan.fields}, {'试算平衡表(期末)'})
        self.assertTrue(any('超大辅助页' in warning for warning in scan.warnings))
        self.assertEqual(fields.file_digest(path), before, '投影不得修改原文件')

    def test_tb_titled_page_is_never_projected(self):
        def heavy(book):
            sheet = tb_page(book, '试算平衡表(期初)')
            sheet.cell(40, 1, 'y' * (fields.HEAVY_SHEET_BYTES + 1024))
        path = self.build(heavy)
        drops = fields._heavy_auxiliary_parts(path)
        self.assertEqual(drops, {}, '试算平衡表页不能因体积被排除在扫描之外')

    def test_fallback_rescans_original_when_no_tb_page(self):
        book = Workbook()
        book.remove(book.active)
        book.create_sheet('封面')
        plain = book.create_sheet('余额表')
        plain.append(['科目名称', '期末余额'])
        plain.append(['银行存款', None])
        heavy_page(book, '辅助-原始数据')
        book.save(self.path)
        book.close()
        scan = fields.scan_template(self.path, ('opening', 'ending'), unit='元')
        self.assertIn('余额表', {field.sheet for field in scan.fields}, '未确认TB页时必须回到原文件扫描')
        self.assertTrue(any('超大辅助页' in warning for warning in scan.warnings))


if __name__ == '__main__':
    unittest.main()
