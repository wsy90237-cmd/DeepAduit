"""Native Excel may renumber external links; compare their actual targets."""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from tb_autofill import workbook
from tb_autofill.models import TargetField

def book(*targets):
    return SimpleNamespace(_external_links=[SimpleNamespace(file_link=SimpleNamespace(Target=t)) for t in targets])

def formula(value):
    return SimpleNamespace(data_type='f',value=value)

class ExternalLinkTests(unittest.TestCase):
    def test_relative_link_must_resolve_to_same_file_after_moving_workbook(self):
        before=workbook._external_targets(book('../sources/a.xlsx'),r'C:\engagement\templates\input.xlsx')
        after=workbook._external_targets(book('../../engagement/sources/a.xlsx'),r'C:\out\run\copy.xlsx')
        self.assertEqual(before,after)
        workbook._verify_external_targets(before,after)
        relocated=workbook._external_targets(book('../sources/a.xlsx'),r'C:\out\run\copy.xlsx')
        with self.assertRaises(ValueError):workbook._verify_external_targets(before,relocated)
        rooted_c=workbook._external_targets(book('/sources/a.xlsx'),r'C:\input.xlsx')
        rooted_d=workbook._external_targets(book('/sources/a.xlsx'),r'D:\output.xlsx')
        self.assertNotEqual(rooted_c,rooted_d)

    def test_renumbered_formula_is_equivalent_only_for_the_same_target(self):
        before=workbook._external_targets(book('file:///C:/a.xlsx','file:///C:/b.xlsx'),r'C:\input.xlsx')
        after=tuple(reversed(before))
        workbook._verify_external_targets(before,after)
        self.assertEqual(workbook._fingerprint(formula('=[2]Sheet1!A1'),before),
                         workbook._fingerprint(formula('=[1]Sheet1!A1'),after))
        self.assertNotEqual(workbook._fingerprint(formula('=[2]Sheet1!A1'),before),
                            workbook._fingerprint(formula('=[2]Sheet1!A1'),after))
        with self.assertRaises(ValueError):workbook._verify_external_targets(before,(before[0],before[0]))

    def test_text_literals_and_structured_references_are_not_external_indices(self):
        before=(('file','a'),('file','b'));after=tuple(reversed(before))
        for text in ('="[1]Sheet1!A1"','=SUM(Table1[1])'):
            self.assertEqual(workbook._fingerprint(formula(text),before),workbook._fingerprint(formula(text),after))
        self.assertNotEqual(workbook._fingerprint(formula('="[1]Sheet1!A1"'),before),
                            workbook._fingerprint(formula('="[2]Sheet1!A1"'),after))

    def test_unparseable_external_formula_and_unknown_link_type_fail_closed(self):
        targets=(('file','a'),('file','b'))
        with patch.object(workbook,'Tokenizer',side_effect=ValueError('unsupported')):
            self.assertNotEqual(workbook._fingerprint(formula('=[1]Sheet!A1'),targets),
                                workbook._fingerprint(formula('=[1]Sheet!A1'),tuple(reversed(targets))))
        with self.assertRaises(ValueError):workbook._verify_external_targets((None,),(None,))

    def test_native_export_opens_original_readonly_then_saves_a_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.xlsx';source.write_bytes(b'synthetic-original')
            output=Path(folder)/'output.xlsx'
            field=TargetField('Sheet1','A1','库存现金','ending')
            scan=SimpleNamespace(path=source,digest='hash')
            active=Mock();active.ReadOnly=True;active.FileFormat=51
            active.Worksheets.return_value.Cells.return_value.HasFormula=False
            def save_as(*args,**kwargs):active.ReadOnly=False
            active.SaveAs.side_effect=save_as
            excel=Mock();excel.Workbooks.Open.return_value=active
            client=SimpleNamespace(DispatchEx=Mock(return_value=excel))
            pythoncom=SimpleNamespace(CoInitialize=Mock(),CoUninitialize=Mock())
            modules={'pythoncom':pythoncom,'win32com':SimpleNamespace(client=client),'win32com.client':client}
            with patch.dict('sys.modules',modules),patch.object(workbook,'planned_writes',return_value=[(field,Decimal(1))]),\
                    patch.object(workbook,'verify_output'),patch.object(workbook,'file_digest',return_value='hash'):
                workbook.write_results(scan,[],output)
            self.assertEqual(excel.Workbooks.Open.call_args.args[0],str(source))
            self.assertTrue(excel.Workbooks.Open.call_args.kwargs['ReadOnly'])
            self.assertNotEqual(active.SaveAs.call_args.args[0],str(source))
            self.assertEqual(active.SaveAs.call_args.kwargs['FileFormat'],51)
            self.assertEqual(source.read_bytes(),b'synthetic-original')
