from decimal import Decimal
from hashlib import sha256
import json
from queue import Queue
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from openpyxl import Workbook, load_workbook
from pypdf import PdfWriter

from tb_autofill import audit_index, auto_intake_ui, auto_project as auto, bank_listing
from tb_autofill.evidence_project import ProjectStore
from tb_autofill.models import LedgerRow, PaperData, Subject


class AutoProjectTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.subject = Subject('A', '合成公司', 2025, 12, 'synthetic', ledger_id='1')
        self.store = ProjectStore(temp.name, '合成公司 [A]', 2025)
        def row(code, name, ending, debit):
            return LedgerRow(code, name, name, opening=Decimal(ending), ending=Decimal(ending),
                debit=Decimal(debit), credit=Decimal(debit), subject_id='A', year=2025, month=12, ledger_id='1')
        self.rows = [row('1002', '银行存款', 1200000, 0), row('1001', '库存现金', 0, 50), row('1122', '应收账款', 0, 0)]
        self.dirs = auto.folders(self.store, self.rows)
        self.bank = self.store.root / '1002 银行存款'
        self.text = '银行回函\n户名：合成公司\n完整账号：01234567\n截止日：2025-12-31\n币种：CNY\n金额单位：元\n回函余额：1180000.00'

    def prepare(self):
        return auto.prepare(self.store, auto.inventory(self.dirs), self.subject, self.rows, 'CNY',
                            audit_index.bundled(), 'confirmed', lambda _: [(1, self.text)])

    def listing(self):
        book = Workbook()
        sheet = book.active
        sheet.title = bank_listing.SHEET
        sheet.append(bank_listing.HEADERS)
        sheet.append(['合成公司', '2025-12-31', '1002', 'CNY', '元', '合成公司', '01234567', 1200000])
        book.save(self.bank / '清单.xlsx')
        book.close()

    def test_batch_revision_withdrawal_and_manual_edits(self):
        self.assertTrue((self.store.root / '1001 库存现金').is_dir())
        self.assertFalse((self.store.root / '1122 应收账款').exists())
        self.listing()
        pdf = self.bank / '回函.pdf'
        pdf.write_bytes(b'synthetic fixture')
        prepared, notes = self.prepare()
        self.assertIn('-20000.00', notes[0])
        path = self.store.root / 'temp.xlsx'
        auto.render(path, prepared, self.subject, audit_index.bundled())
        first = auto.publish(self.store, path, prepared[0])
        book = load_workbook(first)
        sheet = book['证据核对与风险']
        self.assertIn('-20000.00', sheet['F3'].value)
        self.assertEqual('银行账户余额清单!H2', sheet['J2'].value)
        self.assertEqual(1, sheet['I3'].value)
        sheet['A5'] = '人工修改'
        book.save(first)
        book.close()
        (self.bank / '00重复.pdf').write_bytes(pdf.read_bytes())
        self.assertEqual(self.prepare()[0][0], prepared[0])
        self.assertIsNone(auto.publish(self.store, path, prepared[0]))
        pdf.unlink()
        (self.bank / '00重复.pdf').unlink()
        changed, _ = self.prepare()
        auto.render(path, changed, self.subject, audit_index.bundled())
        second = auto.publish(self.store, path, changed[0])
        self.assertNotEqual(first, second)
        book = load_workbook(first)
        self.assertEqual('人工修改', book['证据核对与风险']['A5'].value)
        book.close()

    def test_agent_distinguishes_missing_evidence_from_amount_difference(self):
        self.assertIn('无法检查', self.prepare()[1][-1])
        self.listing()
        self.assertIn('银行回函、银行对账单', self.prepare()[1][-1])
        (self.bank / '回函.pdf').write_bytes(b'synthetic confirmation')
        _, notes = self.prepare()
        self.assertIn('-20000.00', notes[0])
        self.assertIn('账号 01234567：未匹配可比的银行对账单', notes[-1])

    def test_ambiguous_or_wrong_scope_never_compared_with_total(self):
        pdf = self.bank / '回函.pdf'
        pdf.write_bytes(b'synthetic no listing')
        self.assertFalse(self.prepare()[0][-1])
        self.listing()
        pdf.write_bytes(b'synthetic different year')
        self.text = self.text.replace('2025-12', '2024-12')
        prepared, notes = self.prepare()
        self.assertEqual(len(prepared[-1]), 1)
        self.assertTrue(any('不符' in note for note in notes))
        self.assertNotEqual(self.store.root, ProjectStore(self.store.root.parent.parent, '合成公司 [A]', 2024).root)

    def test_background_batch_checks_context_without_publishing_a_workpaper(self):
        callbacks = Queue()
        app = SimpleNamespace(rows=self.rows, loaded_subject=self.subject, paper_library=audit_index.bundled(),
                              intake_status=Mock(), scan=object(), results=[], _post=callbacks.put)
        app.intake = session = auto_intake_ui.Intake(app, self.store)
        session.approval = ('scope', 'CNY')
        session.data = PaperData(rows=tuple(self.rows[:2]))
        session.confirmed_scan, session.review = app.scan, []
        session.stat_template = Mock(return_value=(10, 100))
        session.template_stat = session.stat_template()
        self.listing()
        with patch.object(auto_intake_ui.draft, 'source_key', return_value='scope'), patch.object(
                auto_intake_ui.dxn, 'validate_paper_data_source', return_value=True):
            session.inspect()
            self.assertFalse(session.running)
            session.inspect()
            finish = callbacks.get(timeout=20)
            app.loaded_subject = None
            finish()
            self.assertFalse((self.store.root / '底稿草稿').exists())
            app.loaded_subject = self.subject
            session.inspect()
            callbacks.get(timeout=20)()
            outputs = list((self.store.root / '底稿草稿').rglob('*.xlsx'))
            self.assertEqual(outputs, [])
            self.assertTrue(session.finished)
            session.inspect()
            self.assertFalse(session.running)
            app.scan = None
            session.inspect()
            self.assertIsNone(session.approval)

    def test_auto_intake_does_not_reuse_legacy_pdf_recognition_cache(self):
        self.listing()
        pdf = self.bank / '银行回函.pdf'
        writer = PdfWriter()
        writer.add_blank_page(300, 300)
        writer.write(pdf)
        digest = sha256(pdf.read_bytes()).hexdigest()
        with self.store._connect() as db:
            db.execute('CREATE TABLE intake(digest TEXT PRIMARY KEY, pages TEXT)')
            db.execute('INSERT INTO intake VALUES (?,?)', (digest, json.dumps([(1, self.text)])))
        prepared, notes = auto.prepare(self.store, auto.inventory(self.dirs), self.subject, self.rows, 'CNY',
                                       audit_index.bundled(), 'confirmed')
        self.assertEqual([row['field'] for row in prepared[-1]], ['逐户余额清单'])
        self.assertTrue(any('文字层' in note for note in notes))
        with self.store._connect() as db:
            self.assertIsNotNone(db.execute('SELECT pages FROM intake WHERE digest=?', (digest,)).fetchone())
