"""评测仅使用合成资料，默认预览、隐藏原填数、保留完整来源上下文。"""
from dataclasses import asdict, replace
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tb_autofill.models import LedgerRow, MatchResult, Subject, TargetField
from tools.qa import compare_answers, crosstest, eval_ai_review, per_file_check


class QAGuardTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.base = Path(self.folder.name)
        self.target = TargetField('Synthetic', 'B2', '银行存款', 'ending')
        self.row = LedgerRow('1002', '银行存款', path='银行存款', ending=Decimal(100), subject_id='synthetic', year=2025, month=12, ledger_id='1')
        self.item = MatchResult(self.target)

    def test_preview_does_not_send_until_exact_scope_and_service_are_approved(self):
        client = SimpleNamespace(model='synthetic', base_url='https://example.invalid')
        preview = self.base / 'preview.json'
        with patch.object(eval_ai_review.ai, 'review', return_value=[]) as send:
            self.assertIsNone(eval_ai_review.preview_reviews(client, [[self.item]], [self.row], preview))
            send.assert_not_called()
            digest = hashlib.sha256(preview.read_bytes()).hexdigest()
            changed = replace(self.row, ending=Decimal(200))
            with self.assertRaises(ValueError):
                eval_ai_review.preview_reviews(client, [[self.item]], [changed], preview, digest)
            send.assert_not_called()
            client.base_url = 'https://another.invalid'
            with self.assertRaises(ValueError):
                eval_ai_review.preview_reviews(client, [[self.item]], [self.row], preview, digest)
            send.assert_not_called()
            client.base_url = 'https://example.invalid'
            self.assertEqual(eval_ai_review.preview_reviews(client, [[self.item]], [self.row], preview, digest), [[]])
            send.assert_called_once()

    def test_cache_roundtrip_keeps_source_scope_and_does_not_mix_periods(self):
        subject = Subject('synthetic', 'Synthetic', 2025, 12, str(self.base), ledger_id='1')
        row = replace(self.row, basis='audited', is_profit_loss=True, level=2)
        with patch.object(per_file_check, 'CACHE', self.base / 'cache'):
            per_file_check.store_rows(subject, [row])
            self.assertEqual(per_file_check.load_cache(subject), [row])
            self.assertIsNone(per_file_check.load_cache(replace(subject, month=11)))
            self.assertIsNone(per_file_check.load_cache(replace(subject, ledger_id='2')))
        self.assertEqual(eval_ai_review.load_rows(next((self.base / 'cache').glob('*.json'))), [row])

    def test_incomplete_legacy_snapshot_is_rejected(self):
        path = self.base / 'legacy.json'
        path.write_text(json.dumps([{'code': '1002', 'name': '银行存款'}]), encoding='utf-8')
        with self.assertRaises(ValueError):
            eval_ai_review.load_rows(path)

    def test_preview_library_is_usable_with_a_non_unicode_console(self):
        client = SimpleNamespace(model='synthetic', base_url='https://example.invalid')
        with io.TextIOWrapper(io.BytesIO(), encoding='cp1252') as stream, patch('sys.stdout', stream):
            self.assertIsNone(eval_ai_review.preview_reviews(client, [[self.item]], [self.row], self.base / '中文预览.json'))

    def test_comparison_hides_originals_and_counts_formula_once(self):
        original = replace(self.target, value=Decimal(1))
        formula = replace(self.target, cell='B3', value='=1+1', is_formula=True)
        scan = SimpleNamespace(fields=[original, formula], unit='元')
        with patch.object(compare_answers.fields, 'scan_template', return_value=scan), \
                patch.object(compare_answers, 'load_rows', return_value=[self.row]), \
                patch.object(compare_answers, 'answer_values', return_value=({}, {('Synthetic', 'B3')})):
            result = compare_answers.compare(1, self.base / 'synthetic.xlsx', self.base / 'ledger.json')
        self.assertEqual(result['counts']['公式保护'], 1)
        self.assertEqual(result['counts']['原填空白，待核对'], 1)

    def test_comparison_tolerance_is_in_yuan(self):
        target = replace(self.target, unit_factor=Decimal(10000))
        scan = SimpleNamespace(fields=[target], unit='万元')
        for module, method in ((compare_answers, 'compare'), (per_file_check, 'check_one')):
            with self.subTest(module=module.__name__), patch.object(module.fields, 'scan_template', return_value=scan):
                if method == 'compare':
                    with patch.object(module, 'load_rows', return_value=[self.row]), \
                            patch.object(module, 'answer_values', return_value=({('Synthetic', 'B2'): Decimal('.010004')}, set())):
                        result = module.compare(1, self.base / 'synthetic.xlsx', self.base / 'ledger.json')
                else:
                    with patch.object(module, 'answers_of', return_value=({('Synthetic', 'B2'): Decimal('.010004')}, set())):
                        result = module.check_one(self.base / 'synthetic.xlsx', [self.row])
                self.assertEqual(result['counts']['有差额'], 1)

    def test_trace_uses_requested_occurrence_and_direction(self):
        target = replace(self.target, label='管理费用', statement='profit_loss', measure='profit_loss')
        row = replace(self.row, name='管理费用', path='管理费用', is_profit_loss=True, ending=Decimal(0), debit=Decimal(100))
        result = MatchResult(target, [row], Decimal(100), 'matched')
        self.assertTrue(crosstest.trace(result, {row.code: row})['matches'])
        row = replace(self.row, direction='贷')
        result = MatchResult(replace(self.target, presentation_direction='借'), [row], Decimal(-100), 'matched')
        self.assertTrue(crosstest.trace(result, {row.code: row})['matches'])

    def test_cross_template_comparison_normalizes_units_and_source_scope(self):
        record = {'status': 'matched', 'label': '其他', 'parent': '管理费用', 'period': 'ending',
                  'basis': 'book', 'unit': '1', 'amount': '100', 'sources': ['6602']}
        def run(item):
            return {'template': 'synthetic', 'unit': item['unit'], 'records': [item]}
        self.assertEqual(crosstest.cross_template_conflicts([run(record), run(dict(record, unit='10000', amount='.01'))]), [])
        self.assertEqual(crosstest.cross_template_conflicts([run(record), run(dict(record, parent='销售费用', amount='200'))]), [])
        self.assertEqual(crosstest.cross_template_conflicts([run(record), run(dict(record, sources=['6603'], amount='200'))]), [])
        self.assertEqual(len(crosstest.cross_template_conflicts([run(record), run(dict(record, amount='200'))])), 1)


if __name__ == '__main__':
    unittest.main()
