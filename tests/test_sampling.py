"""凭证抽样：规则打分、模型挑选与回落、抽凭表落表。"""

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from tb_autofill import audit_index, dxn_paper, paper_sheets, papers, sampling
from tb_autofill.models import JournalRow, LedgerRow


def journal(count=15):
    rows = []
    for index in range(1, count + 1):
        amount = Decimal(str(index * 10000))
        rows.append(JournalRow(month=(index % 12) + 1, date=f"2025-{(index % 12) + 1:02d}-15",
                               voucher=f"记-{index:04d}", line="1",
                               summary="支付货款" if index % 3 else "期末调整冲销",
                               code="100201", debit=amount if index % 2 else None,
                               credit=None if index % 2 else amount, contra="应付账款"))
    return rows


class RulePickTests(unittest.TestCase):
    def test_returns_at_most_ten(self):
        self.assertLessEqual(len(sampling.pick(journal(30))), sampling.SAMPLE_SIZE)

    def test_every_pick_has_reason(self):
        for _row, reason in sampling.pick(journal(20)):
            self.assertTrue(reason)

    def test_time_and_amount_features_are_named(self):
        picks = sampling.pick(journal(20))
        reasons = " ".join(reason for _row, reason in picks)
        self.assertIn("金额较大", reasons)

    def test_round_amount_is_flagged(self):
        rows = [JournalRow(month=5, date="2025-05-10", voucher="记-0001", line="1",
                           summary="付工程款", code="100201", debit=Decimal("500000"),
                           contra="应付账款")]
        _picks = sampling.pick(rows)
        self.assertIn("整数金额", _picks[0][1])

    def test_empty_journal_returns_empty(self):
        self.assertEqual(sampling.pick([]), [])

    def test_stable_order_same_input(self):
        first = [(row.voucher, reason) for row, reason in sampling.pick(journal(20))]
        second = [(row.voucher, reason) for row, reason in sampling.pick(journal(20))]
        self.assertEqual(first, second)


class FakeClient:
    def __init__(self, answer):
        self.answer = answer

    def converse_with_skills(self, *args, **kwargs):
        return {"intent": "audit", "answer": self.answer, "warnings": []}

    paper_text = converse_with_skills


class AiPickTests(unittest.TestCase):
    def test_ai_picks_are_honoured(self):
        answer = json.dumps({"picks": [{"no": 2, "reason": "期末大额调整"},
                                       {"no": 5, "reason": "整数金额"}]}, ensure_ascii=False)
        picks, source = sampling.pick_with_ai(FakeClient(answer), "银行存款", journal(10))
        self.assertEqual(len(picks), 2)
        self.assertEqual(picks[0][1], "模型判断：期末大额调整")
        self.assertIn("模型", source)

    def test_bad_json_falls_back_to_rules(self):
        picks, source = sampling.pick_with_ai(FakeClient("我说不准"), "银行存款", journal(10))
        self.assertTrue(picks)
        self.assertIn("规则抽样", source)

    def test_out_of_range_index_is_dropped(self):
        answer = json.dumps({"picks": [{"no": 999, "reason": "越界"}]})
        picks, source = sampling.pick_with_ai(FakeClient(answer), "银行存款", journal(5))
        self.assertTrue(picks)
        self.assertIn("规则抽样", source)

    def test_no_client_uses_rules(self):
        _picks, source = sampling.pick_with_ai(None, "银行存款", journal(5))
        self.assertEqual(source, "规则抽样")


def ledger(with_cash=True):
    rows = [LedgerRow(code="1002", name="银行存款", path="银行存款", level=1, direction="借",
                      opening=Decimal("100000"), debit=Decimal("2000.50"),
                      credit=Decimal("500.25"), ending=Decimal("101500.25"),
                      subject_id="S", year=2025, month=12),
            LedgerRow(code="100201", name="合成银行000001", path="银行存款/合成银行000001",
                      level=2, direction="借", opening=Decimal("100000"),
                      debit=Decimal("2000.50"), credit=Decimal("500.25"),
                      ending=Decimal("101500.25"), subject_id="S", year=2025, month=12)]
    if with_cash:
        rows.insert(0, LedgerRow(code="1001", name="库存现金", path="库存现金", level=1,
                                 direction="借", opening=Decimal("5000"), debit=Decimal("120000"),
                                 credit=Decimal("118000"), ending=Decimal("7000"),
                                 subject_id="S", year=2025, month=12))
    return rows


def build(with_cash=True, journal_rows=None):
    folder = tempfile.mkdtemp()
    path = Path(folder) / "底稿.xlsx"
    paper = audit_index.resolve("1002", "银行存款")[0]
    dxn_paper.build(path, paper=paper, subject_label="合成企业 / 2025-12",
                    rows=ledger(with_cash), resolve=lambda c, n: audit_index.resolve(c, n)[0],
                    prepared_by="张三", reviewed_by="李四", when="2026-11-04",
                    journal=journal_rows)
    return load_workbook(path)


class SheetSelectionTests(unittest.TestCase):
    def test_no_cash_account_means_no_cash_count_sheet(self):
        names = build(with_cash=False).sheetnames
        self.assertNotIn("库存现金监盘表", names)

    def test_cash_account_brings_count_sheet(self):
        self.assertIn("库存现金监盘表", build(with_cash=True).sheetnames)

    def test_every_paper_has_voucher_sampling(self):
        self.assertIn("抽凭表", build(with_cash=False).sheetnames)

    def test_bank_confirmation_is_prefilled(self):
        sheet = build()["银行函证信息汇总表"]
        first = [sheet.cell(8, column).value for column in range(1, 5)]
        self.assertEqual(first[1], "合成银行")
        self.assertEqual(first[2], "000001")
        self.assertEqual(first[3], 101500.25)

    def test_parent_bank_account_is_not_listed(self):
        sheet = build()["银行函证信息汇总表"]
        names = [sheet.cell(row, 2).value for row in range(8, 18) if sheet.cell(row, 2).value]
        self.assertEqual(names, ["合成银行"])

    def test_voucher_sheet_lists_picks_with_reason(self):
        sheet = build(journal_rows=journal(12))["抽凭表"]
        rows = [[sheet.cell(row, column).value for column in (2, 3, 4, 9)]
                for row in range(8, 18) if sheet.cell(row, 3).value]
        self.assertGreaterEqual(len(rows), 5)
        self.assertTrue(all(item[3] for item in rows))

    def test_voucher_sheet_is_blank_without_journal(self):
        sheet = build()["抽凭表"]
        values = [sheet.cell(row, 3).value for row in range(8, 18)]
        self.assertTrue(all(value in (None, "") for value in values))


class CombinedPackTests(unittest.TestCase):
    def build(self, **kwargs):
        folder = tempfile.mkdtemp()
        path = Path(folder) / "合并.xlsx"
        paper = audit_index.resolve("1002", "银行存款")[0]
        dxn_paper.build(path, paper=paper, subject_label="合成企业 / 2025-12",
                        rows=ledger(False), resolve=lambda c, n: audit_index.resolve(c, n)[0],
                        prepared_by="张三", reviewed_by="李四", when="2026-11-04", **kwargs)
        return load_workbook(path)

    def test_review_sheet_present_and_no_lead_sheet(self):
        names = self.build().sheetnames
        self.assertIn("9720-1-010 复核自检清单", names)
        self.assertNotIn("1-010 导引表", names)          # 导引表与审定表只留一个

    def test_catalog_matches_sheet_count(self):
        book = self.build()
        sheet = book["底稿目录"]
        codes = [sheet.cell(row, 2).value for row in range(8, 40) if sheet.cell(row, 2).value]
        self.assertEqual(len(codes), len(book.sheetnames) - 2)   # 去掉 dxnsjtempsheet 与目录
        self.assertEqual(len(codes), len(set(codes)))

    def test_sheet_add_and_drop(self):
        names = self.build(sheet_drop=["货币资金截止性测试"], sheet_add=["存货盘点表"]).sheetnames
        self.assertNotIn("货币资金截止性测试", names)
        self.assertIn("存货盘点表", names)

    def test_available_names_include_category_specific(self):
        self.assertIn("银行函证信息汇总表", paper_sheets.names("cash"))
        self.assertIn("抽凭表", paper_sheets.names("cash"))


class SuggestSheetsTests(unittest.TestCase):
    def accounts(self):
        return ledger(False)

    def test_valid_suggestions_are_accepted(self):
        answer = json.dumps({"add": [{"name": "存货盘点表", "reason": "存在委托代销"}],
                             "remove": [{"name": "库存现金监盘表", "reason": "无现金"}]},
                            ensure_ascii=False)
        result = papers.suggest_sheets(FakeClient(answer), name="货币资金",
                                       accounts=self.accounts(),
                                       available=paper_sheets.names())
        self.assertEqual([item["name"] for item in result["add"]], ["存货盘点表"])
        self.assertEqual([item["name"] for item in result["remove"]], ["库存现金监盘表"])

    def test_unknown_sheet_is_ignored(self):
        answer = json.dumps({"add": [{"name": "不存在的表", "reason": "瞎编"}], "remove": []})
        result = papers.suggest_sheets(FakeClient(answer), name="货币资金",
                                       accounts=self.accounts(),
                                       available=paper_sheets.names("cash"))
        self.assertEqual(result["add"], [])

    def test_without_client_returns_note(self):
        result = papers.suggest_sheets(None, name="货币资金", accounts=self.accounts(),
                                       available=paper_sheets.names("cash"))
        self.assertEqual(result["add"], [])
        self.assertIn("未接入模型", result["note"])

    def test_bad_json_falls_back(self):
        result = papers.suggest_sheets(FakeClient("不知道"), name="货币资金",
                                       accounts=self.accounts(),
                                       available=paper_sheets.names("cash"))
        self.assertEqual(result["add"], [])
        self.assertTrue(result["error"])
        self.assertIn("专项表建议失败", result["note"])
