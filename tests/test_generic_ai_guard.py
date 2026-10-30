"""Synthetic ordinary templates must retain semantic checks through export."""
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from tb_autofill import fields, matching, workbook
from tb_autofill.models import LedgerRow, MatchResult, TargetField


class GenericAiGuardTests(unittest.TestCase):
    def test_scanned_code_must_match_text_and_write_guard(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.append(["科目名称", "索引号", "期末余额"])
            sheet.append(["应收账款", "1002", None])
            book.save(path)
            book.close()
            scan = fields.scan_template(path, ("ending",))
            target = next(field for field in scan.fields if field.label == "应收账款")
            bank = LedgerRow("1002", "银行存款", path="银行存款", ending=Decimal(900))
            result = matching.match_fields([target], [bank])[0]
            self.assertNotEqual(result.status, "matched")
            self.assertFalse(result.selected)
            forged = MatchResult(target, sources=[bank], amount=Decimal(900), status="matched",
                                 method="code", selected=True)
            with self.assertRaisesRegex(ValueError, "未通过写入前复核"):
                workbook.planned_writes(scan, [forged], rows=[bank])
            self.assertNotEqual(matching.resolve_match(target, [bank], method="code").status, "matched")
            receivable = LedgerRow("1002", "应收账款", path="应收账款", ending=Decimal(42))
            valid = matching.match_fields([target], [receivable])[0]
            self.assertEqual((valid.status, valid.amount, valid.method), ("matched", Decimal(42), "code"))
            self.assertEqual(len(workbook.planned_writes(scan, [valid], rows=[receivable])), 1)

    def test_scan_ai_and_write_guard_reject_unrelated_source(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.append(["科目名称", "期末余额"])
            sheet.append(["应收账款", None])
            book.save(path)
            book.close()
            scan = fields.scan_template(path, ("ending",))
            target = next(field for field in scan.fields if field.label == "应收账款")
            self.assertEqual(target.statement, "")
            bank = LedgerRow("1002", "银行存款", path="银行存款", ending=Decimal(900))
            rows = [bank]
            results = matching.match_fields([target], rows)
            self.assertEqual(results[0].status, "unmatched")
            applied, _ = matching.apply_ai_suggestions(results, [{"target_id": target.id,
                "source_codes": ["1002"], "reason": "合成测试"}], rows)
            self.assertEqual(applied, 0)
            forged = MatchResult(target, sources=rows, amount=Decimal(900), status="matched",
                                 method="ai", selected=True)
            with self.assertRaisesRegex(ValueError, "未通过写入前复核"):
                workbook.planned_writes(scan, [forged], rows=rows)

    def test_ai_accepts_only_unique_source_in_confirmed_parent(self):
        target = TargetField("普通模板", "B2", "其他", "ending", parent="管理费用")
        parent = LedgerRow("6602", "管理费用", path="管理费用")
        right = LedgerRow("660201", "其他", path="管理费用/其他", level=2, ending=Decimal(10))
        wrong = LedgerRow("660301", "其他", path="财务费用/其他", level=2, ending=Decimal(20))
        rows = [parent, right, wrong]
        self.assertEqual(matching.resolve_match(target, [right], method="ai", rows=rows).amount, Decimal(10))
        self.assertNotEqual(matching.resolve_match(target, [right], method="ai").status, "matched")
        self.assertNotEqual(matching.resolve_match(target, [wrong], method="ai", rows=rows).status, "matched")
        duplicate = LedgerRow("660202", "其他", path="管理费用/其他", level=2, ending=Decimal(30))
        self.assertEqual(matching.resolve_match(target, [right], method="ai", rows=rows + [duplicate]).status, "ambiguous")
        missing_parent = TargetField("普通模板", "B3", "其他", "ending", parent="不存在的父科目")
        self.assertNotEqual(matching.resolve_match(missing_parent, [right], method="ai", rows=rows).status, "matched")

    def test_manual_selection_retains_existing_behavior(self):
        target = TargetField("普通模板", "B2", "应收账款", "ending")
        bank = LedgerRow("1002", "银行存款", ending=Decimal(900))
        self.assertEqual(matching.resolve_match(target, [bank], method="manual", rows=[bank]).amount, Decimal(900))


if __name__ == "__main__":
    unittest.main()
