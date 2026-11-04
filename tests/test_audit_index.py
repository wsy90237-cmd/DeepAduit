"""底稿索引映射与 Excel 底稿导出。"""

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill

from tb_autofill import audit_index, papers
from tb_autofill.models import LedgerRow

YELLOW = PatternFill("solid", fgColor="FFFF00")


def make_list(path, rows):
    """按「索引号一览」的表头造一份清单，rows 为 (索引号, 名称, 是否标黄)。"""
    book = Workbook()
    sheet = book.active
    sheet.title = audit_index.SHEET
    sheet.append(["序号", "业务类型", "底稿类型", "索引号", "底稿名称", "模板文件数", "模板文件名"])
    for number, (index_no, name, marked) in enumerate(rows, 1):
        sheet.append([number, "财务报表审计", "实质性底稿", index_no, name, 1, f"{index_no}.xlsx"])
        if marked:
            for column in range(1, 8):
                sheet.cell(number + 1, column).fill = YELLOW
    book.save(path)
    return path


class BundledIndexTests(unittest.TestCase):
    def test_bundled_snapshot_is_available(self):
        self.assertGreater(len(audit_index.bundled()), 60)

    def test_highlighted_rows_are_not_report_papers(self):
        """标黄行不属于财务报表审计报表科目。"""
        known = {paper.index_no for paper in audit_index.bundled()}
        for index_no in ("1-011", "6-010", "7-010"):
            self.assertNotIn(index_no, known)

    def test_no_duplicate_index_numbers(self):
        numbers = [paper.index_no for paper in audit_index.bundled()]
        self.assertEqual(len(numbers), len(set(numbers)))


class ResolveTests(unittest.TestCase):
    def check(self, code, name, expected):
        paper, basis = audit_index.resolve(code, name)
        self.assertIsNotNone(paper, basis)
        self.assertEqual(paper.index_no, expected)

    def test_code_mapping_covers_statements(self):
        self.check("1002", "银行存款", "1-010")
        self.check("1122", "应收账款", "1-070")
        self.check("1601", "固定资产", "1-280")
        self.check("2202", "应付账款", "2-070")
        self.check("4001", "实收资本", "3-010")
        self.check("6001", "主营业务收入", "4-010")
        self.check("6602", "管理费用", "5-110")
        self.check("6801", "所得税费用", "5-240")

    def test_short_codes_fall_back_to_name(self):
        paper, basis = audit_index.resolve("102", "银行存款")
        self.assertEqual(paper.index_no, "1-010")
        self.assertIn("名称", basis)

    def test_unknown_account_is_not_guessed(self):
        paper, basis = audit_index.resolve("8888", "往来款")
        self.assertIsNone(paper)
        self.assertIn("人工确认", basis)

    def test_every_code_target_exists_in_the_list(self):
        known = {paper.index_no for paper in audit_index.bundled()}
        self.assertEqual(set(audit_index.match_papers().values()) - known, set())


class LoadTests(unittest.TestCase):
    def test_yellow_rows_are_skipped_when_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            path = make_list(Path(folder) / "清单.xlsx",
                             [("1-010", "货币资金", False), ("1-011", "银行询证函", True),
                              ("1-070", "应收账款", False)])
            loaded = audit_index.load(path)
        self.assertEqual([paper.index_no for paper in loaded], ["1-010", "1-070"])

    def test_other_business_types_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = make_list(Path(folder) / "清单.xlsx", [("1-010", "货币资金", False)])
            book = load_workbook(path)
            book[audit_index.SHEET].append([9, "专项审计", "实质性底稿", "0-010", "审计资料清单", 1, "x.xlsx"])
            book.save(path)
            loaded = audit_index.load(path)
        self.assertEqual([paper.index_no for paper in loaded], ["1-010"])

    def test_missing_list_reports_readable_error(self):
        with self.assertRaises(audit_index.IndexError_):
            audit_index.load(Path(tempfile.gettempdir()) / "不存在的清单.xlsx")


def sample_draft(**extra):
    value = {
        "kind": "audit", "kind_label": "科目审定表",
        "subject": {"code": "1002", "name": "银行存款", "direction": "借", "level": 1,
                    "level_label": "一级科目", "opening": "1,234,567.89", "debit": "2,000.00",
                    "credit": "1,000.00", "ending": "1,235,567.89",
                    "children": [{"code": "100201", "name": "工行基本户", "opening": "1,000,000.00",
                                  "debit": "0.00", "credit": "500.00", "ending": "999,500.00"}]},
        "body": "一、审计目标\n核对余额与对账单。\n- 取得对账单\n结论：待补函证。",
        "refs": [{"citation_id": "K1", "title": "银行存款审计程序", "source": "china-audit"}],
        "paper": {"index_no": "1-010", "name": "货币资金"},
        "has_journal": True,
        "journal": {"count": 2, "debit": "2,000.00", "credit": "1,000.00",
                    "tie": {"checked": True, "debit_matches": True, "credit_matches": True}},
    }
    value.update(extra)
    return value


class ExcelExportTests(unittest.TestCase):
    def export(self, draft=None, **kwargs):
        folder = tempfile.mkdtemp()
        path = Path(folder) / "底稿.xlsx"
        papers.export_xlsx(path, draft or sample_draft(), subject_label="南平市万和房地产有限公司 / 2025-12",
                           prepared_by=kwargs.get("prepared_by", "张三"),
                           reviewed_by=kwargs.get("reviewed_by", "李四"),
                           export_time="2026-11-04 11:40")
        return load_workbook(path).active

    def test_header_carries_index_and_signers(self):
        sheet = self.export()
        values = [str(cell.value) for row in sheet.iter_rows(min_row=1, max_row=6) for cell in row if cell.value]
        self.assertIn("南平市万和房地产有限公司 / 2025-12", values)
        self.assertIn("1-010", values)
        self.assertIn("货币资金", values)
        self.assertIn("张三", values)
        self.assertIn("李四", values)
        self.assertIn("编制人", values)
        self.assertIn("复核人", values)

    def test_amounts_are_numbers_not_text(self):
        sheet = self.export()
        self.assertEqual(sheet.cell(papers.XLSX_TABLE_HEADER + 1, 3).value, 1234567.89)
        self.assertEqual(sheet.cell(papers.XLSX_TABLE_HEADER + 1, 6).value, 1235567.89)

    def test_children_and_journal_are_written(self):
        sheet = self.export()
        text = "\n".join(str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value)
        self.assertIn("工行基本户", text)
        self.assertIn("序时账摘要：2 笔", text)
        self.assertIn("勾稽一致", text)
        self.assertIn("待补函证", text)

    def test_notice_and_signature_block_present(self):
        sheet = self.export()
        text = "\n".join(str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value)
        self.assertIn("未经注册会计师复核", text)
        self.assertIn("编制人：张三", text)

    def test_missing_signer_leaves_blank_line(self):
        sheet = self.export(prepared_by="", reviewed_by="")
        text = "\n".join(str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value)
        self.assertIn("编制人：________", text)

    def test_draft_without_amounts_keeps_amount_area_empty(self):
        draft = sample_draft(paper={}, has_journal=False, journal=None)
        draft["subject"] = {"code": "—", "name": "审计报告草稿", "direction": "", "opening": "",
                            "debit": "", "credit": "", "ending": ""}
        sheet = self.export(draft)
        text = "\n".join(str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value)
        self.assertIn("金额栏留空待复核", text)

    def test_text_body_is_never_dropped(self):
        rows = papers._body_rows("# 标题\n- 列表\n（一）小节\n普通段落")
        self.assertEqual([level for level, _text in rows], [1, -1, 2, 0])


class DraftPaperFieldTests(unittest.TestCase):
    def test_draft_records_paper_for_export(self):
        class Client:
            def converse_with_skills(self, *args, **kwargs):
                return {"intent": "audit", "answer": "结论：待补", "warnings": []}

        row = LedgerRow(code="1002", name="银行存款", path="银行存款", level=1, direction="借",
                        opening=Decimal("100"), ending=Decimal("120"),
                        debit=Decimal("20"), credit=Decimal("0"), subject_id="S1", year=2026, month=12)
        paper, _basis = audit_index.resolve("1002", "银行存款")
        result = papers.draft(Client(), rows=[row], code="1002", paper=paper)
        self.assertEqual(result["paper"]["index_no"], "1-010")
        self.assertEqual(result["paper"]["name"], "货币资金")
