"""报表项目合册、备抵、客商及逐月损益；只使用合成数据，不启动界面。"""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import load_workbook

from tb_autofill import audit_index, dxn_paper, paper_sheets, papers
from tb_autofill.models import AuxiliaryBalanceRow, JournalRow, JournalRows, LedgerRow, PaperData


def row(code, name, ending="100", direction="借", **kwargs):
    return LedgerRow(code, name, path=name, direction=direction, opening=Decimal(ending),
                     ending=Decimal(ending), debit=Decimal(0), credit=Decimal(0),
                     subject_id="synthetic", ledger_id="1", year=2026, month=6, **kwargs)


def resolve(code, name):
    return audit_index.resolve(code, name)[0]


class ReportPaperDataTests(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.folder) / "synthetic-report.xlsx"

    def build(self, rows, **kwargs):
        paper = resolve(rows[0].code, rows[0].name)
        dxn_paper.build(self.path, paper=paper, subject_label="合成企业 / 2026-06", rows=rows,
                        resolve=resolve, **kwargs)
        book = load_workbook(self.path)
        self.addCleanup(book.close)
        return book

    def test_fixed_assets_join_depreciation_and_impairment_without_losing_net_sign(self):
        rows = [row("1601", "固定资产", "100"), row("1602", "累计折旧", "20", "贷"),
                row("1603", "固定资产减值准备", "5", "贷")]
        book = self.build(rows)
        self.assertEqual(dxn_paper.totals(rows, "借")["ending"], Decimal(75))
        self.assertEqual([book["1-280 审定表"].cell(r, 2).value for r in range(9, 12)],
                         ["固定资产", "累计折旧", "固定资产减值准备"])
        detail = book["资产明细及折旧摊销表"]
        self.assertEqual([detail.cell(8, column).value for column in range(2, 6)], [100, 20, 5, 75])
        self.assertEqual(detail["E9"].value, 75)
        detail = " ".join(str(cell.value) for cells in book["1-280 明细表"] for cell in cells)
        self.assertIn("累计折旧", detail)
        self.assertIn("固定资产减值准备", detail)

    def test_intangible_amortization_reverse_balance_increases_net_value(self):
        rows = [row("1701", "无形资产", "100"), row("1702", "累计摊销", "20", "借")]
        book = self.build(rows)
        detail = book["资产明细及折旧摊销表"]
        self.assertEqual([detail.cell(8, column).value for column in range(2, 6)], [100, -20, None, 120])
        self.assertEqual(dxn_paper.totals(rows, "借")["ending"], Decimal(120))

    def test_asset_categories_are_not_guessed_when_contra_names_do_not_match(self):
        roots = [row("1601", "固定资产"), row("1602", "累计折旧", "20", "贷")]
        children = [replace(roots[0], code="160101", name="合成机器", path="固定资产/合成机器", level=2),
                    replace(roots[1], code="160201", name="其他设备", path="累计折旧/其他设备", level=2)]
        data = paper_sheets.asset_component_rows(roots, [*roots, *children], Decimal(80))
        self.assertTrue(all(item[4] is None for item in data[:-1]))
        self.assertEqual(data[-1][4], Decimal(80))

    def test_income_uses_explicit_occurrence_and_positive_report_direction(self):
        source = replace(row("6001", "主营业务收入", "0", "贷", is_profit_loss=True),
                         debit=Decimal(999), credit=Decimal(888), period_amount=Decimal("7.2"),
                         previous_period_amount=Decimal("6"))
        book = self.build([source])
        self.assertEqual(book["4-010 审定表"]["D9"].value, 7.2)
        self.assertEqual(book["4-010 审定表"]["M9"].value, 6)
        self.assertEqual(book["披露表"]["B9"].value, 7.2)
        self.assertEqual(book["披露表"]["C9"].value, 6)
        self.assertIn('SUMIF($C$9:$C$9,"贷"', book["4-010 审定表"]["D10"].value)
        self.assertEqual(book["4-010 明细表"]["R8"].value, 7.2)

    def test_profit_loss_catalog_and_model_facts_have_occurrences_instead_of_balances(self):
        root = replace(row("6602", "管理费用", "888888", is_profit_loss=True), period_amount=Decimal(12000),
                       previous_period_amount=Decimal(10000))
        child = replace(root, code="660201", name="办公费", path="管理费用/办公费", level=2)
        book = self.build([root, child])
        values = [cell.value for sheet in book if sheet.sheet_state == "visible" for cells in sheet for cell in cells]
        self.assertIn("本期发生额", values)
        self.assertNotIn("期末账面余额（元）", values)
        self.assertNotIn("期初余额", values)
        self.assertNotIn("期末余额", values)
        self.assertNotIn(888888, values)
        request = papers.pack_request(rows=[root, child], paper=resolve(root.code, root.name), procedures=["合成核查程序"])
        facts = request["context"]["facts"]
        for item in [facts["subject"], *facts["accounts"], *facts["children"], *facts["movements"]]:
            self.assertFalse({"opening", "ending", "debit", "credit"} & item.keys())
        self.assertEqual(facts["accounts"][0]["current_period"], "12000")

    def test_revenue_confirmation_does_not_treat_customer_balance_as_turnover(self):
        state = {"is_pl": True, "auxiliary": [AuxiliaryBalanceRow("6001", "主营业务收入", "KH", "客户",
                 "SYN-C", "合成客户", ending=Decimal(98765))]}
        result = paper_sheets.prefill("函证信息汇总表", state)
        self.assertEqual(result[0][:2], (1, "合成客户"))
        self.assertTrue(all(value is None for value in result[0][2:]))

    def test_monthly_profit_loss_lists_terminal_details_once_and_keeps_future_months_blank(self):
        root = replace(row("6602", "管理费用", "0", is_profit_loss=True), period_amount=Decimal(21),
                       previous_period_amount=Decimal(12), monthly_amounts=(Decimal(3),) * 6 + (None,) * 6)
        middle = replace(root, code="660201", name="人员费用", path="管理费用/人员费用", level=2)
        wage = replace(root, code="66020101", name="工资", path="管理费用/人员费用/工资", level=3,
                       period_amount=Decimal(12), monthly_amounts=(Decimal(2),) * 6 + (None,) * 6)
        welfare = replace(wage, code="66020102", name="福利", path="管理费用/人员费用/福利",
                          period_amount=Decimal(9), monthly_amounts=(Decimal("1.5"),) * 6 + (None,) * 6)
        book = self.build([root, middle, wage, welfare])
        detail = book["5-110 明细表"]
        self.assertEqual([detail.cell(7, column).value for column in range(6, 18)],
                         [f"{month}月发生额" for month in range(1, 13)])
        values = [detail.cell(8, column).value for column in range(6, 18)]
        self.assertEqual(values, [2] * 6 + [None] * 6)
        self.assertEqual([detail["D8"].value, detail["D9"].value], ["工资", "福利"])
        self.assertEqual(detail["R8"].value, 12)
        self.assertIn("COUNT(F8:K8)=6", detail["AC8"].value)
        self.assertTrue(all(detail.cell(8, column).value is None for column in (20, 21, 24, 25)))
        self.assertNotIn("未审借方发生额", [cell.value for cell in detail[7]])

    def test_missing_period_is_not_replaced_by_debit_credit_or_zero(self):
        root = replace(row("6001", "主营业务收入", "0", "贷", is_profit_loss=True),
                       debit=Decimal(500), credit=Decimal(500))
        book = self.build([root])
        self.assertIsNone(book["4-010 审定表"]["D9"].value)
        self.assertIsNone(book["4-010 明细表"]["R8"].value)
        self.assertIsNone(book["分析程序表"]["B8"].value)
        self.assertIsNone(dxn_paper.totals([root], "贷")["period"])
        self.assertIn("COUNT(D9:D9)=1", book["4-010 审定表"]["D10"].value)

    def test_profit_loss_reconciles_every_exported_level_and_preserves_unknowns(self):
        root = replace(row("6602", "管理费用", "0", is_profit_loss=True), period_amount=Decimal(100))
        middle = replace(root, code="660201", name="办公费", path="管理费用/办公费", level=2)
        leaf = replace(middle, code="66020101", name="文具费", path="管理费用/办公费/文具费", level=3,
                       period_amount=Decimal(90))
        self.assertEqual(dxn_paper.consistency([root], [root, middle, leaf]), (False, Decimal(10)))
        book = self.build([root, middle, leaf])
        self.assertEqual(book["5-110 明细表"]["R8"].value, 90)
        text = " ".join(str(cell.value) for cells in book["5-110 审定表"] for cell in cells)
        self.assertIn("发生额逐级核对存在差异", text)
        self.assertNotIn("四栏逐栏核对一致", text)
        self.assertEqual(dxn_paper.consistency([root], [root, middle, replace(leaf, period_amount=None)]), (None, None))
        self.assertEqual(dxn_paper.consistency([root], [root, middle, replace(leaf, period_amount=Decimal(100))]),
                         (True, Decimal(0)))
        reverse = replace(leaf, direction="贷", period_amount=Decimal(-100))
        self.assertEqual(dxn_paper.consistency([root], [root, middle, reverse]), (True, Decimal(0)))

    def test_profit_loss_checks_previous_occurrence_at_deep_levels(self):
        root = replace(row("6602", "管理费用", "0", is_profit_loss=True), period_amount=Decimal(100),
                       previous_period_amount=Decimal(80))
        middle = replace(root, code="660201", name="办公费", path="管理费用/办公费", level=2)
        leaf = replace(middle, code="66020101", name="文具费", path="管理费用/办公费/文具费", level=3,
                       previous_period_amount=Decimal(70))
        self.assertEqual(dxn_paper.consistency([root], [root, middle, leaf]), (False, Decimal(10)))
        self.assertEqual(dxn_paper.consistency([root], [root, middle, replace(leaf, previous_period_amount=None)]),
                         (None, None))

    def customer_data(self, count=15):
        root = row("1122", "应收账款", str(count * 10))
        child = replace(root, code="112201", name="开票应收款", path="应收账款/开票应收款", level=2)
        auxiliary = tuple(AuxiliaryBalanceRow(child.code, child.name, "001", "客户", str(number),
                                             f"合成客商{number:02}", "借", Decimal(10), Decimal(10),
                                             Decimal(0), Decimal(0), "synthetic", "1", 2026, 6)
                          for number in range(count))
        check = {"account_code": child.code, "type_code": "001", "status": "matched",
                 "opening_difference": Decimal(0), "ending_difference": Decimal(0), "note": "仅核对客户维度"}
        return PaperData((root, child), (root.code,), auxiliary, auxiliary_checks=(check,))

    def test_customer_names_replace_account_dimension_and_are_not_limited_to_ten(self):
        data = self.customer_data()
        book = self.build(data.rows, paper_data=data)
        detail = book["1-070 明细表"]
        names = [cells[3].value for cells in detail if str(cells[0].value).startswith("辅助核算:")]
        self.assertEqual(names, [f"合成客商{number:02}" for number in range(15)])
        self.assertEqual(detail["D8"].value, "合成客商00")
        self.assertIn("COUNT(F8:F22)=15", detail["F23"].value)
        self.assertEqual(book["账龄分析表"]["A22"].value, "合成客商14")
        self.assertEqual(book["客商明细核对"]["F8"].value, "相符")

    def test_missing_customer_dimension_does_not_guess_customers_from_account_names(self):
        data = self.customer_data()
        data = replace(data, auxiliary=(), auxiliary_checks=())
        book = self.build(data.rows, paper_data=data)
        self.assertIsNone(book["账龄分析表"]["A8"].value)
        self.assertIn("未取得", str(book["客商明细核对"]["A8"].value))

    def test_foreign_or_multiple_customer_dimensions_are_rejected(self):
        data = self.customer_data(1)
        for auxiliary in ((replace(data.auxiliary[0], ledger_id="other"),),
                          (*data.auxiliary, replace(data.auxiliary[0], type_code="another"))):
            with self.subTest(auxiliary=auxiliary), self.assertRaises(ValueError):
                self.build(data.rows, paper_data=replace(data, auxiliary=auxiliary))

    def test_customer_text_is_literal_and_audited_amounts_remain_blank(self):
        data = self.customer_data(1)
        data = replace(data, auxiliary=(replace(data.auxiliary[0], name="=1+1"),))
        book = self.build(data.rows, paper_data=data)
        detail = book["1-070 明细表"]
        self.assertEqual(detail["D8"].value, "=1+1")
        self.assertEqual(detail["D8"].data_type, "s")
        self.assertTrue(all(detail.cell(8, column).value is None for column in (7, 9, 10, 13, 14, 16, 18, 19)))

    def test_required_core_sheets_cannot_be_removed(self):
        book = self.build([row("1001", "库存现金", "0")], sheet_drop=["分析程序表", "抽凭表"])
        self.assertTrue({"分析程序表", "抽凭表"} <= set(book.sheetnames))

    def test_unspecified_journal_uses_paper_data_but_explicit_none_excludes_it(self):
        source = row("1001", "库存现金", "0")
        journal = JournalRows([JournalRow(1, "2026-01-01", "SYN-1", "1", "SYNTHETIC-JOURNAL",
                                          source.code, Decimal(1), Decimal(0))])
        data = PaperData(rows=(source,), codes=(source.code,), journal=journal)
        included = self.build(data.rows, paper_data=data)
        self.assertEqual(included["抽凭表"]["D8"].value, "SYNTHETIC-JOURNAL")
        included.close()
        excluded = self.build(data.rows, paper_data=data, journal=None)
        self.assertIsNone(excluded["抽凭表"]["D8"].value)

    def test_unknown_suggested_sheet_can_use_reference_catalog_spec(self):
        spec = ("合成原模板参考表", (("业务事项", 28, "text"), ("依据", 30, "text")),
                "仅为结构参考，业务取数待核实", 2, None)
        with patch.object(dxn_paper.dxn_templates, "spec_for", return_value=spec):
            book = self.build([row("1001", "库存现金", "0")], sheet_add=[spec[0]])
        self.assertIn(spec[0], book.sheetnames)
        self.assertIsNone(book[spec[0]]["A8"].value)


if __name__ == "__main__":
    unittest.main()
