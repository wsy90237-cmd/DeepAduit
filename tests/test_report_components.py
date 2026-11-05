"""报表项目整册的组成与口径验收：备抵净额、客商维度、独立发生额。

只使用合成数据，不读取账套、不启动界面。
既有覆盖不重复：固定资产原值/折旧/减值三栏与反向摊销见
``tests/test_report_paper_data.py`` 的
``test_fixed_assets_join_depreciation_and_impairment_without_losing_net_sign`` 与
``test_intangible_amortization_reverse_balance_increases_net_value``；逐月损益与缺月留空见
``test_monthly_profit_loss_lists_terminal_details_once_and_keeps_future_months_blank``、
``test_missing_period_is_not_replaced_by_debit_credit_or_zero``；多客商维度拒绝见
``test_foreign_or_multiple_customer_dimensions_are_rejected``。
"""

from dataclasses import replace
from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from tb_autofill import audit_index, dxn_paper, paper_sheets
from tb_autofill.models import AuxiliaryBalanceRow, LedgerRow, PaperData


def row(code, name, ending="100", direction="借", **kwargs):
    values = dict(path=name, subject_id="synthetic", ledger_id="1", year=2026, month=6,
                  opening=Decimal(ending), ending=Decimal(ending),
                  debit=Decimal(0), credit=Decimal(0))
    values.update(kwargs)
    return LedgerRow(code, name, direction=direction, **values)


def resolve(code, name):
    return audit_index.resolve(code, name)[0]


class BuildCase(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.folder) / "synthetic-components.xlsx"

    def build(self, rows, *, paper=None, **kwargs):
        dxn_paper.build(self.path, paper=paper or resolve(rows[0].code, rows[0].name),
                        subject_label="合成企业 / 2026-06", rows=rows, resolve=resolve, **kwargs)
        book = load_workbook(self.path)
        self.addCleanup(book.close)
        return book


class ContraAssetTests(BuildCase):
    """固定资产与无形资产的备抵必须按资产项目配套列示，净额方向随借贷反转。"""

    def test_intangible_paper_lists_cost_amortization_and_impairment(self):
        rows = [row("1701", "无形资产", "100"),
                row("1702", "累计摊销", "20", "贷"),
                row("1703", "无形资产减值准备", "5", "贷")]
        book = self.build(rows)
        audit = book["1-330 审定表"]
        self.assertEqual([audit.cell(number, 2).value for number in range(9, 12)],
                         ["无形资产", "累计摊销", "无形资产减值准备"])
        detail = book["资产明细及折旧摊销表"]
        self.assertEqual([detail.cell(8, column).value for column in range(2, 6)],
                         [100, 20, 5, 75])
        self.assertEqual(dxn_paper.totals(rows, "借")["ending"], Decimal(75))
        self.assertEqual(book["披露表"]["B11"].value, 75)

    def test_missing_contra_account_keeps_columns_blank_but_nets_to_cost(self):
        """账套没有折旧科目时，备抵栏留空、净额等于原值；不能凭空补零金额。"""
        rows = [row("1601", "固定资产", "100")]
        book = self.build(rows)
        detail = book["资产明细及折旧摊销表"]
        self.assertEqual([detail.cell(8, column).value for column in range(2, 6)],
                         [100, None, None, 100])
        self.assertEqual(dxn_paper.totals(rows, "借")["ending"], Decimal(100))

    def test_debit_balance_on_depreciation_raises_net_value(self):
        rows = [row("1601", "固定资产", "100"),
                row("1602", "累计折旧", "20", "借")]
        book = self.build(rows)
        self.assertEqual(dxn_paper.totals(rows, "借")["ending"], Decimal(120))
        self.assertEqual([book["资产明细及折旧摊销表"].cell(8, column).value
                          for column in range(2, 6)], [100, -20, None, 120])
        total = book["1-280 审定表"]
        self.assertIn('SUMIF($C$9:$C$10,"借"', total["K11"].value)
        self.assertIn('-SUMIF($C$9:$C$10,"贷"', total["K11"].value)

    def test_contra_from_another_asset_family_is_excluded_or_rejected(self):
        rows = [row("1601", "固定资产", "100"), row("1702", "累计摊销", "20", "贷")]
        fixed_assets = resolve("1601", "固定资产")
        intangibles = resolve("1701", "无形资产")
        self.assertEqual([item.code for item in
                          dxn_paper.group_accounts(rows, fixed_assets, resolve)], ["1601"])
        self.assertEqual([item.code for item in
                          dxn_paper.group_accounts(rows, intangibles, resolve)], ["1702"])
        with self.assertRaisesRegex(ValueError, "不属于当前账套的已确认报表项目"):
            self.build(rows, paper=fixed_assets, accounts=rows)
        book = self.build(rows, paper=fixed_assets)
        self.assertEqual([book["1-280 审定表"].cell(number, 2).value for number in range(9, 10)],
                         ["固定资产"])

    def test_missing_depreciation_amount_is_unknown_not_zero(self):
        unknown = LedgerRow("1602", "累计折旧", path="累计折旧", direction="贷",
                            subject_id="synthetic", ledger_id="1", year=2026, month=6)
        rows = [row("1601", "固定资产", "100"), unknown]
        self.assertIsNone(dxn_paper.totals(rows, "借")["ending"])
        component = paper_sheets.asset_component_rows(rows, rows, Decimal(100))
        self.assertEqual(component[0][0], "整体")
        self.assertEqual(component[0][1], Decimal(100))
        self.assertIsNone(component[0][2])
        self.assertIsNone(component[0][4])
        with self.assertRaisesRegex(ValueError, "不能以零代替"):
            self.build(rows)


class CustomerDimensionTests(BuildCase):
    """客商明细按已选维度列示：同名不合并、父子不重复、差异与缺值原样保留。"""

    def accounts(self):
        root = row("1122", "应收账款", "100")
        child = replace(root, code="112201", name="开票应收款",
                        path="应收账款/开票应收款", level=2)
        return root, child

    def party(self, code, name, ending, direction="借", opening=None):
        return AuxiliaryBalanceRow("112201", "开票应收款", "001", "客户", code, name, direction,
                                   Decimal(opening if opening is not None else ending),
                                   None if ending is None else Decimal(ending),
                                   Decimal(0), Decimal(0), "synthetic", "1", 2026, 6)

    def data(self, auxiliary, *, difference=(None, None)):
        root, child = self.accounts()
        check = {"account_code": child.code, "type_code": "001", "status": "difference",
                 "opening_difference": difference[0], "ending_difference": difference[1],
                 "note": "仅使用客户维度"}
        return PaperData((root, child), (root.code,), tuple(auxiliary), auxiliary_checks=(check,))

    def test_same_name_with_different_codes_is_not_merged(self):
        data = self.data([self.party("C01", "合成贸易", "30"),
                          self.party("C02", "合成贸易", "20")])
        book = self.build(data.rows, paper_data=data)
        detail = book["1-070 明细表"]
        names = [detail.cell(number, 4).value for number in range(8, 10)]
        self.assertEqual(names, ["合成贸易", "合成贸易"])
        self.assertIn("COUNT(F8:F9)=2", detail["F10"].value)
        self.assertEqual(book["账龄分析表"]["A8"].value, "合成贸易")
        self.assertEqual(book["账龄分析表"]["A9"].value, "合成贸易")

    def test_parent_and_child_are_not_summed_twice(self):
        data = self.data([self.party("C01", "合成贸易", "30"),
                          self.party("C02", "合成建设", "20")])
        book = self.build(data.rows, paper_data=data)
        detail = book["1-070 明细表"]
        markers = [str(detail.cell(number, 1).value) for number in range(8, 10)]
        self.assertTrue(all(marker.startswith("辅助核算:112201/001/") for marker in markers))
        values = [str(detail.cell(number, 1).value) for number in range(1, detail.max_row + 1)]
        self.assertNotIn("1:K1122", values)
        self.assertEqual(sum(value == "应收账款小计" for value in values), 1)
        self.assertEqual([book["1-070 审定表"].cell(number, 2).value for number in range(9, 10)],
                         ["应收账款"])

    def test_auxiliary_and_ledger_difference_is_kept(self):
        data = self.data([self.party("C01", "合成贸易", "30")],
                         difference=(Decimal("45"), Decimal("55")))
        book = self.build(data.rows, paper_data=data)
        check = book["客商明细核对"]
        self.assertEqual([check.cell(8, column).value for column in (1, 3, 4, 5)],
                         ["112201", "001", 45, 55])
        self.assertEqual(check["F8"].value, "差异，待核实")

    def test_missing_customer_balance_stays_blank(self):
        data = self.data([self.party("C01", "合成贸易", "30"),
                          self.party("C02", "合成建设", None, opening="20")])
        book = self.build(data.rows, paper_data=data)
        detail = book["1-070 明细表"]
        missing = next(number for number in (8, 9) if detail.cell(number, 4).value == "合成建设")
        self.assertEqual(detail.cell(missing, 15).value, None)
        self.assertEqual(detail.cell(missing, 6).value, 20)
        self.assertIn("COUNT(F8:F9)=2", detail["F10"].value)
        aging = next(number for number in (8, 9)
                     if book["账龄分析表"].cell(number, 1).value == "合成建设")
        self.assertIsNone(book["账龄分析表"].cell(aging, 2).value)


class ProfitLossOccurrenceTests(BuildCase):
    """损益只认独立发生额：借贷同额不能代替本期数，缺失一方不得以另一方推算差额。"""

    def source(self, **kwargs):
        values = dict(is_profit_loss=True, debit=Decimal(500), credit=Decimal(500),
                      opening=Decimal(0))
        values.update(kwargs)
        return row("6001", "主营业务收入", "0", "贷", **values)

    def test_equal_debit_and_credit_do_not_override_explicit_occurrence(self):
        months = tuple(Decimal(month) for month in range(1, 7)) + (None,) * 6
        source = self.source(period_amount=Decimal("7.2"),
                             previous_period_amount=Decimal(6), monthly_amounts=months)
        book = self.build([source])
        self.assertEqual(book["4-010 审定表"]["D9"].value, 7.2)
        self.assertEqual(book["4-010 审定表"]["M9"].value, 6)
        self.assertEqual([book["4-010 明细表"].cell(8, column).value for column in range(6, 18)],
                         [1, 2, 3, 4, 5, 6] + [None] * 6)
        self.assertEqual(book["4-010 明细表"]["R8"].value, 7.2)
        self.assertEqual(book["4-010 明细表"]["V8"].value, 6)

    def test_missing_current_occurrence_with_prior_present_stays_blank(self):
        source = self.source(previous_period_amount=Decimal(6))
        book = self.build([source])
        audit = book["4-010 审定表"]
        self.assertIsNone(audit["D9"].value)
        self.assertEqual(audit["M9"].value, 6)
        self.assertIsNone(audit["R9"].value)
        detail = book["4-010 明细表"]
        self.assertIsNone(detail["R8"].value)
        self.assertEqual(detail["V8"].value, 6)
        self.assertIsNone(dxn_paper.totals([source], "贷")["period"])


if __name__ == "__main__":
    unittest.main()
