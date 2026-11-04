"""鼎信诺式底稿：取数结构解析、审定表与明细表勾稽。"""

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook, load_workbook

from tb_autofill import audit_index, dxn_paper, paper_pack
from tb_autofill.models import LedgerRow

def ledger():
    def row(code, name, path, level, opening, debit, credit, ending):
        return LedgerRow(code=code, name=name, path=path, level=level, direction="借",
                         opening=Decimal(opening), debit=Decimal(debit), credit=Decimal(credit),
                         ending=Decimal(ending), subject_id="S1", year=2025, month=12)

    return [
        row("1001", "现金", "现金", 1, "0", "0", "0", "0"),
        row("1002", "银行存款", "银行存款", 1, "100000", "2000.50", "500.25", "101500.25"),
        row("1009", "其他货币资金", "其他货币资金", 1, "0", "0", "0", "0"),
        row("100201", "合成银行000001", "银行存款/合成银行000001", 2,
            "100000", "2000.50", "500.25", "101500.25"),
    ]


def resolve(code, name):
    return audit_index.resolve(code, name)[0]


def build(**kwargs):
    folder = tempfile.mkdtemp()
    path = Path(folder) / "鼎信诺式底稿.xlsx"
    paper = audit_index.resolve("1002", "银行存款")[0]
    dxn_paper.build(path, paper=paper, subject_label="合成企业 / 2025-12", rows=ledger(),
                    resolve=resolve, prepared_by="张三", reviewed_by="李四", when="2026-11-04",
                    procedures=paper_pack.procedures(paper.index_no, paper.name), **kwargs)
    return load_workbook(path)


def text_of(book):
    sheets = book.worksheets if hasattr(book, "worksheets") else (book,)
    return "\n".join(str(cell.value) for sheet in sheets
                     for row in sheet.iter_rows() for cell in row if cell.value is not None)


class CodeFormatTests(unittest.TestCase):
    def test_two_level_code(self):
        self.assertEqual(dxn_paper.dxn_code("100201", 2), "1002.01")

    def test_three_level_code(self):
        self.assertEqual(dxn_paper.dxn_code("11220101", 3), "1122.01.01")

    def test_level_one_code_unchanged(self):
        self.assertEqual(dxn_paper.dxn_code("1002", 1), "1002")


class GroupTests(unittest.TestCase):
    def test_same_report_paper_groups_accounts(self):
        paper = audit_index.resolve("1002", "银行存款")[0]
        grouped = dxn_paper.group_accounts(ledger(), paper, resolve)
        self.assertEqual([row.code for row in grouped], ["1001", "1002", "1009"])

    def test_other_report_paper_excluded(self):
        paper = audit_index.resolve("1122", "应收账款")[0]
        self.assertEqual(dxn_paper.group_accounts(ledger(), paper, resolve), [])

    def test_detail_rows_skip_parent_when_children_exist(self):
        rows = dxn_paper.detail_rows(ledger(), ledger()[1])
        self.assertEqual([row.code for row in rows], ["100201"])

    def test_detail_rows_fall_back_to_self(self):
        rows = dxn_paper.detail_rows(ledger(), ledger()[0])
        self.assertEqual([row.code for row in rows], ["1001"])


class StructureTests(unittest.TestCase):
    def test_sheets_follow_dxn_order(self):
        book = build()
        self.assertEqual(book.sheetnames[:6], ["dxnsjtempsheet", "底稿目录", "1-010 审定表",
                                               "1-010 明细表", "审计程序", "披露表"])

    def test_cash_includes_special_sheets(self):
        names = build().sheetnames
        for title in ("分析程序表", "银行账户清单", "银行存款余额调节表", "库存现金监盘表",
                      "银行函证信息汇总表", "货币资金截止性测试"):
            self.assertIn(title, names)

    def test_asset_categories_get_their_own_sheets(self):
        rows = [LedgerRow(code="1601", name="固定资产", path="固定资产", level=1, direction="借",
                          opening=Decimal("3000000"), debit=Decimal("200000"), credit=Decimal("0"),
                          ending=Decimal("3200000"), subject_id="S1", year=2025, month=12)]
        folder = tempfile.mkdtemp()
        path = Path(folder) / "固定资产.xlsx"
        paper = audit_index.resolve("1601", "固定资产")[0]
        dxn_paper.build(path, paper=paper, subject_label="合成企业 / 2025-12", rows=rows,
                        resolve=resolve, prepared_by="张三", reviewed_by="李四", when="2026-11-04")
        names = load_workbook(path).sheetnames
        self.assertIn("资产明细及折旧摊销表", names)
        self.assertIn("权属证明检查表", names)
        self.assertNotIn("银行账户清单", names)

    def test_header_fields_use_dxn_metadata(self):
        book = build()
        text = text_of(book)
        self.assertIn("被审计单位：合成企业", text)
        self.assertIn("财务报表截止日：2025年12月31日", text)
        self.assertIn("会计期间：2025年01月—2025年12月", text)
        self.assertIn("编制人：张三", text)

    def test_catalog_lists_sheets(self):
        text = text_of(build()["底稿目录"])
        for name in ("1-010 审定表", "1-010 明细表", "审计程序", "披露表"):
            self.assertIn(name, text)


class AuditSheetTests(unittest.TestCase):
    def test_amounts_come_from_ledger(self):
        sheet = build()["1-010 审定表"]
        values = [cell.value for row in sheet.iter_rows() for cell in row
                  if isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool)]
        self.assertIn(101500.25, values)     # 期末
        self.assertIn(100000, values)        # 期初（上期末）

    def test_total_row_uses_sumif_formulas(self):
        sheet = build()["1-010 审定表"]
        text = " ".join(str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value)
        self.assertIn('SUMIF($C$9:$C$11,"借"', text)

    def test_change_rate_is_written(self):
        sheet = build()["1-010 审定表"]
        rates = [cell.value for row in sheet.iter_rows() for cell in row
                 if isinstance(cell.value, float) and 0 < cell.value < 1]
        self.assertTrue(any(abs(value - 0.0150025) < 1e-9 for value in rates), rates)

    def test_report_figure_is_marked_pending(self):
        self.assertIn("本机未读取财务报表", text_of(build()["1-010 审定表"]))

    def test_profit_loss_uses_period_columns(self):
        rows = [LedgerRow(code="6001", name="主营业务收入", path="主营业务收入", level=1,
                          direction="贷", opening=Decimal("0"), debit=Decimal("8652000"),
                          credit=Decimal("8652000"), ending=Decimal("0"),
                          subject_id="S1", year=2025, month=12, is_profit_loss=True)]
        folder = tempfile.mkdtemp()
        path = Path(folder) / "收入.xlsx"
        paper = audit_index.resolve("6001", "主营业务收入")[0]
        dxn_paper.build(path, paper=paper, subject_label="合成企业 / 2025-12", rows=rows,
                        resolve=resolve, prepared_by="张三", reviewed_by="李四", when="2026-11-04",
                        procedures=paper_pack.procedures(paper.index_no, paper.name))
        text = text_of(load_workbook(path)["4-010 审定表"])
        self.assertIn("本期账面数", text)
        self.assertIn("上期账面数", text)
        self.assertNotIn("期末账面数", text)


class DetailSheetTests(unittest.TestCase):
    def test_fetch_marker_follows_dxn_style(self):
        sheet = build()["1-010 明细表"]
        marks = [str(sheet.cell(r, 1).value) for r in range(1, sheet.max_row + 1)
                 if str(sheet.cell(r, 1).value or "").startswith("1:K")]
        self.assertIn("1:K1002.01", marks)

    def test_subtotal_and_total_formulas(self):
        text = " ".join(str(cell.value) for row in build()["1-010 明细表"].iter_rows()
                        for cell in row if cell.value)
        self.assertIn("银行存款小计", text)
        self.assertIn("货币资金合计", text)

    def test_parent_is_not_double_counted(self):
        sheet = build()["1-010 明细表"]
        codes = [str(sheet.cell(r, 2).value) for r in range(1, sheet.max_row + 1)]
        self.assertIn("1002.01", codes)
        self.assertNotIn("1002", codes)      # 有下级时不再列上级本身

    def test_cross_check_row_present(self):
        text = text_of(build()["1-010 明细表"])
        self.assertIn("与审定表核对（期末审定数）", text)


class ReadLayoutTests(unittest.TestCase):
    """用合成底稿验证解析，默认测试不探测本机客户文件。"""

    def sample(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "synthetic.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.title = "dxnsjtempsheet"
        sheet.append(["合成元数据"])
        sheet.append([None, "索引号", "ZA"])
        sheet = book.create_sheet("账项明细表")
        sheet.append(["1:K1002.01", "合成账户"])
        book.save(path)
        book.close()
        return path

    def test_metadata_parsed(self):
        meta = dxn_paper.read_metadata(self.sample())
        self.assertIn("索引号", meta)
        self.assertEqual(str(meta["索引号"]).strip(), "ZA")

    def test_fetch_markers_parsed(self):
        layout = dxn_paper.read_layout(self.sample())
        marks = layout.get("账项明细表") or {}
        self.assertTrue(any(code.startswith("1002") for code in marks), list(marks)[:5])

    def test_our_paper_metadata_round_trips(self):
        folder = tempfile.mkdtemp()
        path = Path(folder) / "x.xlsx"
        paper = audit_index.resolve("1002", "银行存款")[0]
        dxn_paper.build(path, paper=paper, subject_label="合成企业 / 2025-12", rows=ledger(),
                        resolve=resolve, prepared_by="张三", reviewed_by="李四", when="2026-11-04")
        layout = dxn_paper.read_layout(path)
        self.assertIn("1002.01", layout["明细表"])


class CreditAccountTests(unittest.TestCase):
    """贷方科目（长期借款）：明细小计与审定合计必须同号，否则校验永远报错。"""

    def build(self):
        row = LedgerRow(code="2501", name="长期借款", path="长期借款", level=1, direction="贷",
                        opening=Decimal("100000.00"), debit=Decimal("10000.00"),
                        credit=Decimal("60000.00"), ending=Decimal("150000.00"),
                        subject_id="S1", year=2025, month=12)
        folder = tempfile.mkdtemp()
        path = Path(folder) / "长期借款.xlsx"
        paper = audit_index.resolve("2501", "长期借款")[0]
        dxn_paper.build(path, paper=paper, subject_label="合成企业 / 2025-12", rows=[row],
                        resolve=resolve, prepared_by="张三", reviewed_by="李四", when="2026-11-04")
        return load_workbook(path)

    def test_detail_subtotal_signs_both_ends(self):
        sheet = self.build()["2-230 明细表"]
        for cell in ("F9", "S9"):
            self.assertIn('SUMIF($E$8:$E$8,"贷"', str(sheet[cell].value))

    def test_audit_total_subtracts_credit(self):
        sheet = self.build()["2-230 审定表"]
        self.assertIn('SUMIF($C$9:$C$9,"贷"', str(sheet["K10"].value))

    def test_amounts_match_before_formula(self):
        book = self.build()
        detail = book["2-230 明细表"]["Q8"].value
        audit = book["2-230 审定表"]["F9"].value
        self.assertAlmostEqual(float(detail), float(audit), places=2)

    def test_same_account_listed_once(self):
        row = LedgerRow(code="2501", name="长期借款", path="长期借款", level=1, direction="贷",
                        opening=Decimal("0"), debit=Decimal("0"), credit=Decimal("0"),
                        ending=Decimal("0"), subject_id="S1", year=2025, month=12)
        paper = audit_index.resolve("2501", "长期借款")[0]
        grouped = dxn_paper.group_accounts([row, row], paper, resolve)
        self.assertEqual(len(grouped), 1)
