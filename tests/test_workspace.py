import tkinter as tk
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from tb_autofill import gui, workspace
from tb_autofill.models import MatchResult, TargetField


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.interpreter = tk.Tcl()
        variable = lambda value="": tk.StringVar(master=self.interpreter, value=value)
        self.app = SimpleNamespace(
            rows=[], results=[], scan=None, loaded_subject=None, review_document=None,
            page_context=variable(), overview_meta=variable(), overview_entity=variable(),
            home_continue=Mock(), home_read=Mock(), current_subject=None, busy=False,
            review_status=variable(),
            template_var=variable(), _subject_label=lambda: "合成企业 / 2026-06" if self.app.loaded_subject else "",
        )

    def test_navigation_retains_available_work_and_removes_placeholder_pages(self):
        self.assertEqual(list(workspace.PAGES),
                         ["tb", "agent", "papers", "notes", "report", "security"])
        self.assertEqual(workspace.PAGES["tb"], "工作台")
        self.assertEqual(workspace.PAGES["agent"], "审计对话")
        self.assertEqual(workspace.PAGES["security"], "设置")

    def test_home_requires_reading_the_selected_ledger_and_excludes_tb_details(self):
        workspace.refresh_context(self.app)
        self.assertEqual(self.app.page_context.get(), "尚未读取账套")
        self.assertEqual(self.app.overview_entity.get(), "尚未读取")
        self.assertEqual(self.app.home_continue.configure.call_args.kwargs["state"], "disabled")
        self.assertEqual(self.app.home_read.configure.call_args.kwargs["state"], "disabled")

        self.app.loaded_subject = SimpleNamespace(name="合成企业", year=2026, month=6, ledger_id="01")
        self.app.current_subject = self.app.loaded_subject
        self.app.rows = [object(), object()]
        self.app.template_var.set("example.xlsx")
        workspace.refresh_context(self.app)
        self.assertEqual(self.app.page_context.get(), "合成企业 / 2026-06")
        self.assertEqual(self.app.overview_meta.get(), "2026-06 · 账套 01")
        self.assertEqual(self.app.home_continue.configure.call_args.kwargs["state"], "normal")
        self.assertEqual(self.app.home_read.configure.call_args.kwargs["state"], "normal")

        self.app.scan = SimpleNamespace(path=Path("example.xlsx"), unit="万元")
        target = TargetField("期末", "B2", "库存现金", "ending", value=5)
        self.app.results = [MatchResult(target, amount=Decimal("10"), status="matched", selected=True),
                            MatchResult(target, status="unmatched")]
        workspace.refresh_context(self.app)
        self.assertEqual(self.app.overview_meta.get(), "2026-06 · 账套 01")

        self.app.rows, self.app.results, self.app.scan, self.app.loaded_subject = [], [], None, None
        workspace.refresh_context(self.app)
        self.assertEqual(self.app.page_context.get(), "尚未读取账套")
        self.assertEqual(self.app.home_continue.configure.call_args.kwargs["state"], "disabled")

    def test_context_meta_omits_missing_fields_instead_of_placeholders(self):
        self.assertEqual(workspace.context_meta(None, "example.xlsx"), "")
        self.assertEqual(workspace.context_meta(SimpleNamespace(year=2026, month=6, ledger_id="")), "2026-06")
        self.assertEqual(workspace.context_meta(SimpleNamespace(year=2026, month=6, ledger_id="02")),
                         "2026-06 · 账套 02")

    def test_result_display_treats_whitespace_as_empty_but_keeps_zero(self):
        for value in (" ", "  ", "\t\n", "\N{NO-BREAK SPACE}"):
            with self.subTest(value=value):
                target = TargetField("期末", "B2", "库存现金", "ending", value=value)
                row = gui.App._row_values(None, MatchResult(target, status="unmatched"))
                self.assertEqual(row[6], "")
        zero = TargetField("期末", "B2", "库存现金", "ending", value=0)
        row = gui.App._row_values(None, MatchResult(zero, amount=Decimal(0), status="matched"))
        self.assertEqual((row[5], row[6], row[7]), ("0.00", "0.00", "0.00"))


if __name__ == "__main__":
    unittest.main()
