"""Source-review regressions; all amounts and files are synthetic."""

from dataclasses import replace
from decimal import Decimal
import inspect
from pathlib import Path
import queue
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openpyxl import load_workbook

from tb_autofill import audit_index, dxn_paper, gui, paper_pack, papers
from tb_autofill.models import LedgerRow, Subject, ledger_digest


def ledger():
    root = LedgerRow("1002", "银行存款", path="银行存款", level=1, direction="借",
                     opening=Decimal(100), ending=Decimal(100), debit=Decimal(0), credit=Decimal(0),
                     subject_id="synthetic", year=2026, month=6, ledger_id="1")
    branch = replace(root, code="100201", name="合成银行", path="银行存款/合成银行", level=2)
    leaf = replace(branch, code="10020101", name="合成账户", path="银行存款/合成银行/合成账户", level=3)
    return [root, branch, leaf]


def resolve(code, name):
    return audit_index.resolve(code, name)[0]


class RequestScopeTests(unittest.TestCase):
    def app(self):
        return SimpleNamespace(
            busy=False, _context_revision=1, rows=ledger(), results=[], knowledge_refs=[],
            store=SimpleNamespace(has_key=True, data={"ai_enabled": True}), chat_history=[],
            _chat_append=Mock(), _async=Mock(), _on_chat_answer=Mock(), wait_window=Mock(),
        )

    def send(self, app):
        with patch.object(gui, "PayloadDialog", return_value=SimpleNamespace(confirmed=True)), \
                patch.object(gui.ai_module.DeepSeekClient, "from_store", return_value=SimpleNamespace(
                    model="synthetic", base_url="https://model.invalid/v1")):
            gui.App._chat_send(app, question="请复核本次账套的审计证据")

    def test_context_change_while_preview_open_cancels_request(self):
        app = self.app()
        app.wait_window.side_effect = lambda _dialog: setattr(app, "_context_revision", 2)
        self.send(app)
        app._async.assert_not_called()
        self.assertEqual(app.chat_history, [])
        self.assertIn("重新预览", app._chat_append.call_args.args[1])

    def test_unchanged_preview_can_start_request(self):
        app = self.app()
        self.send(app)
        app._async.assert_called_once()

    def test_generation_entrypoints_never_open_dialog_or_send_data(self):
        app = self.app()
        app._draft_document = MethodType(gui.App._draft_document, app)
        with patch.object(gui, "PaperDialog") as dialog, \
                patch.object(gui.dxn_paper, "build_all") as build, \
                patch.object(gui.ai_module.DeepSeekClient, "from_store") as client, \
                patch.object(gui.messagebox, "showinfo") as notice:
            for method in (gui.App._draft_notes, gui.App._draft_report,
                           gui.App._draft_paper, gui.App._build_all_papers):
                method(app)
            self.assertEqual(notice.call_count, 4)
            dialog.assert_not_called()
            build.assert_not_called()
            client.assert_not_called()

    def test_dialog_detects_same_length_source_changes(self):
        rows = ledger()
        subject = Subject("synthetic", "合成企业", 2026, 6, "synthetic", ledger_id="1")
        for revision, current_subject, current_rows in (
            (2, subject, rows),
            (1, replace(subject, ledger_id="2"), rows),
            (1, subject, [replace(row, ending=Decimal(200)) for row in rows]),
        ):
            with self.subTest(revision=revision, subject=current_subject):
                app = SimpleNamespace(_context_revision=revision, loaded_subject=current_subject, rows=current_rows)
                dialog = SimpleNamespace(app=app, _source_snapshot=(1, subject, ledger_digest(rows)),
                                         _reload_choices=Mock(), state=Mock())
                self.assertFalse(gui.PaperDialog._ensure_fresh_choices(dialog))
                dialog._reload_choices.assert_called_once()

    def test_old_account_response_cannot_replace_current_draft_or_journal(self):
        replies = queue.Queue()
        replies.put(("ok", ({"kind": "pack"}, ["old journal"], "old")))
        dialog = SimpleNamespace(running=True, queue=replies, run_btn=Mock(), state=Mock(),
                                 _ensure_fresh_choices=Mock(return_value=True), scope="paper",
                                 _synced_code="current", draft=None, journal_rows=None, journal_code="")
        gui.PaperDialog._poll(dialog)
        self.assertIsNone(dialog.draft)
        self.assertIsNone(dialog.journal_rows)
        self.assertEqual(dialog.journal_code, "")

    def test_stale_export_stops_before_choosing_output_file(self):
        dialog = SimpleNamespace(_ensure_fresh_choices=Mock(return_value=False))
        with patch.object(gui.filedialog, "asksaveasfilename") as choose:
            gui.PaperDialog._export(dialog)
        choose.assert_not_called()


class DraftAmountTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "synthetic.xlsx"
        self.paper = resolve("1002", "银行存款")

    def build(self, **kwargs):
        dxn_paper.build(self.path, paper=kwargs.pop("paper", self.paper),
                        subject_label="合成企业 / 2026-06", rows=kwargs.pop("rows", ledger()),
                        resolve=resolve, **kwargs)
        book = load_workbook(self.path)
        self.addCleanup(book.close)
        return book

    def test_nested_descendants_are_not_double_counted(self):
        rows = ledger()
        self.assertEqual([row.code for row in dxn_paper.detail_rows(rows, rows[0])], ["100201"])
        self.assertEqual(dxn_paper.consistency([rows[0]], rows), (True, Decimal(0)))

    def test_mixed_directions_are_reconciled_as_net_balances(self):
        root, child, _leaf = ledger()
        root = replace(root, opening=Decimal(60), ending=Decimal(60))
        debit = replace(child, opening=Decimal(80), ending=Decimal(80))
        credit = replace(child, code="100202", path="银行存款/合成银行二", name="合成银行二",
                         direction="贷", opening=Decimal(20), ending=Decimal(20))
        self.assertEqual(dxn_paper.consistency([root], [root, debit, credit]), (True, Decimal(0)))

    def test_missing_amount_cannot_be_reported_as_zero(self):
        with self.assertRaisesRegex(ValueError, "缺少|不完整"):
            self.build(rows=[replace(ledger()[0], opening=None)])
        self.assertFalse(self.path.exists())

    def test_missing_paper_never_groups_unrelated_accounts(self):
        with self.assertRaisesRegex(ValueError, "报表项目|索引"):
            self.build(paper=None)
        self.assertFalse(self.path.exists())

    def test_unmatched_paper_never_falls_back_to_first_account(self):
        with self.assertRaisesRegex(ValueError, "报表项目|一级科目"):
            self.build(paper=resolve("1122", "应收账款"))
        self.assertFalse(self.path.exists())

    def test_mixed_ledgers_are_rejected_before_export(self):
        root, child, _leaf = ledger()
        with self.assertRaisesRegex(ValueError, "账套|主体|期间"):
            self.build(rows=[root, replace(child, ledger_id="other")])
        self.assertFalse(self.path.exists())

    def test_conflicting_duplicate_code_is_rejected_before_export(self):
        rows = ledger()
        with self.assertRaisesRegex(ValueError, "冲突"):
            self.build(rows=[*rows, replace(rows[0], ending=Decimal(200))])
        self.assertFalse(self.path.exists())

    def test_book_balances_do_not_become_audited_figures(self):
        book = self.build()
        audit, detail = book["1-010 审定表"], book["1-010 明细表"]
        self.assertEqual(audit["M9"].value, 100)  # 年初账面余额
        for cell in ("K9", "Q9"):
            self.assertIsNone(audit[cell].value, cell)
        for cell in ("J8", "M8", "N8", "S8"):
            self.assertIsNone(detail[cell].value, cell)

    def test_disclosure_totals_align_with_amount_columns(self):
        sheet = self.build()["披露表"]
        self.assertEqual(sheet["B9"].value, 100)
        self.assertEqual(sheet["C9"].value, 100)
        self.assertIsNone(sheet["D9"].value)
        self.assertIsNone(sheet["D8"].value)

    def test_alternate_pack_keeps_audited_and_adjustment_columns_empty(self):
        for is_pl in (False, True):
            rows = [replace(row, is_profit_loss=is_pl) for row in ledger()]
            paper_pack.build(self.path, paper=self.paper, subject_label="合成企业 / 2026-06",
                             subject_row=rows[0], rows=rows)
            book = load_workbook(self.path)
            self.addCleanup(book.close)
            sheet = book["1-010 导引表"]
            for cell in (("C8", "D8") if is_pl else ("D8", "E8")):
                self.assertIsNone(sheet[cell].value, (is_pl, cell))
            self.assertIsNone(book["1-010-15 披露表"]["D8"].value)

    def test_alternate_pack_rejects_missing_amount_and_mixed_sources(self):
        for rows in ([replace(ledger()[0], ending=None)],
                     [ledger()[0], replace(ledger()[1], ledger_id="other")]):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "不完整|账套"):
                paper_pack.build(self.path, paper=self.paper, subject_label="合成企业 / 2026-06",
                                 subject_row=rows[0], rows=rows)


class DraftTextSafetyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "synthetic.xlsx"

    def assert_literal(self, book, literal):
        self.addCleanup(book.close)
        matches = [cell for sheet in book for row in sheet for cell in row if cell.value == literal]
        self.assertTrue(matches, literal)
        self.assertTrue(all(cell.data_type == "s" for cell in matches))

    def test_simple_export_does_not_execute_model_text_or_signer(self):
        literal = '=HYPERLINK("https://example.invalid","synthetic")'
        draft = {"body": literal, "subject": {"code": "1002", "name": literal}, "kind_label": "合成草稿"}
        papers.export_xlsx(self.path, draft, prepared_by=literal)
        self.assert_literal(load_workbook(self.path), literal)

    def test_packed_export_keeps_narrative_as_text(self):
        literal = "=1+1"
        paper_pack.build(self.path, paper=resolve("1002", "银行存款"), subject_label="合成企业 / 2026-06",
                         subject_row=ledger()[0], rows=ledger(), narrative={"guide_notes": [literal]})
        self.assert_literal(load_workbook(self.path), literal)

    def test_dxn_export_keeps_text_literal_and_local_formulas_active(self):
        literal = "=1+1"
        dxn_paper.build(self.path, paper=resolve("1002", "银行存款"), subject_label="合成企业 / 2026-06",
                        rows=ledger(), resolve=resolve, narrative={"guide_notes": [literal]}, prepared_by=literal)
        book = load_workbook(self.path)
        self.assert_literal(book, literal)
        self.assertEqual(book["1-010 审定表"]["D10"].data_type, "f")


class ReleaseCoverageTests(unittest.TestCase):
    def test_native_gate_covers_every_business_module(self):
        from tools.build_release import NATIVE_MODULES
        package = Path(inspect.getfile(papers)).parent
        modules = {f"tb_autofill.{path.stem}" for path in package.glob("*.py") if not path.stem.startswith("__")}
        self.assertEqual(NATIVE_MODULES, modules)
