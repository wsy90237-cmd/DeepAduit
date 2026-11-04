"""一级科目入口、二级明细、请求预览与真实 Tk/Excel 导出；仅合成数据。"""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openpyxl import load_workbook

from tb_autofill import audit_index, gui, papers
from tb_autofill.models import LedgerRow, Subject


def ledger():
    root = LedgerRow("1002", "银行存款", path="银行存款", level=1, opening=Decimal(100),
                     ending=Decimal(100), debit=Decimal(0), credit=Decimal(0),
                     subject_id="synthetic", year=2026, month=6, ledger_id="1")
    first = replace(root, code="100201", name="合成银行甲", path="银行存款/合成银行甲",
                    level=2, opening=Decimal(60), ending=Decimal(60))
    second = replace(first, code="100202", name="合成银行乙", path="银行存款/合成银行乙",
                     opening=Decimal(40), ending=Decimal(40))
    leaf = replace(first, code="10020101", name="合成分户", path=first.path + "/合成分户", level=3)
    other = replace(root, code="1001", name="库存现金", path="库存现金", opening=Decimal(999), ending=Decimal(999))
    return [root, first, second, leaf, other]


class PrimaryScopeTests(unittest.TestCase):
    def test_primary_choices_keep_zero_balance_roots(self):
        rows = [replace(row, opening=Decimal(0), ending=Decimal(0)) for row in ledger()]
        self.assertEqual({code for code, _label in papers.account_choices(rows, level=1, limit=None)}, {"1001", "1002"})

    def test_scope_contains_one_primary_and_its_descendants(self):
        root, scoped = papers.primary_scope(ledger(), "1002")
        self.assertEqual(root.code, "1002")
        self.assertEqual({row.code for row in scoped}, {"1002", "100201", "100202", "10020101"})

    def test_secondary_cannot_be_selected_as_primary(self):
        with self.assertRaisesRegex(ValueError, "一级科目"):
            papers.primary_scope(ledger(), "100201")

    def test_prepared_model_request_excludes_other_primary_accounts(self):
        request = papers.pack_request(rows=ledger(), code="1002", paper=audit_index.resolve("1002", "银行存款")[0],
                                      procedures=["核对科目余额"], subject_label="合成企业 / 2026-06")
        self.assertNotIn("库存现金", str(request["context"]["facts"]))
        self.assertNotIn("999", str(request["context"]["facts"]))

    def test_cancelled_sheet_suggestion_does_not_contact_model(self):
        client = Mock()
        result = papers.suggest_sheets(client, name="银行存款", accounts=ledger()[:1],
                                       available=["银行账户清单"], confirm=lambda request, service: False)
        client.converse.assert_not_called()
        client.converse_with_skills.assert_not_called()
        self.assertIn("取消", result["note"])


class PaperUiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        def isolated_settings(app):
            app.store, app._pending_error = None, ""
        with patch.object(gui.App, "_load_settings", isolated_settings), \
                patch.object(gui.App, "_autodetect"), \
                patch.object(gui.knowledge, "LocalLibrary"), \
                patch.object(gui.auditlog, "AuditLog"):
            self.app = gui.App()
            self.app.withdraw()
        self.addCleanup(self.app.destroy)
        self.app.audit.verify.return_value = (True, 0, "")
        self.app.rows = ledger()
        self.app.loaded_subject = Subject("synthetic", "合成企业", 2026, 6, "synthetic", ledger_id="1")

    def dialog(self):
        dialog = gui.PaperDialog(self.app)
        dialog.withdraw()
        dialog.with_journal.set(False)
        dialog.code_box.current(next(index for index, item in enumerate(dialog.choices) if item[0] == "1002"))
        dialog._sync_paper()
        return dialog

    def test_workspace_button_opens_paper_dialog_without_api_key(self):
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        buttons = [widget for widget in descendants(self.app.pages["papers"])
                   if isinstance(widget, gui.ttk.Button) and widget.cget("text") == "制作底稿（一级科目）"]
        self.assertEqual(len(buttons), 1)
        with patch.object(gui, "PaperDialog") as opened:
            buttons[0].invoke()
        opened.assert_called_once_with(self.app, "paper")

    def test_dialog_only_lists_primary_and_shows_direct_secondary(self):
        dialog = self.dialog()
        self.assertEqual({code for code, _label in dialog.choices}, {"1001", "1002"})
        values = [dialog.children_tree.item(item, "values") for item in dialog.children_tree.get_children()]
        self.assertEqual([(row[0], row[1]) for row in values], [("100201", "合成银行甲"), ("100202", "合成银行乙")])

    def test_missing_primary_does_not_fall_back_to_secondary(self):
        self.app.rows = ledger()[1:4]
        dialog = gui.PaperDialog(self.app)
        dialog.withdraw()
        self.assertEqual(dialog.choices, [])
        self.assertIn("未识别到一级科目", dialog.state.get())

    def test_all_secondary_accounts_are_included_beyond_twenty_rows(self):
        root = replace(ledger()[0], opening=Decimal(25), ending=Decimal(25))
        children = [replace(root, code=f"1002{index:02}", name=f"合成银行{index}", path=f"银行存款/合成银行{index}",
                            level=2, opening=Decimal(1), ending=Decimal(1)) for index in range(1, 26)]
        self.app.rows = [root, *children]
        dialog = self.dialog()
        self.assertEqual(len(dialog.children_tree.get_children()), 25)
        path = Path(self.folder.name) / "synthetic-many.xlsx"
        with patch.object(gui.filedialog, "asksaveasfilename", return_value=str(path)):
            dialog._export()
        book = load_workbook(path)
        self.addCleanup(book.close)
        catalog_codes = {row[0].value for row in book["底稿目录"]}
        self.assertTrue({child.code for child in children} <= catalog_codes)

    def test_export_contains_only_selected_primary_and_secondary(self):
        dialog = self.dialog()
        path = Path(self.folder.name) / "synthetic.xlsx"
        with patch.object(gui.filedialog, "asksaveasfilename", return_value=str(path)):
            dialog._export()
        book = load_workbook(path)
        self.addCleanup(book.close)
        catalog = " ".join(str(cell.value) for row in book["底稿目录"] for cell in row if cell.value is not None)
        self.assertIn("一级科目：1002 银行存款", catalog)
        self.assertIn("包含的二级科目（2 项", catalog)
        self.assertIn("合成银行甲", catalog)
        self.assertIn("合成银行乙", catalog)
        detail = book["1-010 明细表"]
        codes = [row[1].value for row in detail if row[0].value and str(row[0].value).startswith("1:K")]
        self.assertEqual(codes, ["1002.01", "1002.02"])
        self.assertNotIn("库存现金监盘表", book.sheetnames)
        self.assertEqual(book["1-010 审定表"]["D9"].value, 100)
        self.assertIsNone(book["1-010 审定表"]["K9"].value)

    def test_cancelled_preview_starts_no_request(self):
        dialog = self.dialog()
        client = Mock(model="synthetic", base_url="https://model.invalid/v1")
        request = {"question": "合成问题", "context": {"facts": {"subject": {"code": "1002"}}}}
        dialog.queue.put(("preview", (request, None, "1002", client)))
        dialog.running = True
        with patch.object(gui, "PayloadDialog", return_value=SimpleNamespace(confirmed=False)), \
                patch.object(dialog, "wait_window"):
            dialog._poll()
        client.converse.assert_not_called()
        self.assertIsNone(dialog.draft)

    def test_changed_source_during_preview_starts_no_request(self):
        dialog = self.dialog()
        client = Mock(model="synthetic", base_url="https://model.invalid/v1")
        def change(_dialog):
            self.app._context_revision += 1
        with patch.object(gui, "PayloadDialog", return_value=SimpleNamespace(confirmed=True)), \
                patch.object(dialog, "wait_window", side_effect=change):
            self.assertFalse(dialog._confirm_request({"question": "合成问题", "context": {}}, client, "1002"))
        client.converse.assert_not_called()

    def test_confirmed_request_sends_exactly_the_previewed_context(self):
        dialog = self.dialog()
        self.app.store = SimpleNamespace(data={"ai_enabled": True})
        client = Mock(model="synthetic", base_url="https://model.invalid/v1")
        client.converse.return_value = {"intent": "audit", "answer": '{"audit_notes":["待补合成证据"]}'}
        with patch.object(gui, "PayloadDialog", return_value=SimpleNamespace(confirmed=True)) as preview, \
                patch.object(dialog, "wait_window"), \
                patch.object(gui.ai_module.DeepSeekClient, "from_store", return_value=client), \
                patch.object(gui.threading, "Thread", side_effect=lambda target, daemon: SimpleNamespace(start=target)):
            dialog._generate()
            client.converse.assert_not_called()
            dialog._poll()
        payload = preview.call_args.args[1]
        client.converse.assert_called_once_with(payload["question"], context=payload["context"])
        client.converse_with_skills.assert_not_called()
        dialog._poll()
        self.assertEqual(dialog.draft["subject"]["code"], "1002")
