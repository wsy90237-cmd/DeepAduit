"""一级科目入口、二级明细、请求预览与真实 Tk/Excel 导出；仅合成数据。"""

from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from openpyxl import load_workbook

from tb_autofill import ai, audit_index, gui, papers
from tb_autofill.models import JournalRow, JournalRows, LedgerRow, PaperData, Subject


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
        client.paper_text.assert_not_called()
        self.assertIn("取消", result["note"])

    def test_sheet_suggestion_uses_direct_json_and_exact_preview(self):
        calls, previews = [], []
        def transport(path, payload):
            calls.append(payload)
            return {"choices": [{"finish_reason": "stop", "message": {
                "content": '{"add":[{"name":"银行账户清单","reason":"核对账户范围"}],"remove":[]}'}}]}
        client = ai.DeepSeekClient("synthetic-key", transport=transport)
        result = papers.suggest_sheets(client, name="银行存款", accounts=ledger()[:1],
                                       available=["银行账户清单"],
                                       confirm=lambda request, service: previews.append(request) or True)
        self.assertEqual(result["add"][0]["name"], "银行账户清单")
        request = previews[0]
        self.assertEqual(calls[0]["messages"][0]["content"], request["system"])
        self.assertEqual(json.loads(calls[0]["messages"][1]["content"]), request["context"])
        self.assertEqual(calls[0]["messages"][2]["content"], request["question"])
        self.assertNotIn(ai.CONVERSATION_SYSTEM, request["system"])
        self.assertEqual(request["context"]["facts"]["accounts"][0]["code"], "1002")

    def test_invalid_or_incomplete_sheet_suggestion_is_an_error(self):
        for content, reason in (("无需增加", "stop"), ('{}', "stop"),
                                ('{"add":[],"remove":[]}', "length"),
                                ('{"add":{},"remove":[]}', "stop")):
            with self.subTest(content=content, reason=reason):
                client = ai.DeepSeekClient("synthetic-key", transport=lambda *args: {
                    "choices": [{"finish_reason": reason, "message": {"content": content}}]})
                result = papers.suggest_sheets(client, name="银行存款", accounts=ledger()[:1],
                                               available=["银行账户清单"])
                self.assertTrue(result["error"])
                self.assertFalse(result["add"])


class PaperUiTests(unittest.TestCase):
    def setUp(self):
        def unexpected_dialog(title, message, **kwargs):
            self.fail(f"{title}: {message}")
        for name in ("showerror", "showinfo", "askyesno"):
            self.enterContext(patch.object(gui.messagebox, name, side_effect=unexpected_dialog))
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
        def read_paper_data(subject, codes, install_path, with_journal=True):
            roots = [row for row in self.app.rows if row.code in codes]
            scoped = [row for row in self.app.rows if any(row == root or
                      (root.path and row.path.startswith(root.path + "/") and row.code.startswith(root.code))
                      for root in roots)]
            return PaperData(rows=tuple(scoped), codes=tuple(codes),
                             journal=JournalRows() if with_journal else None)
        self.enterContext(patch.object(gui.dxn, "read_paper_data", side_effect=read_paper_data))
        self.enterContext(patch.object(gui.dxn, "validate_paper_data_source", return_value=True))

    def dialog(self):
        dialog = gui.PaperDialog(self.app)
        dialog.withdraw()
        dialog.with_journal.set(False)
        dialog.code_box.current(next(index for index, item in enumerate(dialog.choices) if item[0] == "1-010"))
        dialog._sync_paper()
        dialog.children_tree.selection_set("1002")
        return dialog

    def test_workspace_button_opens_paper_dialog_without_api_key(self):
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        buttons = [widget for widget in descendants(self.app.pages["papers"])
                   if isinstance(widget, gui.ttk.Button) and widget.cget("text") == "制作报表项目底稿"]
        self.assertEqual(len(buttons), 1)
        with patch.object(gui, "PaperDialog") as opened:
            buttons[0].invoke()
        opened.assert_called_once_with(self.app, "paper")

    def test_dialog_groups_report_items_and_shows_primary_and_direct_secondary(self):
        dialog = self.dialog()
        self.assertEqual(list(dialog.code_box.cget("values")), ["1-010 货币资金"])
        self.assertEqual(dialog.children_tree.get_children(), ("1001", "1002"))
        values = [(dialog.children_tree.item(item, "text"), dialog.children_tree.item(item, "values")[0])
                  for item in dialog.children_tree.get_children("1002")]
        self.assertEqual(values, [("100201", "合成银行甲"), ("100202", "合成银行乙")])
        self.assertIn("1-010 货币资金", dialog.paper_var.get())
        self.assertIn("2 个科目", dialog.paper_var.get())

    def test_profit_loss_tree_shows_occurrence_instead_of_closing_balance(self):
        source = replace(ledger()[0], code="6602", name="管理费用", path="管理费用", is_profit_loss=True,
                         opening=Decimal(0), ending=Decimal(0), period_amount=Decimal(12000))
        child = replace(source, code="660201", name="办公费", path="管理费用/办公费", level=2)
        self.app.rows = [source, child]
        dialog = gui.PaperDialog(self.app)
        dialog.withdraw()
        self.assertEqual(dialog.children_tree.heading("ending", "text"), "本期账面发生额")
        for code in (source.code, child.code):
            value = dialog.children_tree.item(code, "values")[1]
            self.assertEqual(Decimal(str(value).replace(",", "")), Decimal(12000))

    def test_chat_previews_focused_accounts_and_knowledge_before_sending_exact_payload(self):
        source = replace(ledger()[0], code="1601", name="固定资产", path="固定资产",
                         opening=Decimal(165000), ending=Decimal(165000))
        depreciation = replace(source, code="1602", name="累计折旧", path="累计折旧", direction="贷",
                               opening=Decimal(0), ending=Decimal(52000))
        self.app.rows = [source, depreciation]
        self.app.store = SimpleNamespace(has_key=True, data={"ai_enabled": True})
        client = Mock(model="synthetic", base_url="https://model.invalid/v1")
        client.converse.return_value = {"intent": "audit", "answer": "原值未变动，折旧需结合清单核查。", "proposals": []}
        ref = {"citation_id": "K1", "title": "合成审计参考", "text": "核对资产清单。"}
        def enrich(question, context):
            context["knowledge_refs"] = [ref]
            return context, [ref], [ref]
        with patch.object(gui.ai_module.DeepSeekClient, "from_store", return_value=client), \
                patch.object(gui.ai_module, "inject_skills", side_effect=enrich), \
                patch.object(gui, "PayloadDialog", return_value=SimpleNamespace(confirmed=True)) as preview, \
                patch.object(self.app, "wait_window"), patch.object(self.app, "_on_chat_answer"), \
                patch.object(self.app, "_async", side_effect=lambda label, work, done: done(work())):
            self.app._chat_send("固定资产今年有什么异常吗")
        payload = preview.call_args.args[1]
        context = {key: value for key, value in payload.items() if key not in {"question", "history"}}
        self.assertEqual({item["code"] for item in context["facts"]["accounts"]}, {"1601", "1602"})
        self.assertEqual(context["knowledge_refs"], [ref])
        client.converse.assert_called_once_with("固定资产今年有什么异常吗", payload["history"], context)
        client.converse_with_skills.assert_not_called()

    def test_selecting_tree_row_does_not_shrink_report_scope_or_clear_draft(self):
        dialog = self.dialog()
        value = {"report": "1-010"}
        dialog.draft = value
        dialog.children_tree.selection_set("1001")
        self.assertEqual(dialog._synced_code, "1-010")
        self.assertIs(dialog.draft, value)
        dialog.children_tree.selection_set("100202")
        self.assertEqual(dialog._synced_code, "1-010")
        self.assertIs(dialog.draft, value)

    def test_inline_preview_confirms_the_displayed_request(self):
        dialog = self.dialog()
        captured = {}
        def confirm():
            captured["text"] = dialog.view.get("1.0", "end-1c")
            dialog._preview_choice.set("send")
        dialog.after(20, confirm)
        payload = {"purpose": "合成测试", "context": {"example": "仅合成数据"}}
        self.assertTrue(dialog._preview_request(payload, "合成模型", "https://model.invalid"))
        self.assertIn("仅合成数据", captured["text"])
        self.assertIn("https://model.invalid", captured["text"])

    def test_sheet_suggestions_apply_only_checked_items(self):
        parent = self.dialog()
        suggestion = gui.SheetSuggestionDialog(parent, {"add": [{"name": "甲表", "reason": "甲"}],
                                                        "remove": [{"name": "乙表", "reason": "乙"}]})
        suggestion.choices[1].set(True)
        suggestion._accept()
        self.assertEqual(suggestion.result, [("remove", {"name": "乙表", "reason": "乙"})])

    def test_money_display_uses_two_decimals_without_changing_value(self):
        value = Decimal("123.4567")
        self.assertEqual(gui.fmt_amount(value), "123.46")
        self.assertEqual(value, Decimal("123.4567"))

    def test_report_selection_only_shows_related_accounts(self):
        root = replace(ledger()[0], code="5001", name="开发成本", path="开发成本")
        child = replace(root, code="500101", name="合成项目", path="开发成本/合成项目", level=2)
        self.app.rows.extend([root, child])
        dialog = self.dialog()
        dialog.code_box.current(next(index for index, item in enumerate(dialog.choices) if item[0] == "1-150"))
        dialog._sync_paper()
        self.assertEqual(dialog.children_tree.get_children(), ("5001",))
        self.assertEqual(dialog.children_tree.get_children("5001"), ("500101",))
        self.assertEqual(dialog._synced_code, "1-150")
        self.assertEqual(dialog.current_paper.index_no, "1-150")

    def test_failed_sheet_suggestion_does_not_claim_no_adjustment_or_replace_selection(self):
        dialog = self.dialog()
        dialog.store = SimpleNamespace(has_key=True, data={"ai_enabled": True})
        dialog.sheet_add = ["银行账户清单"]
        with patch.object(gui.ai_module.DeepSeekClient, "from_store"), \
                patch.object(gui.papers, "suggest_sheets", return_value={
                    "add": [], "remove": [], "note": "合成格式错误", "error": True}), \
                patch.object(gui.messagebox, "showerror") as failed, \
                patch.object(gui.messagebox, "showinfo") as no_change:
            dialog._suggest_sheets()
        failed.assert_called_once()
        no_change.assert_not_called()
        self.assertEqual(dialog.state.get(), "合成格式错误")
        self.assertEqual(dialog.sheet_add, ["银行账户清单"])

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
        self.assertEqual(len(dialog.children_tree.get_children("1002")), 25)
        path = Path(self.folder.name) / "synthetic-many.xlsx"
        with patch.object(gui.filedialog, "asksaveasfilename", return_value=str(path)):
            dialog._export()
        book = load_workbook(path)
        self.addCleanup(book.close)
        detail_codes = {str(row[1].value).replace(".", "") for row in book["1-010 明细表"] if row[1].value}
        self.assertTrue({child.code for child in children} <= detail_codes)

    def test_export_contains_entire_cash_report_including_cash_and_bank(self):
        dialog = self.dialog()
        path = Path(self.folder.name) / "synthetic.xlsx"
        with patch.object(gui.filedialog, "asksaveasfilename", return_value=str(path)):
            dialog._export()
        book = load_workbook(path)
        self.addCleanup(book.close)
        catalog = " ".join(str(cell.value) for row in book["底稿目录"] for cell in row if cell.value is not None)
        self.assertNotIn("合成银行甲", catalog)
        self.assertIn("账项明细表", catalog)
        detail = book["1-010 明细表"]
        codes = [row[1].value for row in detail if row[0].value and str(row[0].value).startswith("1:K")]
        self.assertEqual(codes, ["1001", "1002.01", "1002.02"])
        self.assertIn("库存现金监盘表", book.sheetnames)
        self.assertEqual(book["1-010 审定表"]["D9"].value, 999)
        self.assertEqual(book["1-010 审定表"]["D10"].value, 100)
        self.assertIsNone(book["1-010 审定表"]["K9"].value)

    def test_export_always_includes_available_cached_journal(self):
        dialog = self.dialog()
        accounts, rows = papers.report_scope(self.app.rows, dialog.current_paper)
        journal = JournalRows([JournalRow(1, "2026-01-01", "SYN-1", "1", "SYNTHETIC-CACHED-VOUCHER",
                                          "1002", Decimal(1), Decimal(0))])
        dialog.with_journal.set(True)
        dialog.paper_data = PaperData(rows=rows, codes=tuple(row.code for row in accounts), journal=journal)
        for enabled in (False, True):
            dialog.with_journal.set(enabled)
            path = Path(self.folder.name) / f"synthetic-journal-{enabled}.xlsx"
            with patch.object(gui.filedialog, "asksaveasfilename", return_value=str(path)):
                dialog._export_pack()
            book = load_workbook(path)
            try:
                values = [cell.value for sheet in book for cells in sheet for cell in cells]
                self.assertIn("SYNTHETIC-CACHED-VOUCHER", values)
            finally:
                book.close()

    def test_shared_reserve_scope_reaches_real_reader_and_exports_only_its_category(self):
        base = replace(ledger()[0], code="1122", name="应收账款", path="应收账款",
                       subject_id="2", ledger_id="18")
        reserve = replace(base, code="1231", name="坏账准备", path="坏账准备", direction="贷",
                          opening=Decimal(20), ending=Decimal(20))
        first = replace(reserve, code="123101", name="应收账款坏账准备", level=2,
                        path="坏账准备/应收账款坏账准备", opening=Decimal(12), ending=Decimal(12))
        other = replace(first, code="123102", name="其他应收款坏账准备",
                        path="坏账准备/其他应收款坏账准备", opening=Decimal(8), ending=Decimal(8))
        self.app.rows = [base, reserve, first, other]
        self.app.loaded_subject = Subject("2", "合成企业", 2026, 6, "synthetic", ledger_id="18")
        database = object.__new__(gui.dxn.DxnLedgerDB)
        database.data, database.tables = Path("synthetic/data"), gui.dxn._TABLES
        database.read = Mock(return_value=self.app.rows)
        database.connection = MagicMock()
        database.connection.cursor.return_value.__enter__.return_value.fetchall.side_effect = [[], []]
        gui.dxn.read_paper_data.side_effect = lambda subject, codes, install, with_journal: database.paper_data(
            subject, codes, with_journal=with_journal)
        dialog = gui.PaperDialog(self.app)
        dialog.withdraw()
        dialog.with_journal.set(False)
        dialog.code_box.current(next(i for i, item in enumerate(dialog.choices) if item[0] == "1-070"))
        dialog._sync_paper()
        path = Path(self.folder.name) / "synthetic-reserve.xlsx"
        with patch.object(gui.filedialog, "asksaveasfilename", return_value=str(path)):
            dialog._export_pack()
        book = load_workbook(path)
        try:
            names = [book["1-070 审定表"].cell(i, 2).value for i in (9, 10)]
            self.assertEqual(names, ["应收账款", "应收账款坏账准备"])
            self.assertNotIn("其他应收款坏账准备", [cell.value for cells in book["1-070 审定表"] for cell in cells])
        finally:
            book.close()

    def test_cancelled_preview_starts_no_request(self):
        dialog = self.dialog()
        client = Mock(model="synthetic", base_url="https://model.invalid/v1")
        request = {"question": "合成问题", "context": {"facts": {"subject": {"code": "1002"}}}}
        dialog.queue.put(("preview", (request, None, "1002", client)))
        dialog.running = True
        with patch.object(dialog, "_preview_request", return_value=False):
            dialog._poll()
        client.converse.assert_not_called()
        self.assertIsNone(dialog.draft)

    def test_changed_source_during_preview_starts_no_request(self):
        dialog = self.dialog()
        client = Mock(model="synthetic", base_url="https://model.invalid/v1")
        def change(*_args):
            self.app._context_revision += 1
            return True
        with patch.object(dialog, "_preview_request", side_effect=change):
            self.assertFalse(dialog._confirm_request({"question": "合成问题", "context": {}}, client, "1002"))
        client.converse.assert_not_called()

    def test_confirmed_request_sends_exactly_the_previewed_context(self):
        dialog = self.dialog()
        self.app.store = SimpleNamespace(data={"ai_enabled": True})
        client = Mock(model="synthetic", base_url="https://model.invalid/v1")
        client.paper_text.return_value = {"intent": "audit", "answer": '{"audit_notes":["待补合成证据"]}'}
        with patch.object(dialog, "_preview_request", return_value=True) as preview, \
                patch.object(gui.ai_module.DeepSeekClient, "from_store", return_value=client), \
                patch.object(gui.threading, "Thread", side_effect=lambda target, daemon: SimpleNamespace(start=target)):
            dialog._generate()
            client.paper_text.assert_not_called()
            dialog._poll()
        payload = preview.call_args.args[0]
        client.paper_text.assert_called_once_with({key: value for key, value in payload.items() if key != "purpose"})
        client.converse.assert_not_called()
        client.converse_with_skills.assert_not_called()
        dialog._poll()
        self.assertEqual(dialog.draft["subject"]["code"], "1-010")

    def test_changed_source_blocks_export_before_choosing_a_file(self):
        dialog = self.dialog()
        accounts, rows = papers.report_scope(self.app.rows, dialog.current_paper)
        dialog.paper_data = PaperData(rows=rows, codes=tuple(row.code for row in accounts))
        dialog.draft = {"kind": "pack"}
        with patch.object(gui.dxn, "validate_paper_data_source", return_value=False), \
                patch.object(gui.filedialog, "asksaveasfilename") as choose:
            dialog._export_pack()
        choose.assert_not_called()
        self.assertIsNone(dialog.draft)
        self.assertIsNone(dialog.paper_data)
