"""合成证据从导入、模型请求预览、采纳到实际工作簿的回归测试。"""
import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from openpyxl import load_workbook
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from tb_autofill import audit_index, dxn_paper, evidence_draft, evidence_ui, pdf_text
from tb_autofill.bank_evidence_ui import _found_in_excerpt
from tb_autofill.evidence_project import ProjectStore
from tb_autofill.models import LedgerRow, Subject


class EvidenceReviewTests(unittest.TestCase):
    def test_split_decimal_cannot_be_presented_as_whole_yuan(self):
        excerpt = "Balance 456 ． 78"
        for quote in ("456", excerpt):
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                evidence_draft.validate_fields(dict(field="余额", value="456", quote=quote,
                    risk="待核", next_step="核对原件"), excerpt)
        self.assertFalse(_found_in_excerpt("456", excerpt, "0-9.,+-"))

    def test_selected_page_reaches_only_current_reviewed_workpaper(self):
        with TemporaryDirectory() as folder:
            subject = Subject("S", "合成公司", 2025, 12, "synthetic", ledger_id="L")
            store = ProjectStore(folder, evidence_draft.project_entity(subject), subject.year)
            balance = LedgerRow("1002", "银行存款", opening=Decimal("123.45"),
                                debit=Decimal("0"), credit=Decimal("0"), ending=Decimal("123.45"),
                                subject_id="S", ledger_id="L", year=2025, month=12)
            app = SimpleNamespace(loaded_subject=subject, rows=[balance], scan=None, results=[],
                                  template_var=Mock(), evidence_store=store)
            app.template_var.get.return_value = ""
            pdf = Path(folder) / "合成函证.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(400, 400)
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 20 20 Td (Balance 123.45) Tj ET")
            page.replace_contents(stream)
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): DictionaryObject({
                    NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica")})})})
            writer.write(pdf)
            original = pdf.read_bytes()
            doc = store.import_pdf(pdf, lambda path: [s for _, s in pdf_text.extract_pdf_pages(path)])
            store.mark_reviewed(doc["id"])
            view = Mock()
            view.store = store
            view.documents = store.list_documents()
            view.docs.current.return_value = view.pages.current.return_value = 0
            evidence_ui.EvidenceDialog.select_doc(view)
            _, page = evidence_ui.EvidenceDialog.current(view)
            self.assertEqual(page["text"], "Balance 123.45")
            result = dict(field="银行余额", value="123.45", quote=page["text"],
                          risk="待核账户完整性", next_step="核对回函")
            client = Mock(model="synthetic")
            client._request.return_value = {"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(result, ensure_ascii=False)}}]}
            row = evidence_draft.extract_fields(client, {"摘录": page["text"]})
            paper = audit_index.resolve("1002", "银行存款")[0]
            row.update(paper=paper.index_no, context=evidence_draft.source_key(app),
                       filename=doc["name"], version=doc["version"], ref=page["evidence_id"],
                       page=1, sha256=doc["sha256"], reviewer="合成复核人")
            sid = store.add_suggestion(json.dumps(row), [row["ref"]])
            self.assertFalse(evidence_ui.paper_rows(app, paper.index_no))
            store.confirm_suggestion(sid)
            rows = evidence_ui.paper_rows(app, paper.index_no)
            self.assertEqual(rows, [row])
            out = Path(folder) / "单册.xlsx"
            dxn_paper.build(out, paper=paper, subject_label="合成公司 / 2025-12", rows=app.rows,
                            resolve=lambda c, n: audit_index.resolve(c, n)[0], evidence_rows=rows)
            book = load_workbook(out, read_only=True)
            try:
                values = list(book["证据核对与风险"].values)[1]
                self.assertIn(row["ref"], values)
                self.assertIn("123.45", values)
                self.assertIn(doc["sha256"], values)
            finally:
                book.close()
            output_dir = Path(folder) / "整套"
            bundle = dxn_paper.build_all(output_dir, subject_label="合成公司 / 2025-12",
                rows=app.rows, resolve=lambda c, n: audit_index.resolve(c, n)[0], papers=[paper],
                evidence_for=lambda index: evidence_ui.paper_rows(app, index))
            self.assertEqual(bundle["papers"], 1)
            book = load_workbook(output_dir / bundle["files"][0], read_only=True)
            try:
                self.assertIn(row["ref"], list(book["证据核对与风险"].values)[1])
            finally:
                book.close()
            self.assertEqual(pdf.read_bytes(), original)
            app.loaded_subject = Subject("B", "其他公司", 2025, 12, "B")
            self.assertFalse(evidence_ui.paper_rows(app, paper.index_no))

    def test_cancel_evidence_preview_sends_no_request(self):
        view = Mock()
        view.selection.return_value = ({"id": "doc"}, {"page": 1}, SimpleNamespace(name="测试"),
                                       "合成摘录", "scope")
        view.subject.name = "合成公司"
        view.app._subject_label.return_value = "合成公司 / 2025-12"
        view.show_text.return_value = False
        with patch.object(evidence_ui.ai.DeepSeekClient, "from_store",
                          return_value=SimpleNamespace(base_url="local/mock", model="mock")):
            evidence_ui.EvidenceDialog.analyze(view)
        self.assertIn("合成摘录", view.show_text.call_args.args[1])
        self.assertIn("local/mock", view.show_text.call_args.args[1])
        view.app._async.assert_not_called()

    def test_batch_keeps_successes_and_reuses_unchanged_scan(self):
        with TemporaryDirectory() as directory:
            store = ProjectStore(directory, "合成主体", 2025)
            valid = store.inbox / "回函.pdf"
            writer = PdfWriter()
            writer.add_blank_page(100, 100)
            writer.write(valid)
            invalid = store.inbox / "损坏.pdf"
            invalid.write_bytes(b"synthetic non-PDF")
            view = Mock()
            view.store = store
            view.stop_batch.is_set.return_value = False
            view.app._async.side_effect = lambda label, run, done: done(run())
            with patch.object(evidence_ui, "extract_pdf_pages", return_value=[(1, "合成回函")]) as extract:
                evidence_ui.EvidenceDialog.import_files(view, [invalid, valid])
                evidence_ui.EvidenceDialog.import_files(view, [valid])
                self.assertEqual(extract.call_count, 1)
            self.assertEqual(len(store.list_documents()), 1)
            self.assertIn("损坏.pdf", view.show_text.call_args.args[1])

    def test_export_refuses_pdf_path(self):
        with TemporaryDirectory() as directory:
            pdf = Path(directory) / "source.pdf"
            pdf.write_bytes(b"%PDF-synthetic")
            with self.assertRaisesRegex(ValueError, "xlsx"):
                dxn_paper.build(pdf, paper=None, rows=[], resolve=None, subject_label="合成")
            self.assertEqual(pdf.read_bytes(), b"%PDF-synthetic")
