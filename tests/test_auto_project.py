from decimal import Decimal
from queue import Queue
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from openpyxl import Workbook, load_workbook

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

    def test_scanned_image_in_bank_folder_uses_rules_and_keeps_source(self):
        self.listing()
        image = self.bank / '回函.PNG'
        image.write_bytes(b'synthetic image fixture')
        (self.store.root / '1001 库存现金' / '错投.jpg').write_bytes(b'outside bank scope')
        self.text = self.text.replace('01234567', '０１２３ ４５６７').replace('2025-12-31', '2025 一 12 一 31')
        self.text = self.text.replace('1180000.00', '1,180,000 · 00').replace('户名：', '户名 ， ')
        prepared, notes = self.prepare()
        evidence = prepared[-1][1]
        self.assertEqual((evidence['filename'], evidence['page']), (str(image.relative_to(self.store.root)), 1))
        self.assertIn('-20000.00', evidence['comparison'])
        self.assertTrue(any('错投.jpg' in note and '非银行自动填写范围' in note for note in notes))
        image.write_bytes(b'synthetic ambiguous image')
        self.text += '\n期末余额：1000000.00'
        prepared, notes = self.prepare()
        self.assertEqual(len(prepared[-1]), 1)
        self.assertTrue(any('第1页' in note and '重复字段' in note for note in notes))
        image.write_bytes(b'synthetic separated amounts')
        self.text = self.text.split('\n期末余额')[0].replace('1,180,000 · 00', '1 180000 · 00')
        prepared, notes = self.prepare()
        self.assertEqual(len(prepared[-1]), 1)
        self.assertTrue(any('银行证据余额格式不符合要求' in note for note in notes))

    def test_unconfirmed_scope_does_not_read_dropped_files(self):
        app = SimpleNamespace(rows=self.rows, loaded_subject=self.subject, paper_library=audit_index.bundled(),
                              intake_status=Mock(), scan=None)
        app.intake = session = auto_intake_ui.Intake(app, self.store)
        (self.bank / '回函.jpg').write_bytes(b'synthetic fixture')
        with patch.object(auto_intake_ui, 'Thread') as worker:
            session.inspect()
            session.inspect()
        worker.assert_not_called()
        self.assertFalse(session.running)
        self.assertIn('未读取', session.notes[0])

    def test_background_batch_checks_context_before_publication(self):
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
            self.assertEqual(len(outputs), 1, session.notes)
            session.inspect()
            self.assertFalse(session.running)
            app.scan = None
            session.inspect()
            self.assertIsNone(session.approval)
