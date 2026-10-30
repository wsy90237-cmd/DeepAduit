"""Synthetic regressions for template semantics, without customer data."""
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openpyxl import Workbook, load_workbook

from tb_autofill import fields, gui, matching, workbook
from tb_autofill.models import LedgerRow, MatchResult


class OpeningProfitTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory(dir=".")
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "synthetic.xlsx"

    def scan(self, *, formula=False, opening=False):
        book = Workbook()
        sheet = book.active
        sheet.title = "试算平衡表(期初)" if opening else "试算平衡表(期末)"
        sheet["A1"] = sheet.title
        sheet["A3"] = "利润表试算平衡表"
        sheet["A6"] = "报表项目"
        sheet["D6"] = "上期审定数" if opening else "期末未审数"
        sheet["A8"] = "利润分配"
        sheet["A9"] = "加：（一）年初未分配利润"
        if formula:
            sheet["D9"] = "=1+1"
        book.save(self.path)
        book.close()
        scan = fields.scan_template(self.path, ("opening",) if opening else ("ending",), "元")
        return scan, next(f for f in scan.fields if f.cell == "D9")

    @staticmethod
    def sources(opening="11"):
        parent = LedgerRow("4104", "利润分配", direction="贷", ending=Decimal(70))
        detail = LedgerRow("410401", "未分配利润", path="利润分配/未分配利润", level=2,
                           direction="贷", opening=None if opening is None else Decimal(opening),
                           ending=Decimal(70), debit=Decimal(3), credit=Decimal(5))
        return [parent, detail]

    def test_current_period_profit_distribution_uses_opening_balance(self):
        scan, target = self.scan()
        self.assertEqual((target.period, target.basis, target.measure), ("ending", "book", "opening_balance"))
        rows = self.sources()
        result = matching.match_fields([target], rows)[0]
        self.assertEqual((result.status, result.amount, result.source_ids), ("matched", Decimal(11), ["410401"]))
        self.assertEqual(workbook.planned_writes(scan, [result], rows=rows), [(target, Decimal(11))])
        self.assertEqual(matching.resolve_match(target, [rows[1]], rows=rows).amount, Decimal(11))

    def test_unique_standalone_account_and_negative_opening(self):
        _, target = self.scan()
        row = replace(self.sources("-11")[1], code="4104", path="未分配利润", level=1)
        result = matching.match_fields([target], [row])[0]
        self.assertEqual((result.status, result.amount, result.source_ids), ("matched", Decimal(-11), ["4104"]))
        debit = replace(row, direction="借", opening=Decimal(11))
        self.assertEqual(matching.match_fields([target], [debit])[0].amount, Decimal(-11))

    def test_opening_balance_cannot_be_reused_for_other_labels_or_forged_path(self):
        _, target = self.scan()
        parent, detail = self.sources()
        unrelated = replace(target, label="营业收入", measure="opening_balance")
        self.assertNotEqual(matching.resolve_match(unrelated, [detail], rows=[parent, detail]).status, "matched")
        forged_path = replace(detail, code="999901")
        self.assertNotEqual(matching.resolve_match(target, [forged_path], rows=[parent, forged_path]).status, "matched")
        self.assertNotEqual(matching.match_fields([target], [parent, forged_path])[0].status, "matched")
        for altered in (replace(target, period="opening"), replace(target, basis="audited"),
                        replace(target, statement="balance_sheet"), replace(target, occurrence_scope="month"),
                        replace(target, presentation_direction="借")):
            self.assertNotEqual(matching.resolve_match(altered, [detail], rows=[parent, detail]).status, "matched")
        self.assertNotEqual(matching.resolve_match(target, [detail], rows=[detail]).status, "matched")

    def test_missing_opening_wrong_path_and_profit_loss_are_not_used(self):
        scan, target = self.scan()
        parent, detail = self.sources()
        wrong = replace(detail, code="500101", path="管理费用/未分配利润", is_profit_loss=True)
        other = replace(detail, code="400101", path="其他权益/未分配利润")
        for rows, selected in (([parent, replace(detail, opening=None)], replace(detail, opening=None)),
                               ([parent, wrong], wrong), ([parent, other], other)):
            with self.subTest(path=selected.path, opening=selected.opening):
                result = matching.match_fields([target], rows)[0]
                self.assertNotEqual(result.status, "matched")
                self.assertFalse(result.selected)
                self.assertNotEqual(matching.resolve_match(target, [selected], rows=rows).status, "matched")
                forged = MatchResult(target, [selected], Decimal(11), status="matched", method="exact", selected=True)
                with self.assertRaises(ValueError):
                    workbook.planned_writes(scan, [forged], rows=rows)

    def test_ambiguous_candidates_cannot_be_selected_by_ai_or_forged_result(self):
        scan, target = self.scan()
        parent, detail = self.sources()
        other = replace(detail, code="410402")
        rows = [parent, detail, other]
        results = matching.match_fields([target], rows)
        self.assertEqual(results[0].status, "ambiguous")
        self.assertNotEqual(matching.resolve_match(target, [detail], method="manual", rows=rows).status, "matched")
        applied, _ = matching.apply_ai_suggestions(results, [{"target_id": target.id, "source_codes": [detail.code]}], rows)
        self.assertEqual(applied, 0)
        forged = MatchResult(target, [detail], Decimal(11), status="matched", method="exact", selected=True)
        with self.assertRaises(ValueError):
            workbook.planned_writes(scan, [forged], rows=rows)

    def test_formula_and_audited_opening_are_protected(self):
        _, target = self.scan(formula=True)
        self.assertEqual(matching.match_fields([target], self.sources())[0].status, "protected")
        _, opening = self.scan(opening=True)
        self.assertEqual(opening.basis, "audited")
        self.assertEqual(matching.resolve_match(opening, [self.sources()[1]]).status, "basis_mismatch")

    def test_distinct_opening_and_ending_claims_are_not_duplicates(self):
        scan, opening = self.scan()
        ending = replace(opening, cell="D10", label="未分配利润", parent="",
                         measure="balance", statement="balance_sheet", presentation_direction="贷")
        row = replace(self.sources()[1], code="4104", path="未分配利润", level=1)
        scan.fields = [opening, ending]
        results = matching.match_fields(scan.fields, [row])
        self.assertEqual([(r.status, r.amount) for r in results],
                         [("matched", Decimal(11)), ("matched", Decimal(70))])
        self.assertEqual([amount for _, amount in workbook.planned_writes(scan, results, rows=[row])],
                         [Decimal(11), Decimal(70)])
        duplicate = replace(opening, cell="D11")
        scan.fields = [opening, duplicate]
        results = matching.match_fields(scan.fields, [row])
        self.assertEqual([r.status for r in results], ["duplicate", "duplicate"])
        matching.apply_ai_suggestions(results, [{"target_id": field.id, "source_codes": [row.code]}
                                                for field in scan.fields], [row])
        self.assertEqual([r.status for r in results], ["duplicate", "duplicate"])
        self.assertFalse(any(r.selected for r in results))
        forged = [MatchResult(field, [row], Decimal(11), status="matched", method="manual", selected=True)
                  for field in scan.fields]
        with self.assertRaises(ValueError):
            workbook.planned_writes(scan, forged, rows=[row])

    def test_two_profit_distribution_roots_cannot_supply_opening_profit(self):
        scan, target = self.scan()
        parent, detail = self.sources()
        second = replace(parent, code="4199")
        rows = [parent, second, detail]
        results = matching.match_fields([target], rows)
        self.assertEqual((results[0].status, results[0].amount), ("ambiguous", None))
        self.assertFalse(results[0].selected)
        self.assertNotEqual(matching.resolve_match(target, [detail], method="manual", rows=rows).status, "matched")
        forged = MatchResult(target, [detail], Decimal(11), status="matched", method="manual", selected=True)
        with self.assertRaises(ValueError):
            workbook.planned_writes(scan, [forged], rows=rows)

    def test_forged_opening_amount_cannot_pass_write_review(self):
        scan, target = self.scan()
        parent, detail = self.sources()
        # 期末余额 70 与年初余额 11 不同：金额只能在 resolve_match 内产生，伪造的金额必须被复核拒绝。
        forged = MatchResult(target, [detail], Decimal(70), status="matched", method="exact", selected=True)
        with self.assertRaisesRegex(ValueError, "未通过写入前复核"):
            workbook.planned_writes(scan, [forged], rows=[parent, detail])
        self.assertEqual(workbook.planned_writes(
            scan, [MatchResult(target, [detail], Decimal(11), status="matched", method="exact", selected=True)],
            rows=[parent, detail])[0][1], Decimal(11))

    def test_opening_profit_follows_moved_split_headers_units_and_missing_values(self):
        for dr, dc, split in ((0, 0, False), (37, 3, True), (101, 8, False)):
            for value in (None, "0", "-25", "25"):
                with self.subTest(offset=(dr, dc), split=split, value=value):
                    book = Workbook()
                    sheet = book.active
                    sheet.title = "迁移页"
                    for row, col, text in ((1, 1, "试算平衡表(期末)"), (2, 1, "金额单位：万元"),
                                           (3, 1, "利润表试算平衡表"), (6, 1, "报表项目"),
                                           (6, 4, "期末" if split else "期末未审数"),
                                           (8, 1, "利润分配"), (9, 1, "加：（一）年初未分配利润")):
                        sheet.cell(row + dr, col + dc, text)
                    if split:
                        sheet.cell(7 + dr, 4 + dc, "未审数")
                    book.save(self.path); book.close()
                    scan = fields.scan_template(self.path)
                    target = next(f for f in scan.fields if "年初未分配利润" in f.label)
                    rows = self.sources(value)
                    result = matching.match_fields([replace(target, value=None)], rows)[0]
                    self.assertEqual(target.measure, "opening_balance")
                    self.assertEqual(result.status, "missing" if value is None else "matched")
                    self.assertEqual(result.amount, None if value is None else Decimal(value) / 10000)


class TemplatePeriodTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory(dir=".")
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "synthetic.xlsx"

    def scan(self, date="截止日期：2025-12-31", *, second=None, opening=False, generic=False):
        book = Workbook()
        sheet = book.active
        sheet.title = "通用表" if generic else "试算平衡表(期初)" if opening else "试算平衡表(期末)"
        if generic:
            sheet["A1"] = "科目名称"; sheet["B1"] = "期末余额"; sheet["A2"] = "银行存款"
        else:
            sheet["A1"] = sheet.title
            if isinstance(date, datetime):
                sheet["A2"] = "截止日期："; sheet["B2"] = date
            elif date is not None:
                sheet["A2"] = date
            if second is not None:
                sheet["A3"] = second
            sheet["A6"] = "报表项目"
            sheet["D6"] = "上期审定数" if opening else "期末未审数"
            sheet["A8"] = "流动资产："; sheet["A9"] = "银行存款"
        book.save(self.path); book.close()
        return fields.scan_template(self.path, ("opening",) if opening else ("ending",), "元")

    @staticmethod
    def rows(year=2025, month=12):
        return [LedgerRow("1002", "银行存款", path="银行存款", ending=Decimal(123),
                          subject_id="1", year=year, month=month, ledger_id="1")]

    def test_declared_date_binds_current_period_and_write_plan(self):
        for value in ("截止日期：2025-12-31", "截止日期：2025年12月31日", datetime(2025, 12, 31)):
            with self.subTest(date=value):
                scan = self.scan(value)
                self.assertEqual(scan.ending_periods, (("试算平衡表(期末)", 2025, 12),))
                good = self.rows()
                workbook.validate_source_context(scan, [], good)
                results = matching.match_fields(scan.fields, good)
                self.assertEqual(workbook.planned_writes(scan, results, rows=good)[0][1], Decimal(123))
                for rows in (self.rows(2024, 12), self.rows(2025, 11), self.rows(0, 0)):
                    with self.assertRaises(ValueError):
                        workbook.validate_source_context(scan, [], rows)
                    with self.assertRaises(ValueError):
                        workbook.planned_writes(scan, matching.match_fields(scan.fields, rows), rows=rows)

    def test_invalid_conflicting_and_midmonth_dates_stop_scan(self):
        for first, second in (("截止日期：2025-12-15", None), ("截止日期：2025-02-30", None),
                              ("截止日期：未知日期", None),
                              ("截止日期：2025-12-31", "截止日期：2024-12-31")):
            with self.subTest(first=first, second=second), self.assertRaises(ValueError):
                self.scan(first, second=second)

    def test_undeclared_date_and_opening_sheet_remain_unbound(self):
        for scan in (self.scan(generic=True), self.scan("截止日期："), self.scan(opening=True)):
            self.assertEqual(scan.ending_periods, ())
            workbook.validate_source_context(scan, [], self.rows(2024, 12))

    def test_preview_worker_rejects_wrong_period_before_matching(self):
        self.scan()
        capture = Mock()
        app = SimpleNamespace(busy=False, template_var=SimpleNamespace(get=lambda: str(self.path)),
                              unit_var=SimpleNamespace(get=lambda: "元"), rows=self.rows(2024, 12),
                              _periods=lambda: ("ending",), _invalidate_review=Mock(),
                              _async=capture, _on_scanned=Mock())
        gui.App._recognize(app)
        work = capture.call_args.args[1]
        with patch.object(matching, "match_fields") as match, self.assertRaises(ValueError):
            work()
        match.assert_not_called()

    def test_year_to_date_metadata_and_mixed_source_periods(self):
        for value, year, month in (("会计期间：2026年1-6月", 2026, 6),
                                   ("截止日：2024年2月29日", 2024, 2)):
            scan = self.scan(value)
            self.assertEqual(scan.ending_periods, (("试算平衡表(期末)", year, month),))
            workbook.validate_source_context(scan, [], self.rows(year, month))
            with self.assertRaises(ValueError):
                workbook.validate_source_context(scan, [], self.rows(year, month) + self.rows(2023, 12))
            with self.assertRaises(ValueError):
                workbook.validate_source_context(scan, [], None)
        for value in ("会计期间：2026年7-9月", "截止日：2023年2月29日"):
            with self.assertRaises(ValueError):
                self.scan(value)

    def test_moved_date_and_internal_reference_are_bound_without_fixed_coordinates(self):
        self.scan()
        book = load_workbook(self.path)
        sheet = book.active
        sheet["A2"] = "截止日期："
        sheet["B2"] = "='元数据'!A1"
        metadata = book.create_sheet("元数据")
        metadata["A1"] = "2025-12-31"
        sheet.insert_rows(1, 37); sheet.insert_cols(1, 3)
        book.save(self.path); book.close()
        scan = fields.scan_template(self.path)
        self.assertEqual(scan.ending_periods, (("试算平衡表(期末)", 2025, 12),))
        with self.assertRaises(ValueError):
            workbook.validate_source_context(scan, [], self.rows(2024, 12))

    def test_unselected_ending_period_does_not_bind_opening_only_import(self):
        self.scan()
        book = load_workbook(self.path)
        opening = book.create_sheet("试算平衡表(期初)")
        opening.append(["报表项目", "上期审定数"])
        opening.append(["银行存款", None])
        book.save(self.path); book.close()
        scan = fields.scan_template(self.path, ("opening",), "元")
        self.assertEqual(scan.ending_periods, ())
        workbook.validate_source_context(scan, [], self.rows(2024, 12))

    def save_and_scan(self, book):
        book.save(self.path)
        book.close()
        return fields.scan_template(self.path)

    def test_date_concatenation_and_cross_sheet_datetime_bind_before_writing(self):
        for expression in ('="截止日期：" & D2 & E2 & F2 & G2 & H2 & I2',
                           "='元数据'!A1", "='元数据'!A1:A1"):
            with self.subTest(expression=expression):
                self.scan()
                book = load_workbook(self.path)
                sheet = book.active
                metadata = book.create_sheet("元数据")
                metadata["A1"] = datetime(2025, 12, 31)
                if expression.startswith('="'):
                    sheet["A2"] = expression
                    for col, value in enumerate((2025, "年", 12, "月", 31, "日"), 4):
                        sheet.cell(2, col, value)
                else:
                    sheet["A2"] = "截止日期："
                    sheet["B2"] = expression
                scan = self.save_and_scan(book)
                self.assertEqual(scan.ending_periods, ((sheet.title, 2025, 12),))
                bad_rows = self.rows(2026, 10)
                with self.assertRaises(ValueError):
                    workbook.planned_writes(scan, matching.match_fields(scan.fields, bad_rows), rows=bad_rows)

    def test_unsupported_date_formula_cannot_be_empty_or_partially_resolved(self):
        for expression in ('="截止日期："&TEXT(B2,"yyyy年mm月dd日")',
                           '="截止日期：2025-12-31"&Missing!A1',
                           '="截止日期：2025-12-31"&A2'):
            with self.subTest(expression=expression):
                self.scan()
                book = load_workbook(self.path)
                book.active["A2"] = expression
                book.active["B2"] = datetime(2025, 12, 31)
                with self.assertRaisesRegex(ValueError, "截止日期公式无法解析"):
                    self.save_and_scan(book)

    def test_unknown_neighbor_date_cannot_borrow_later_date(self):
        self.scan()
        book = load_workbook(self.path)
        sheet = book.active
        sheet["A2"] = "截止日期："
        sheet["B2"] = '=TEXT(\'元数据\'!A1,"yyyy年mm月dd日")'
        sheet["C2"] = "2026年10月31日"
        book.create_sheet("元数据")["A1"] = datetime(2025, 12, 31)
        with self.assertRaisesRegex(ValueError, "截止日期公式无法解析"):
            self.save_and_scan(book)

    def test_cross_sheet_unknown_date_keeps_metadata_hint(self):
        self.scan()
        book = load_workbook(self.path)
        book.active["A2"] = "='元数据'!A1"
        book.create_sheet("元数据")["A1"] = '="截止日期："&TEXT(B1,"yyyy年mm月dd日")'
        with self.assertRaisesRegex(ValueError, "截止日期公式无法解析"):
            self.save_and_scan(book)

    def test_concatenated_date_conflict_is_not_hidden(self):
        self.scan()
        book = load_workbook(self.path)
        sheet = book.active
        sheet["A2"] = '="截止日期："&B2&C2'
        sheet["B2"] = "2025-12-"
        sheet["C2"] = "31"
        sheet["A3"] = "截止日期：2026-10-31"
        with self.assertRaisesRegex(ValueError, "截止日期存在冲突"):
            self.save_and_scan(book)

    def test_concatenated_units_do_not_fall_back_to_yuan(self):
        self.scan()
        book = load_workbook(self.path)
        sheet = book.active
        sheet["A3"] = '="金额单位：" & B3 & C3'
        sheet["B3"] = "万"
        sheet["C3"] = "元"
        scan = self.save_and_scan(book)
        self.assertEqual(scan.unit, "万元")
        rows = self.rows()
        result = matching.match_fields(scan.fields, rows)[0]
        self.assertEqual(result.amount, Decimal("0.0123"))
        self.assertEqual(workbook.planned_writes(scan, [result], rows=rows)[0][1], Decimal("0.0123"))

    def test_unknown_unit_dependencies_cannot_look_like_valid_partial_unit(self):
        for label, expression in (('="金额单位：万元"&Missing!A1', None),
                                  ("金额单位：", '=IF(TRUE,"万元","")')):
            with self.subTest(label=label):
                self.scan()
                book = load_workbook(self.path)
                book.active["A3"] = label
                book.active["B3"] = expression
                book.active["C3"] = "元"
                with self.assertRaisesRegex(ValueError, "金额单位公式无法解析"):
                    self.save_and_scan(book)

    def test_explicit_unit_overrides_unknown_unit_formula_without_disabling_date_guard(self):
        self.scan()
        book = load_workbook(self.path)
        book.active["A3"] = '="金额单位："&IF(TRUE,"万元","")'
        book.save(self.path)
        book.close()
        for unit, factor in (("自动", None), ("元", Decimal(1)), ("万元", Decimal(10000))):
            with self.subTest(unit=unit):
                if factor is None:
                    with self.assertRaisesRegex(ValueError, "金额单位公式无法解析"):
                        fields.scan_template(self.path, unit=unit)
                    continue
                scan = fields.scan_template(self.path, unit=unit)
                self.assertEqual((scan.unit, scan.fields[0].unit_factor), (unit, factor))
                rows = self.rows()
                result = matching.match_fields(scan.fields, rows)[0]
                self.assertEqual(workbook.planned_writes(scan, [result], rows=rows)[0][1], Decimal(123) / factor)
                with self.assertRaises(ValueError):
                    workbook.validate_source_context(scan, [], self.rows(2026, 10))

    def test_unrelated_formula_and_literal_ampersand_remain_safe(self):
        self.scan()
        book = load_workbook(self.path)
        sheet = book.active
        sheet["B3"] = "=SUM(1,2)"
        self.assertEqual(fields._unit_text(book, sheet, '="A&B" & "C"'), "A&BC")
        self.assertEqual(fields._unit_text(book, sheet, '="0=" & C3'), "0=")
        sheet["C3"] = 0
        self.assertEqual(fields._unit_text(book, sheet, '="0=" & C3'), "0=0")
        scan = self.save_and_scan(book)
        self.assertEqual(scan.ending_periods, ((sheet.title, 2025, 12),))


if __name__ == "__main__":
    unittest.main()
