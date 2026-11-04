"""资料需求清单、基础说明与整套生成。"""

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from tb_autofill import audit_index, dxn_paper, paper_materials, paper_pack
from tb_autofill.models import LedgerRow


def ledger():
    def row(code, name, path, level, direction, opening, debit, credit, ending, pl=False):
        return LedgerRow(code=code, name=name, path=path, level=level, direction=direction,
                         opening=Decimal(opening), debit=Decimal(debit), credit=Decimal(credit),
                         ending=Decimal(ending), subject_id="S", year=2025, month=12,
                         is_profit_loss=pl)

    return [
        row("1001", "库存现金", "库存现金", 1, "借", "5000", "120000", "118000", "7000"),
        row("1002", "银行存款", "银行存款", 1, "借", "100000", "2000.50", "500.25", "101500.25"),
        row("100201", "合成银行000001", "银行存款/合成银行000001", 2, "借",
            "100000", "2000.50", "500.25", "101500.25"),
        row("1122", "应收账款", "应收账款", 1, "借", "1200000", "4800000", "4500000", "1500000"),
        row("6001", "主营业务收入", "主营业务收入", 1, "贷", "0", "8000000", "8000000", "0", True),
    ]


def resolve(code, name):
    return audit_index.resolve(code, name)[0]


class MaterialTests(unittest.TestCase):
    def test_cash_requires_bank_statements(self):
        items = paper_materials.for_paper("1-010", "货币资金")
        names = [item["name"] for item in items]
        self.assertIn("银行对账单（全期间）", names)
        self.assertIn("科目余额表（鼎信诺账套）", names)

    def test_ledger_material_is_marked_ready(self):
        items = paper_materials.for_paper("1-010", "货币资金")
        ready = {item["name"]: item["ready"] for item in items}
        self.assertTrue(ready["科目余额表（鼎信诺账套）"])
        self.assertFalse(ready["银行对账单（全期间）"])

    def test_journal_adds_a_ready_row(self):
        with_journal = paper_materials.for_paper("1-010", "货币资金", has_journal=True)
        names = [item["name"] for item in with_journal]
        self.assertIn("序时账（凭证分录）", names)

    def test_pending_lists_only_missing(self):
        items = paper_materials.for_paper("1-070", "应收账款")
        pending = paper_materials.pending(items)
        self.assertNotIn("科目余额表（鼎信诺账套）", pending)
        self.assertIn("函证回函及发函记录", pending)

    def test_categories_differ(self):
        cash = {item["name"] for item in paper_materials.for_paper("1-010", "货币资金")}
        stock = {item["name"] for item in paper_materials.for_paper("1-150", "存货")}
        self.assertTrue(cash - stock)
        self.assertTrue(stock - cash)


class BaselineTextTests(unittest.TestCase):
    def test_balance_sheet_notes_use_amounts(self):
        lines = paper_pack.baseline_notes(name="货币资金", accounts=[1, 2],
                                          total={"opening": Decimal("330000"),
                                                 "debit": Decimal("415930.40"),
                                                 "credit": Decimal("121843.58"),
                                                 "ending": Decimal("624086.82"),
                                                 "period": Decimal("0")},
                                          is_pl=False, consistent=True, gap=Decimal("0"),
                                          pending=["银行对账单（全期间）"])
        joined = "\n".join(lines)
        self.assertIn("624,086.82", joined)
        self.assertIn("逐栏核对一致", joined)
        self.assertIn("尚需取得 1 项资料", joined)

    def test_profit_loss_notes_use_period_amount(self):
        lines = paper_pack.baseline_notes(name="营业收入", accounts=[1],
                                          total={"opening": Decimal("0"), "debit": Decimal("8000000"),
                                                 "credit": Decimal("8000000"),
                                                 "ending": Decimal("0"), "period": Decimal("8000000")},
                                          is_pl=True, consistent=True, gap=Decimal("0"))
        joined = "\n".join(lines)
        self.assertIn("本期发生额 8,000,000.00", joined)
        self.assertIn("上年账套", joined)

    def test_conclusion_names_missing_evidence(self):
        text = paper_pack.default_conclusion(True, ["银行对账单（全期间）", "银行询证函回函"])
        self.assertIn("银行对账单（全期间）", text)
        self.assertIn("暂不支持无异常结论", text)


class PackIntegrationTests(unittest.TestCase):
    def build(self, builder):
        folder = tempfile.mkdtemp()
        path = Path(folder) / "底稿.xlsx"
        paper = audit_index.resolve("1002", "银行存款")[0]
        if builder == "dxn":
            dxn_paper.build(path, paper=paper, subject_label="合成企业 / 2025-12", rows=ledger(),
                            resolve=resolve, prepared_by="张三", reviewed_by="李四",
                            when="2026-11-04",
                            procedures=paper_pack.procedures(paper.index_no, paper.name))
        else:
            paper_pack.build(path, paper=paper, subject_label="合成企业 / 2025-12",
                             subject_row=ledger()[1], rows=ledger(), prepared_by="张三",
                             reviewed_by="李四", when="2026-11-04")
        return load_workbook(path)

    def test_dxn_pack_has_no_materials_sheet(self):
        """资料清单已按需求去掉，只保留导引/审定的说明与待补提示。"""
        book = self.build("dxn")
        self.assertNotIn("资料需求清单", book.sheetnames)

    def test_4100_pack_has_materials_sheet(self):
        names = self.build("4100").sheetnames
        self.assertTrue(any("资料需求清单" in name for name in names), names)

    def test_default_notes_written_without_model(self):
        book = self.build("dxn")
        sheet = book["1-010 审定表"]
        text = "\n".join(str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value)
        self.assertIn("取自本机账套科目余额表", text)
        self.assertIn("审计结论", text)
        self.assertAssetNotPending(text)

    def assertAssetNotPending(self, text):
        self.assertNotIn("待补：未生成文字说明", text)


class BuildAllTests(unittest.TestCase):
    def test_batch_outputs_files_and_summary(self):
        folder = tempfile.mkdtemp()
        result = dxn_paper.build_all(
            folder, subject_label="合成企业 / 2025-12", rows=ledger(), resolve=resolve,
            papers=audit_index.bundled(), prepared_by="张三", reviewed_by="李四",
            when="2026-11-04",
            procedures_for=lambda index_no, name: paper_pack.procedures(index_no, name))
        self.assertGreaterEqual(result["papers"], 3)
        self.assertGreaterEqual(result["pending"], 10)
        files = list(Path(folder).glob("*.xlsx"))
        self.assertEqual(len(files), result["papers"] + 1)      # 含汇总
        summary = load_workbook(result["summary"])
        self.assertEqual(summary.sheetnames, ["底稿总目录", "待补资料汇总", "使用说明"])

    def test_summary_lists_every_paper(self):
        folder = tempfile.mkdtemp()
        result = dxn_paper.build_all(
            folder, subject_label="合成企业 / 2025-12", rows=ledger(), resolve=resolve,
            papers=audit_index.bundled(), when="2026-11-04")
        catalogue = load_workbook(result["summary"])["底稿总目录"]
        text = "\n".join(str(cell.value) for row in catalogue.iter_rows() for cell in row if cell.value)
        self.assertIn("1-010", text)
        self.assertIn("1-070", text)
        self.assertIn(f"{result['papers']} 册", text)

    def test_empty_subject_is_skipped(self):
        folder = tempfile.mkdtemp()
        result = dxn_paper.build_all(folder, subject_label="合成企业 / 2025-12", rows=ledger(),
                                     resolve=resolve, papers=audit_index.bundled(),
                                     when="2026-11-04")
        self.assertNotIn("1-280 固定资产.xlsx", result["files"])
