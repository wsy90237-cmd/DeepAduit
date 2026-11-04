"""整册底稿：目录结构、取数勾稽、程序执行情况与文字栏回落。"""

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from tb_autofill import audit_index, paper_pack, papers
from tb_autofill.models import JournalRow, LedgerRow


def ledger():
    def row(code, name, path, level, opening, debit, credit, ending):
        return LedgerRow(code=code, name=name, path=path, level=level, direction="借",
                         opening=Decimal(opening), debit=Decimal(debit), credit=Decimal(credit),
                         ending=Decimal(ending), subject_id="S1", year=2025, month=12)

    return [
        row("1002", "银行存款", "银行存款", 1, "600000.00", "60000.60", "30000.00", "630000.60"),
        row("100201", "工行基本户", "银行存款/工行基本户", 2, "100000.00", "10000.10",
            "5000.00", "105000.10"),
        row("100202", "农发行一般户", "银行存款/农发行一般户", 2, "200000.00", "20000.20",
            "10000.00", "210000.20"),
        row("100203", "国开行一般户", "银行存款/国开行一般户", 2, "300000.00", "30000.30",
            "15000.00", "315000.30"),
    ]


def journal():
    return [
        JournalRow(month=3, date="2025-03-18", voucher="记-0032", line="2", summary="收回货款",
                   code="100201", debit=Decimal("2000"), contra="应收账款"),
        JournalRow(month=6, date="2025-06-30", voucher="记-0188", line="1", summary="支付合成服务费",
                   code="100202", credit=Decimal("20000"), contra="应付账款"),
    ]


def paper():
    value, _basis = audit_index.resolve("1002", "银行存款")
    return value


def build(**kwargs):
    folder = tempfile.mkdtemp()
    path = Path(folder) / "底稿.xlsx"
    paper_pack.build(path, paper=paper(), subject_label="合成企业 / 2025-12", subject_row=ledger()[0],
                     rows=ledger(), journal=kwargs.pop("journal", journal()),
                     prepared_by="张三", reviewed_by="李四", when="2026-11-04",
                     export_time="2026-11-04 11:40", **kwargs)
    return load_workbook(path)


def text_of(workbook):
    return "\n".join(str(cell.value) for sheet in workbook.worksheets
                     for row in sheet.iter_rows() for cell in row if cell.value is not None)


class StructureTests(unittest.TestCase):
    def test_sheets_follow_template_order(self):
        book = build()
        self.assertEqual(book.sheetnames, ["底稿目录", "1-010 导引表", "1-010-0 程序表",
                                           "1-010-1 货币资金明细表", "1-010-2 凭证抽查表",
                                           "1-010-15 披露表", "1-010-9 资料需求清单",
                                           "9720-1-010 复核自检清单"])

    def test_header_fields_present_on_every_sheet(self):
        book = build()
        for sheet in book.worksheets:
            values = [str(cell.value) for row in sheet.iter_rows(min_row=1, max_row=4)
                      for cell in row if cell.value]
            joined = " ".join(values)
            self.assertIn("被审计单位：合成企业", joined)
            self.assertIn("编制人：张三", joined)
            self.assertIn("复核人：李四", joined)
            self.assertIn("2025年12月31日", joined)

    def test_catalog_lists_every_sheet(self):
        book = build()
        catalog = book["底稿目录"]
        text = "\n".join(str(cell.value) for row in catalog.iter_rows() for cell in row if cell.value)
        for name in book.sheetnames[1:]:
            self.assertIn(name, text)


class ReconciliationTests(unittest.TestCase):
    def test_balanced_detail_matches_ledger(self):
        total = {"opening": Decimal("600000.00"), "debit": Decimal("60000.60"),
                 "credit": Decimal("30000.00"), "ending": Decimal("630000.60")}
        check = paper_pack.reconcile(total, ledger()[0])
        self.assertTrue(check["consistent"])
        self.assertEqual(check["diff"]["ending"], Decimal("0"))

    def test_mismatch_is_flagged_not_hidden(self):
        total = {"opening": Decimal("0"), "debit": Decimal("0"), "credit": Decimal("0"),
                 "ending": Decimal("1")}
        check = paper_pack.reconcile(total, ledger()[0])
        self.assertFalse(check["consistent"])
        self.assertNotEqual(check["diff"]["ending"], Decimal("0"))

    def test_detail_sheet_shows_ledger_row_and_difference(self):
        book = build()
        text = text_of(book)
        self.assertIn("科目余额表（1002 银行存款）", text)
        self.assertIn("差异（明细合计－科目余额表）", text)

    def test_amounts_come_from_ledger_not_model(self):
        book = build()
        sheet = book["1-010 导引表"]
        numbers = [cell.value for row in sheet.iter_rows() for cell in row
                   if isinstance(cell.value, float)]
        self.assertIn(630000.60, numbers)


class ProcedureTests(unittest.TestCase):
    def test_procedures_are_category_specific(self):
        cash = [item.text for item in paper_pack.procedures("1-010", "货币资金")]
        self.assertTrue(any("监盘库存现金" in text for text in cash))
        receivable = [item.text for item in paper_pack.procedures("1-070", "应收账款")]
        self.assertTrue(any("账龄" in text for text in receivable))
        self.assertNotEqual(cash, receivable)

    def test_most_procedures_carry_assertion_or_index(self):
        items = paper_pack.procedures("1-280", "固定资产")
        tagged = sum(1 for item in items if item.assertions or item.basis)
        self.assertGreaterEqual(tagged / len(items), 0.9, f"{tagged}/{len(items)}")
        self.assertTrue(all(item.text.strip() for item in items))

    def test_procedure_sheet_omits_execution_status(self):
        book = build(narrative={"procedure_notes": ["已执行（与科目余额表核对一致）"]})
        sheet = book["1-010-0 程序表"]
        text = "\n".join(str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value)
        self.assertNotIn("本项目执行情况", text)
        self.assertNotIn("已执行（与科目余额表核对一致）", text)


class NarrativeTests(unittest.TestCase):
    def test_guide_notes_replace_placeholder(self):
        book = build(narrative={"guide_notes": ["1、总账与明细账核对相符，详见 1-010-1；"]})
        self.assertIn("总账与明细账核对相符", text_of(book))

    def test_disclosure_note_mapping(self):
        book = build(narrative={"disclosure_notes": [{"item": "银行存款", "note": "3 个活期账户"}]})
        self.assertIn("3 个活期账户", text_of(book))

    def test_review_conclusion_written(self):
        book = build(narrative={"review_conclusion": "勾稽一致，外部证据待补。"})
        self.assertIn("勾稽一致，外部证据待补。", text_of(book))

    def test_missing_narrative_keeps_blank_prompts(self):
        book = build(narrative={})
        self.assertIn("待补", text_of(book))


class JournalTests(unittest.TestCase):
    def test_samples_are_ordered_by_amount(self):
        samples = paper_pack.sample_journal(journal())
        self.assertEqual(samples[0].voucher, "记-0188")
        self.assertEqual(len(samples), 2)

    def test_sample_limit_respected(self):
        many = journal() * 12
        self.assertEqual(len(paper_pack.sample_journal(many)), paper_pack.SAMPLE_LIMIT)

    def test_empty_journal_is_stated(self):
        book = build(journal=None)
        self.assertIn("本期无序时账记录或未读取序时账", text_of(book))


class GuardTests(unittest.TestCase):
    def test_unknown_subject_raises(self):
        with self.assertRaises(ValueError):
            paper_pack.build(Path(tempfile.mkdtemp()) / "x.xlsx", paper=paper(),
                             subject_label="合成企业 / 2025-12",
                             subject_row=NextMissing(), rows=ledger())

    def test_category_fallbacks(self):
        self.assertEqual(paper_pack.category("1-070", "应收账款"), "receivable")
        self.assertEqual(paper_pack.category("2-230", "长期借款"), "liability")
        self.assertEqual(paper_pack.category("3-010", "股本"), "equity")
        self.assertEqual(paper_pack.category("4-010", "营业收入"), "revenue")
        self.assertEqual(paper_pack.category("5-110", "管理费用"), "expense")
        self.assertEqual(paper_pack.category("1-280", "固定资产"), "asset")


class NextMissing:
    """科目余额表里不存在的科目行。"""

    code, name, path, level, direction = "9999", "不存在科目", "不存在科目", 1, "借"
    opening = debit = credit = ending = Decimal("0")


class FakeClient:
    """把给定回复当作模型回答；用于验证 JSON 解析与字段对齐。"""

    def __init__(self, answer, intent="audit"):
        self.answer, self.intent = answer, intent
        self.calls = 0

    def converse_with_skills(self, *args, **kwargs):
        self.calls += 1
        return {"intent": self.intent, "answer": self.answer, "warnings": [],
                "citation_warnings": []}


class PackDraftTests(unittest.TestCase):
    def draft(self, answer, intent="audit", procedures=("程序甲", "程序乙", "程序丙")):
        client = FakeClient(answer, intent)
        return papers.draft_pack(client, rows=ledger(), code="1002", paper=paper(),
                                 procedures=procedures, subject_label="合成企业 / 2025-12")

    def test_json_is_parsed_and_aligned(self):
        answer = ('{"audit_notes": ["1、核对相符，详见 1-010-1；"],'
                  ' "procedures": [{"no": 1, "status": "已执行（账套核对）"},'
                  ' {"no": 3, "status": "不适用（账面为零）"}],'
                  ' "disclosure": [{"item": "银行存款", "note": "3 个活期户"}],'
                  ' "review": "勾稽一致"}')
        value = self.draft(answer)
        self.assertEqual(value["kind"], "pack")
        self.assertEqual(value["guide_notes"], ["1、核对相符，详见 1-010-1；"])
        self.assertEqual(value["procedure_notes"],
                         ["已执行（账套核对）", "待补（模型未给出执行情况）", "不适用（账面为零）"])
        self.assertEqual(value["disclosure_notes"][0]["note"], "3 个活期户")
        self.assertEqual(value["review_conclusion"], "勾稽一致")

    def test_fenced_json_is_accepted(self):
        answer = '```json\n{"audit_notes": ["1、见 1-010-1"], "procedures": []}\n```'
        self.assertTrue(self.draft(answer)["guide_notes"])

    def test_plain_text_is_rejected(self):
        with self.assertRaises(ValueError):
            self.draft("一、审计目标\n核对余额。")

    def test_offtopic_answer_is_rejected(self):
        with self.assertRaises(ValueError):
            self.draft('{"audit_notes": []}', intent="chat")

    def test_preview_lists_all_sections(self):
        answer = ('{"audit_notes": ["1、核对相符"], "procedures": [{"no": 1, "status": "已执行"}],'
                  ' "disclosure": [{"item": "银行存款", "note": "3 户"}], "review": "待补函证"}')
        text = papers.pack_preview(self.draft(answer), ["程序甲", "程序乙", "程序丙"])
        for section in ("审计说明", "程序表", "披露表", "复核自检"):
            self.assertIn(section, text)
        self.assertIn("待补函证", text)


def revenue_ledger():
    """损益类：主营业务收入，期末已结转，本期数取贷方发生额。"""
    def row(code, name, path, level, debit, credit, pl=True):
        return LedgerRow(code=code, name=name, path=path, level=level, direction="贷",
                         opening=Decimal("0"), debit=Decimal(debit), credit=Decimal(credit),
                         ending=Decimal("0"), subject_id="S1", year=2025, month=12,
                         is_profit_loss=pl)

    return [
        row("6001", "主营业务收入", "主营业务收入", 1, "8652000", "8652000"),
        row("600101", "产品销售收入", "主营业务收入/产品销售收入", 2, "6120000", "6120000"),
        row("600102", "技术服务收入", "主营业务收入/技术服务收入", 2, "2532000", "2532000"),
    ]


class ProfitLossLayoutTests(unittest.TestCase):
    def build(self):
        folder = tempfile.mkdtemp()
        path = Path(folder) / "收入底稿.xlsx"
        value, _basis = audit_index.resolve("6001", "主营业务收入")
        paper_pack.build(path, paper=value, subject_label="合成企业 / 2025-12",
                         subject_row=revenue_ledger()[0], rows=revenue_ledger(),
                         prepared_by="张三", reviewed_by="李四", when="2026-11-04")
        return load_workbook(path)

    def test_guide_uses_period_columns(self):
        book = self.build()
        text = " ".join(str(cell.value) for row in book["4-010 导引表"].iter_rows(max_row=8)
                        for cell in row if cell.value)
        self.assertIn("本期未审数", text)
        self.assertIn("本期审定数", text)
        self.assertNotIn("期初余额", text)

    def test_period_amount_uses_credit_side(self):
        book = self.build()
        sheet = book["4-010 导引表"]
        values = [cell.value for row in sheet.iter_rows() for cell in row
                  if isinstance(cell.value, (int, float))]
        self.assertIn(6120000.0, values)
        self.assertIn(2532000.0, values)
        self.assertIn(8652000.0, values)

    def test_period_amounts_carry_thousand_separator_format(self):
        """Decimal 金额也必须套用千分位格式，不能退回 General。"""
        book = self.build()
        sheet = book["4-010 导引表"]
        formats = [sheet.cell(row, 2).number_format for row in (8, 9, 10)]
        self.assertTrue(all("#,##0.00" in item for item in formats), formats)

    def test_detail_share_adds_up_to_full(self):
        book = self.build()
        sheet = book["4-010-1 营业收入明细表"]
        shares = [cell.value for row in sheet.iter_rows() for cell in row
                  if isinstance(cell.value, float) and 0 < cell.value <= 1]
        self.assertAlmostEqual(sum(shares[:3]), 1.0, places=6)

    def test_disclosure_uses_period_amount(self):
        book = self.build()
        text = " ".join(str(cell.value) for row in book["4-010-15 披露表"].iter_rows(max_row=8)
                        for cell in row if cell.value)
        self.assertIn("本期发生额", text)

    def test_reconciliation_still_checks_ledger(self):
        total = {"opening": Decimal("0"), "debit": Decimal("8652000"),
                 "credit": Decimal("8652000"), "ending": Decimal("0"),
                 "period": Decimal("8652000")}
        check = paper_pack.reconcile(total, revenue_ledger()[0])
        self.assertTrue(check["consistent"])

    def test_period_amount_by_direction(self):
        expense = revenue_ledger()[0]
        self.assertEqual(paper_pack.period_amount(expense), Decimal("8652000"))
        debit_side = LedgerRow(code="6602", name="管理费用", path="管理费用", level=1, direction="借",
                               opening=Decimal("0"), debit=Decimal("120000"), credit=Decimal("120000"),
                               ending=Decimal("0"), is_profit_loss=True)
        self.assertEqual(paper_pack.period_amount(debit_side), Decimal("120000"))
