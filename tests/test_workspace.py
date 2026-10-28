import tkinter as tk
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from tb_autofill import gui, workspace
from tb_autofill.models import MatchResult, TargetField


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.interpreter = tk.Tcl()
        variable = lambda value="": tk.StringVar(master=self.interpreter, value=value)
        self.app = SimpleNamespace(
            rows=[], results=[], scan=None, loaded_subject=None, review_document=None,
            overview_values=[variable() for _ in range(4)], page_context=variable(),
            overview_template=variable(), overview_notice=variable(), review_status=variable(),
            template_var=variable(), _subject_label=lambda: "合成企业 / 2026-06" if self.app.loaded_subject else "",
        )

    def test_navigation_retains_available_work_and_removes_placeholder_pages(self):
        self.assertEqual(list(workspace.PAGES), ["overview", "tb", "notes", "papers", "report", "knowledge", "security"])
        self.assertEqual(workspace.PAGES["overview"], "工作台")

    def test_home_reflects_real_work_state_and_invalidation(self):
        workspace.refresh_context(self.app)
        self.assertEqual([item.get() for item in self.app.overview_values], ["—"] * 4)
        self.assertIn("读取科目余额", self.app.overview_notice.get())

        self.app.loaded_subject = object()
        self.app.rows = [object(), object()]
        self.app.template_var.set("example.xlsx")
        workspace.refresh_context(self.app)
        self.assertEqual(self.app.overview_values[0].get(), "2")
        self.assertIn("待识别", self.app.overview_template.get())
        self.assertIn("识别字段", self.app.overview_notice.get())

        self.app.scan = SimpleNamespace(path=Path("example.xlsx"), unit="万元")
        target = TargetField("期末", "B2", "库存现金", "ending", value=5)
        self.app.results = [MatchResult(target, amount=Decimal("10"), status="matched", selected=True),
                            MatchResult(target, status="unmatched")]
        workspace.refresh_context(self.app)
        self.assertEqual([item.get() for item in self.app.overview_values], ["2", "2", "1", "1"])
        self.assertIn("单位：万元", self.app.overview_template.get())
        self.assertIn("待确认 1 项，现有值差异 1 项", self.app.overview_notice.get())

        self.app.rows, self.app.results, self.app.scan, self.app.loaded_subject = [], [], None, None
        workspace.refresh_context(self.app)
        self.assertEqual([item.get() for item in self.app.overview_values], ["—"] * 4)
        self.assertEqual(self.app.page_context.get(), "尚未读取账套")

    def test_result_display_treats_whitespace_as_empty_but_keeps_zero(self):
        for value in (" ", "  ", "\t\n", "\N{NO-BREAK SPACE}"):
            with self.subTest(value=value):
                target = TargetField("期末", "B2", "库存现金", "ending", value=value)
                row = gui.App._row_values(None, MatchResult(target, status="unmatched"))
                self.assertEqual(row[6], "")
        zero = TargetField("期末", "B2", "库存现金", "ending", value=0)
        row = gui.App._row_values(None, MatchResult(zero, amount=Decimal(0), status="matched"))
        self.assertEqual((row[5], row[6], row[7]), ("0", "0", "0"))


if __name__ == "__main__":
    unittest.main()
