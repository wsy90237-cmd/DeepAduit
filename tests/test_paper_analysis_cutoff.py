"""底稿结构化回复、变动分析、同口径勾稽及截止性候选，全部使用合成数据。"""

from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from openpyxl import load_workbook

from tb_autofill import ai, audit_index, dxn, dxn_paper, paper_sheets, papers, sampling
from tb_autofill.models import JournalRow, JournalRows, LedgerRow, Subject


def ledger(code="1002", name="银行存款", direction="借", pl=False):
    root = LedgerRow(code, name, path=name, level=1, direction=direction,
                     opening=Decimal(0 if pl else 100), ending=Decimal(0 if pl else 125),
                     debit=Decimal(125 if pl else 25), credit=Decimal(125 if pl else 0),
                     subject_id="1", ledger_id="9", year=2026, month=6, is_profit_loss=pl,
                     period_amount=Decimal(125) if pl else None)
    child = replace(root, code=code + "01", name="合成明细", path=name + "/合成明细", level=2)
    return [root, child]


def journal(day="2026-06-30", month=6, summary="期末暂估付款", voucher="合成-1", amount="10000"):
    return JournalRow(month, day, voucher, "1", summary, "100201", debit=Decimal(amount))


class PaperResponseTests(unittest.TestCase):
    def test_prompt_uses_paragraphs_and_only_reference_documents(self):
        request = self.request()
        self.assertIn("高级审计经理", request["system"])
        self.assertIn("职业怀疑", request["system"])
        self.assertIn("未取得证据", request["system"])
        self.assertIn("扩大样本", request["system"])
        self.assertIn("账内观察", request["system"])
        self.assertNotIn('"1、..."', request["question"])
        self.assertEqual(request["context"]["facts"]["analysis_rows"][0]["rate"], "25.00%")
        self.assertTrue(all("SKILL.md" not in item["locator"] for item in request["context"]["knowledge_refs"]))
        self.assertEqual(request["context"]["facts"]["workpaper_indexes"]["账项明细表"], "1-010-1")
        self.assertIn("序时账", request["question"])
        self.assertIn('"conclusion"', request["question"])

    def test_model_receives_actual_book_mismatch_instead_of_an_assumed_success(self):
        rows = ledger()
        rows[1] = replace(rows[1], ending=Decimal(199))
        request = papers.pack_request(rows=rows, code="1002", paper=audit_index.resolve("1002", "银行存款")[0],
                                      procedures=["核对余额"])
        self.assertFalse(request["context"]["facts"]["book_check"]["consistent"])
        self.assertEqual(request["context"]["facts"]["book_check"]["absolute_difference_sum"], "74")

    def request(self):
        return papers.pack_request(rows=ledger(), code="1002", paper=audit_index.resolve("1002", "银行存款")[0],
                                   procedures=["核对余额"], journal=[journal()])

    def test_business_json_is_returned_directly_without_chat_answer_nesting(self):
        body = {"audit_notes": ["余额增长集中在合成明细，原因尚需核实。"],
                "analysis": [{"code": "1002", "note": "较年初增加，需结合收付款记录核实变动原因。"}]}
        calls = []
        def transport(path, payload):
            calls.append(payload)
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body, ensure_ascii=False)}}]}
        client = ai.DeepSeekClient("synthetic-key", transport=transport)
        request = self.request()
        result = papers.pack_result(client.paper_text(request), request)
        self.assertIn("1002", result["analysis_notes"])
        self.assertEqual(calls[0]["messages"][0]["content"], request["system"])
        self.assertEqual(json.loads(calls[0]["messages"][1]["content"]), request["context"])
        self.assertNotIn(ai.CONVERSATION_SYSTEM, calls[0]["messages"][0]["content"])

    def test_fenced_json_with_trailing_explanation_is_accepted(self):
        value = papers._first_json('```json\n{"audit_notes":["合成说明"]}\n```\n解释中另有 {}')
        self.assertEqual(value["audit_notes"], ["合成说明"])

    def test_plain_prose_reports_response_format_error(self):
        with self.assertRaisesRegex(ValueError, "模型已响应"):
            papers._first_json("本期余额有所增加。")

    def test_incomplete_response_is_not_accepted_as_complete_paper(self):
        client = ai.DeepSeekClient("synthetic-key", transport=lambda *args: {
            "choices": [{"finish_reason": "length", "message": {"content": '{"audit_notes":'}}]})
        with self.assertRaisesRegex(ai.AIError, "未完成"):
            client.paper_text(self.request())

    def test_unknown_analysis_code_cannot_add_a_foreign_account(self):
        with self.assertRaisesRegex(ValueError, "有效变动分析"):
            papers.pack_result({"analysis": [{"code": "foreign", "note": "不应采纳"}]}, self.request())

    def test_invented_amount_and_citation_are_not_written_to_paper(self):
        for text in ("余额为 987654321.12 元。", "已核对完成 [K999]"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                papers.pack_result({"audit_notes": [text]}, self.request())

    def test_unverified_model_conclusion_does_not_claim_a_completed_audit(self):
        result = papers.pack_result({"audit_notes": ["需核实凭证附件。"],
                                     "conclusion": "经审计，未见异常，余额可以确认。"}, self.request())
        self.assertNotIn("余额可以确认", result["conclusion"])
        self.assertIn("待核实", result["conclusion"])

    def test_known_workpaper_index_is_not_treated_as_an_invented_amount(self):
        rows = ledger("1601", "固定资产")
        request = papers.pack_request(rows=rows, code="1601", paper=audit_index.resolve("1601", "固定资产")[0],
                                      procedures=["核对明细"], journal=[journal()])
        result = papers.pack_result({"audit_notes": ["明细表见索引 1-280-1，凭证附件待检查。"]}, request)
        self.assertIn("1-280-1", result["guide_notes"][0])

    def test_known_dotted_account_code_does_not_block_fact_based_draft(self):
        rows = ledger("6602", "管理费用", pl=True)
        rows[1] = replace(rows[1], code="660207", name="职工薪酬", path="管理费用/职工薪酬")
        request = papers.pack_request(rows=rows, code="6602", paper=audit_index.resolve("6602", "管理费用")[0],
                                      procedures=["核对明细"])
        result = papers.pack_result({"audit_notes": ["职工薪酬（6602.07）的变动原因待核实。"]}, request)
        self.assertIn("6602.07", result["guide_notes"][0])

    def test_unverified_amount_reports_the_number(self):
        with self.assertRaises(papers.UnverifiedNarrativeError) as error:
            papers.pack_result({"audit_notes": ["余额增加 999.40元。"]}, self.request())
        self.assertIn("999.40元", str(error.exception))

    def test_procedure_and_review_numbers_are_also_checked(self):
        for extra in ({"procedures": [{"status": "差异 999.40元"}]}, {"review": "差异 999.40元"}):
            with self.subTest(extra=extra), self.assertRaises(papers.UnverifiedNarrativeError):
                papers.pack_result({"audit_notes": ["待核实。"], **extra}, self.request())

    def test_model_percentage_must_match_a_locally_computed_rate(self):
        self.assertTrue(papers.pack_result({"audit_notes": ["变动率为 25.00%。"]}, self.request()))
        with self.assertRaises(papers.UnverifiedNarrativeError):
            papers.pack_result({"audit_notes": ["变动率为 26.00%。"]}, self.request())


class PaperFillTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "synthetic.xlsx"

    def build(self, rows=None, **kwargs):
        rows = rows or ledger()
        paper = audit_index.resolve(rows[0].code, rows[0].name)[0]
        dxn_paper.build(self.path, paper=paper, subject_label="合成企业 / 2026-06", rows=rows,
                        accounts=[rows[0]], resolve=lambda c, n: audit_index.resolve(c, n)[0], **kwargs)
        book = load_workbook(self.path)
        self.addCleanup(book.close)
        return book, paper.index_no

    def test_analysis_numbers_are_local_and_ai_explanation_is_written(self):
        book, _ = self.build(narrative={"analysis_notes": {"1002": "余额增加，需核实对应收付款。"}})
        sheet = book["分析程序表"]
        self.assertEqual([sheet.cell(8, column).value for column in range(2, 6)], [125, 100, 25, 0.25])
        self.assertIn("需核实对应收付款", sheet["F8"].value)
        self.assertEqual(sheet["E8"].number_format, "0.00%")

    def test_profit_loss_does_not_use_zero_opening_as_prior_year_occurrence(self):
        book, _ = self.build(ledger("6001", "主营业务收入", "贷", True))
        sheet = book["分析程序表"]
        self.assertEqual(sheet["B8"].value, 125)
        self.assertIsNone(sheet["C8"].value)
        self.assertIsNone(sheet["D8"].value)
        self.assertIn("未提供上期发生额", sheet["F8"].value)

    def test_each_account_type_uses_a_comparable_book_measure(self):
        for code, name, direction, pl, expected_term in (
            ("1002", "银行存款", "借", False, "Q"),
            ("2501", "长期借款", "贷", False, "Q"),
            ("6001", "主营业务收入", "贷", True, "S"),
            ("6602", "管理费用", "借", True, "S"),
        ):
            with self.subTest(code=code):
                book, index = self.build(ledger(code, name, direction, pl))
                sheet = book[f"{index} 明细表"]
                row = next(row[0].row for row in sheet if str(row[0].value).startswith("与审定表核对") and "未审" in str(row[0].value))
                self.assertIn(expected_term, sheet.cell(row, 6).value)
                self.assertIn(f"'{index} 审定表'!F", sheet.cell(row, 6).value)
                self.assertIn("待补审定依据", sheet.cell(row + 1, 7).value)

    def test_audited_check_allows_valid_adjustments_instead_of_requiring_equal_balances(self):
        book, index = self.build()
        sheet = book[f"{index} 审定表"]
        check = next(row[0].row for row in sheet if row[0].value == "审定数与调整衔接差异")
        self.assertIn("K10-F10-G10+H10-I10+J10", sheet.cell(check, 4).value)
        for column in (7, 8, 9, 10):
            self.assertNotIn("SUMIF", sheet.cell(10, column).value)
        self.assertIsNone(sheet["K9"].value)
        self.assertEqual(sheet["D10"].number_format, dxn_paper.MONEY_FORMAT)

    def test_program_execution_column_is_removed(self):
        book, _ = self.build()
        self.assertNotIn("本项目执行情况", [cell.value for cell in book["审计程序"][7]])
        self.assertEqual(book["审计程序"].max_column, 9)

    def test_no_secondary_details_does_not_claim_completed_reconciliation(self):
        book, index = self.build(rows=ledger()[:1])
        text = " ".join(str(cell.value) for row in book[f"{index} 审定表"] for cell in row if cell.value)
        self.assertIn("尚未取得二级明细，无法完成", text)
        self.assertNotIn("账面数与科目余额表勾稽一致（差异 0.00 元）", text)

    def test_cutoff_sheet_is_prefilled_but_manual_conclusion_is_blank(self):
        book, _ = self.build(journal=[journal(), journal("2026-02-10", 2, "正常付款", "合成-2")])
        sheet = book["货币资金截止性测试"]
        self.assertEqual(sheet["B8"].value, "合成-1")
        self.assertIn("截止日", sheet["H8"].value)
        self.assertTrue(all(sheet.cell(8, column).value is None for column in (9, 10, 11)))
        self.assertNotIn("货币资金货币资金", sheet["A1"].value)

    def test_limited_journal_scope_is_explicit_on_cutoff_sheet(self):
        book, _ = self.build(journal=JournalRows([journal()], complete=False))
        text = str([cell.value for row in book["货币资金截止性测试"] for cell in row])
        self.assertIn("抽样总体不完整", text)

    def test_special_sheet_formulas_wait_for_both_inputs(self):
        for title, column, expression in (("银行账户清单", 8, "G8-F8"), ("库存现金监盘表", 4, "C8-B8"),
                                         ("坏账准备计算表", 6, "D8-E8"), ("存货盘点表", 5, "D8-C8"),
                                         ("借款及利息测算表", 8, "G8-F8")):
            with self.subTest(title=title):
                book, _ = self.build(sheet_add=[title])
                cell = book[title].cell(8, column)
                self.assertIn("IF(COUNT(", cell.value)
                self.assertIn(expression, cell.value)
                self.assertIn(',"")', cell.value)


class CutoffSelectionTests(unittest.TestCase):
    def test_early_january_is_not_mistaken_for_post_year_end(self):
        rows = [journal("2026-01-01", 1, voucher="年初"), journal("2026-12-30", 12, voucher="年末")]
        self.assertEqual([row.voucher for row, _reason in sampling.cutoff(rows, 2026, 12)], ["年末"])

    def test_post_cutoff_date_and_period_mismatch_are_risk_reasons(self):
        picked = sampling.cutoff([journal("2026-07-02", 6)], 2026, 6)
        self.assertEqual(len(picked), 1)
        self.assertIn("记账月份不一致", picked[0][1])

    def test_recent_extraction_detects_limit_and_never_claims_full_year_tie(self):
        subject = Subject("1", "合成企业", 2026, 6, "synthetic", ledger_id="9")
        database = object.__new__(dxn.DxnLedgerDB)
        database.data, database.connection = Path("synthetic/data"), MagicMock()
        cursor = database.connection.cursor.return_value.__enter__.return_value
        row = {"kjmonth": 6, "jzsj": "2026-06-30", "pzzl": "合成", "pzbh": "1", "bh": "1",
               "sm": "合成分录", "kmbh": "100201", "jffse": "10", "dffse": "0", "dfkmmc": "应付账款"}
        cursor.fetchall.return_value = [row, dict(row, bh="2"), dict(row, bh="3")]
        with patch.object(dxn, "_data_dir", return_value=database.data):
            result = database.journal(subject, "1002", limit=2)
        self.assertEqual(len(result), 2)
        self.assertFalse(result.complete)
        self.assertIn("DESC", cursor.execute.call_args.args[0])
        self.assertEqual(cursor.execute.call_args.args[1][-1], 3)
        facts = papers._journal_facts(result, replace(ledger()[0], debit=Decimal(20)))
        self.assertFalse(facts["tie"]["checked"])
        self.assertIn("有限条", facts["scope_note"])
