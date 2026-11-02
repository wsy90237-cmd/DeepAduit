"""Regression cases from the six confirmed review findings; synthetic data only."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import queue
import tempfile
import tkinter as tk
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openpyxl import Workbook, load_workbook

from tb_autofill import auditlog, fields, gui, matching, workbook
from tb_autofill.models import LedgerRow, MatchResult, Subject, ledger_digest


def rows(subject="A", amount="100"):
    return [LedgerRow("1001", "库存现金", ending=Decimal(amount), debit=Decimal("30"),
                      subject_id=subject, year=2026, month=2)]


class WorkbookSafetyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "synthetic.xlsx"

    def scan(self, header="期末余额", detail=None, third=None):
        book = Workbook()
        sheet = book.active
        sheet.title = "Synthetic"
        sheet.append(["科目名称", header])
        if detail:
            sheet.merge_cells(f"A1:A{3 if third else 2}")
            sheet["B2"] = detail
        if third:
            sheet["B3"] = third
        sheet.cell(4 if third else 3 if detail else 2, 1, "库存现金")
        book.save(self.path)
        book.close()
        return fields.scan_template(self.path, ("ending",), "元")

    def test_audited_subheader_never_uses_book_values(self):
        for header, detail, third in (("期末余额", "审定数", None), ("期末", "余额", "审定数")):
            with self.subTest(header=header, detail=detail, third=third):
                scan = self.scan(header, detail, third)
                self.assertEqual(len(scan.fields), 1)
                result = matching.match_fields(scan.fields, rows())[0]
                self.assertEqual(result.status, "basis_mismatch")
                self.assertFalse(result.selected)

    def test_adjustment_subheader_is_excluded(self):
        scan = self.scan("期末余额", "调整分录")
        self.assertEqual(scan.fields, [])

    def test_monthly_occurrence_is_not_replaced_by_cumulative(self):
        for name in ("本月借方发生额", "当月发生额"):
            with self.subTest(header=name):
                scan = self.scan(name)
                self.assertEqual(len(scan.fields), 1)
                result = matching.match_fields(scan.fields, rows())[0]
                self.assertEqual(result.status, "missing")
                self.assertIsNone(result.amount)
                self.assertFalse(result.selected)
                self.assertIn("单月", result.reason)
                manual = matching.resolve_match(scan.fields[0], rows())
                self.assertEqual(manual.status, "missing")

    def test_inline_credit_balance_header_keeps_its_side(self):
        scan = self.scan("期末贷方余额")
        result = matching.match_fields(scan.fields, rows())[0]
        self.assertEqual(result.amount, Decimal(0))

    def test_current_source_is_required_for_bound_scan(self):
        scan = self.scan()
        scan.source_digest = ledger_digest(rows())
        results = matching.match_fields(scan.fields, rows())
        self.assertEqual(workbook.planned_writes(scan, results, rows=rows())[0][1], 100)
        for current in (None, rows("B", "200"), rows("A", "200")):
            with self.subTest(current=current), self.assertRaisesRegex(ValueError, "账套|主体"):
                workbook.planned_writes(scan, results, rows=current)

    def test_foreign_source_is_rejected_even_without_digest(self):
        scan = self.scan()
        results = matching.match_fields(scan.fields, rows())
        with self.assertRaisesRegex(ValueError, "之外"):
            workbook.planned_writes(scan, results, rows=rows("B"))

    def test_tb_write_rechecks_semantics_even_for_a_forged_exact_match(self):
        scan=self.scan()
        field=replace(scan.fields[0],label='应收账款',statement='balance_sheet')
        scan.fields=[field]
        correct=replace(rows()[0],code='1122',name='应收账款')
        wrong=replace(rows()[0],code='1002',name='银行存款')
        forged=MatchResult(target=field,sources=[wrong],amount=Decimal(100),status='matched',
                           selected=True,method='exact')
        with self.assertRaisesRegex(ValueError,'写入前复核'):
            workbook.planned_writes(scan,[forged],rows=[correct,wrong])

    def test_extra_content_outside_original_extent_is_rejected(self):
        scan = self.scan()
        results = matching.match_fields(scan.fields, rows())
        writes = workbook.planned_writes(scan, results)
        output = self.path.with_stem("output")
        for value in ("unexpected", "=1+1", 42):
            with self.subTest(value=value):
                book = load_workbook(scan.path)
                book["Synthetic"]["B2"] = 100
                book["Synthetic"]["D20"] = value
                book.save(output)
                book.close()
                with self.assertRaisesRegex(ValueError, "D20"):
                    workbook.verify_output(scan.path, output, writes)

    def test_correct_output_with_unchanged_formula_is_accepted(self):
        scan = self.scan()
        output = self.path.with_stem("output")
        book = load_workbook(scan.path)
        book["Synthetic"]["B2"] = 100
        book.save(output)
        book.close()
        writes = workbook.planned_writes(scan, matching.match_fields(scan.fields, rows()))
        workbook.verify_output(scan.path, output, writes)

    def test_saved_amount_tolerance_is_in_yuan_for_every_unit(self):
        scan = self.scan()
        output = self.path.with_stem("unit-output")
        for factor in (1, 1000, 10000, 100000000):
            target = replace(scan.fields[0], unit_factor=Decimal(factor))
            for yuan_error, accepted in (("0.004", True), ("0.04", False)):
                with self.subTest(factor=factor, yuan_error=yuan_error):
                    book = load_workbook(scan.path)
                    book["Synthetic"][target.cell] = float(Decimal(1) + Decimal(yuan_error) / factor)
                    book.save(output)
                    book.close()
                    if accepted:
                        workbook.verify_output(scan.path, output, [(target, Decimal(1))])
                    else:
                        with self.assertRaisesRegex(ValueError, "金额"):
                            workbook.verify_output(scan.path, output, [(target, Decimal(1))])

    def test_review_csv_keeps_numeric_zero_distinct_from_blank(self):
        import csv
        scan = self.scan()
        results = [matching.resolve_match(replace(scan.fields[0], value=value), rows())
                   for value in (None, 0, Decimal(0))]
        output = self.path.with_suffix(".csv")
        workbook.export_report(output, results)
        with output.open(encoding="utf-8-sig", newline="") as stream:
            records = list(csv.DictReader(stream))
        self.assertEqual([row["现有值"] for row in records], ["", "0", "0"])


def fake_app():
    interpreter = tk.Tcl()
    app = SimpleNamespace(_interpreter=interpreter, busy=False, _context_revision=0,
                          rows=rows(), results=[object()], scan=object(),
                          current_subject=Subject("A", "Synthetic-A", 2026, 2, "synthetic"),
                          loaded_subject=Subject("A", "Synthetic-A", 2026, 2, "synthetic"),
                          subjects=[], queue=queue.Queue(), _ai_subset=[],
                          _chat_clear=Mock(), _refresh_tree=Mock(),
                          report_btn=Mock(), write_btn=Mock(), subject_box=Mock(),
                          ledger_state=Mock(), scan_state=Mock(), _log=Mock(), after=Mock())
    for name in ("install_var", "project_var", "subject_var", "year_var", "month_var",
                 "template_var", "period_var", "unit_var"):
        setattr(app, name, tk.StringVar(master=interpreter))
    app._invalidate_review = MethodType(gui.App._invalidate_review, app)
    app._pump = MethodType(gui.App._pump, app)
    app._post = app.queue.put
    app._set_busy = lambda busy, text="": setattr(app, "busy", busy)
    app._finish_error = MethodType(gui.App._finish_error, app)
    app._finish_ok = MethodType(gui.App._finish_ok, app)
    return app


class SessionSafetyTests(unittest.TestCase):
    def test_context_edits_invalidate_old_results(self):
        for name in ("install_var", "project_var", "subject_var", "year_var", "month_var",
                     "template_var", "period_var", "unit_var"):
            with self.subTest(variable=name):
                app = fake_app()
                gui.App._bind_context_changes(app)
                getattr(app, name).set("changed")
                self.assertEqual(app.results, [])
                self.assertIsNone(app.scan)
                app._chat_clear.assert_called()
                if name in {"install_var", "project_var", "subject_var", "year_var", "month_var"}:
                    self.assertEqual(app.rows, [])
                    self.assertIsNone(app.loaded_subject)

    def test_new_ledger_invalidates_previous_matches(self):
        app = fake_app()
        subject = Subject("B", "Synthetic-B", 2026, 2, "synthetic")
        gui.App._on_ledger(app, rows("B", "200"), subject)
        self.assertEqual(app.results, [])
        self.assertIsNone(app.scan)
        self.assertEqual(app.loaded_subject, subject)
        self.assertIn("Synthetic-B", gui.App._subject_label(app))

    def test_stale_background_success_cannot_repopulate_results(self):
        app, done = fake_app(), Mock()
        old_revision = app._context_revision
        app._invalidate_review(clear_ledger=True)
        gui.App._finish_ok(app, "synthetic", ["old results"], done, old_revision)
        done.assert_not_called()
        self.assertFalse(app.busy)

    def test_background_error_releases_busy_and_pump_survives(self):
        app = fake_app()

        class ImmediateThread:
            def __init__(self, target, **kwargs):
                self.target = target

            def start(self):
                self.target()

        def fail():
            raise ValueError("synthetic failure")

        with patch.object(gui.threading, "Thread", ImmediateThread), patch.object(gui.messagebox, "showerror") as shown:
            gui.App._async(app, "synthetic", fail)
            gui.App._pump(app)
        self.assertFalse(app.busy)
        self.assertEqual(shown.call_args.args[1], "synthetic failure")
        app.after.assert_called_once()

    def test_one_bad_callback_does_not_disable_future_queue_ticks(self):
        app, next_action = fake_app(), Mock()
        app.queue.put(lambda: (_ for _ in ()).throw(ValueError("synthetic callback failure")))
        app.queue.put(next_action)
        gui.App._pump(app)
        self.assertFalse(app.busy)
        scheduled = app.after.call_args.args[1]
        scheduled()
        next_action.assert_called_once()

    def test_write_audit_uses_the_export_snapshot(self):
        app = fake_app()
        target = fields.TargetField("Synthetic", "B2", "库存现金", "ending")
        results = [matching.resolve_match(target, rows())]
        scan = SimpleNamespace(path=Path("synthetic.xlsx"), digest="synthetic-digest", unit="元")
        app._audit = Mock()
        app.results, app.scan = [], None
        with patch.object(gui.messagebox, "showinfo"):
            gui.App._on_written(app, Path("output.xlsx"), scan, results, "Synthetic-A / 2026-02")
        self.assertEqual(app._audit.call_args.kwargs["subject"], "Synthetic-A / 2026-02")
        self.assertEqual(app._audit.call_args.kwargs["cells"][0]["after"], "100")


class AuditCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "synthetic.jsonl"
        self.log = auditlog.AuditLog(self.path, actor="synthetic")

    def test_tail_deletion_is_detected_and_cannot_be_extended(self):
        for i in range(3):
            self.log.append("synthetic", item=i)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        self.assertFalse(self.log.verify()[0])
        with self.assertRaises(auditlog.AuditLogError):
            self.log.append("replacement")

    def test_missing_log_is_detected_if_checkpoint_survives(self):
        self.log.append("synthetic")
        self.path.unlink()
        self.assertFalse(self.log.verify()[0])

    def test_corrupt_checkpoint_fails_closed(self):
        self.log.append("synthetic")
        self.log.checkpoint.write_bytes(b"synthetic corrupt checkpoint")
        self.assertFalse(self.log.verify()[0])

    def test_legacy_chain_is_explicitly_limited(self):
        self.log.append("synthetic")
        self.log.checkpoint.unlink()
        ok, seq, reason = self.log.verify()
        self.assertTrue(ok)
        self.assertIsNone(seq)
        self.assertIn("仅链内", reason)

    def test_business_fields_cannot_replace_actor_or_hash(self):
        for key in ("seq", "actor", "hash", "prev"):
            with self.subTest(key=key), self.assertRaises(auditlog.AuditLogError):
                self.log.append("synthetic", **{key: "spoofed"})


if __name__ == "__main__":
    unittest.main()
