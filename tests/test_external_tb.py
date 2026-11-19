import csv
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tb_autofill.models import MatchResult, TargetField
from tools import preview_external_tb as pilot


def table(headers, rows):
    stream = StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


class ExternalTBTests(unittest.TestCase):
    identity = ('合成企业', '演示', '2026-06', 'CNY')
    accounts = (
        ('1002', '', '银行存款', '借', '资产负债', 100, 60, 25, 135),
        ('1002.01', '1002', '银行甲', '借', '资产负债', 80, 50, 20, 110),
        ('1002.02', '1002', '银行乙', '借', '资产负债', 20, 10, 5, 25),
        ('02202', '', '应付账款', '贷', '资产负债', -100, 25, 60, -135),
    )
    entries = (
        ('2026-01-05', '记1', 1, '1002.01', 50, 0),
        ('2026-01-05', '记1', 2, '02202', 0, 50),
        ('2026-06-10', '记2', 1, '1002.02', 10, 0),
        ('2026-06-10', '记2', 2, '02202', 0, 10),
        ('2026-06-30', '记3', 1, '1002.01', 0, 20),
        ('2026-06-30', '记3', 2, '1002.02', 0, 5),
        ('2026-06-30', '记3', 3, '02202', 25, 0),
    )

    def sources(self, *, accounts=None, entries=None, journal_context=None):
        return (table(pilot.BALANCE, [self.identity + item for item in (accounts or self.accounts)]),
                table(pilot.JOURNAL, [(journal_context or self.identity) + item for item in (entries or self.entries)]))

    def test_actual_template_preview_keeps_formula_and_existing_cells(self):
        fixture = Path(__file__).parent / 'fixtures' / '试算平衡表-合成.xlsx'
        with tempfile.TemporaryDirectory() as folder:
            balance, journal, report = [Path(folder) / name for name in ('balance.csv', 'journal.csv', 'preview.csv')]
            balance_bytes, journal_bytes = self.sources()
            balance.write_bytes(balance_bytes)
            journal.write_bytes(journal_bytes)
            original = pilot.fields.file_digest(fixture)
            result = pilot.run(balance, journal, fixture, report)
            bank = next(item for item in result if item.target.label.strip() == '银行存款' and item.target.period == 'ending')
            payable = next(item for item in result if item.target.label.strip() == '应付账款' and item.target.period == 'ending')
            self.assertEqual(bank.amount, 135)
            self.assertEqual(payable.amount, 135)
            self.assertEqual(payable.source_ids, ['02202'])
            self.assertTrue(any(item.selected for item in result))
            self.assertFalse(any(item.selected for item in result if item.target.is_formula or item.target.value is not None))
            self.assertIn('序时账 SHA-256', report.read_text(encoding='utf-8-sig'))
            self.assertEqual(pilot.fields.file_digest(fixture), original)

    def test_wrong_leaf_rejected_even_when_vouchers_and_total_still_balance(self):
        changed = list(self.entries)
        changed[-2] = changed[-2][:3] + ('1002.01', 0, 5)
        with self.assertRaisesRegex(ValueError, r'1002\.01.*\+5\.00.*1002\.02.*-5\.00'):
            pilot.read_rows(*self.sources(entries=changed))

    def test_different_period_and_missing_direction_rejected(self):
        with self.assertRaisesRegex(ValueError, '截止月或币种不一致'):
            pilot.read_rows(*self.sources(journal_context=('合成企业', '演示', '2026-05', 'CNY')))
        invalid = list(self.accounts)
        invalid[-1] = invalid[-1][:3] + ('',) + invalid[-1][4:]
        with self.assertRaisesRegex(ValueError, '方向或类别不明确'):
            pilot.read_rows(*self.sources(accounts=invalid))

    def test_month_only_turnover_not_accepted(self):
        with self.assertRaisesRegex(ValueError, '本年借方发生额'):
            balance, journal = self.sources()
            pilot.read_rows(balance.replace('本年借方发生额'.encode(), '本月借方发生额'.encode()), journal)

    def test_profit_loss_candidate_is_not_proposed_as_a_write(self):
        fixture = Path(__file__).parent / 'fixtures' / '试算平衡表-合成.xlsx'
        target = TargetField('试算平衡表', 'D99', '主营业务收入', 'ending', statement='profit_loss')
        unsafe = MatchResult(target, amount=pilot.Decimal(999), status='matched', selected=True)
        with tempfile.TemporaryDirectory() as folder:
            balance, journal, report = [Path(folder) / name for name in ('balance.csv', 'journal.csv', 'preview.csv')]
            balance_bytes, journal_bytes = self.sources()
            balance.write_bytes(balance_bytes)
            journal.write_bytes(journal_bytes)
            with patch.object(pilot.matching, 'match_fields', return_value=[unsafe]):
                pilot.run(balance, journal, fixture, report)
        self.assertEqual((unsafe.status, unsafe.amount, unsafe.selected), ('missing', None, False))

    def test_parent_omission_and_unbalanced_opening_rejected(self):
        invalid = list(self.accounts)
        invalid[1] = ('1002.01', '') + invalid[1][2:]
        with self.assertRaisesRegex(ValueError, '父级与编码层级不一致'):
            pilot.read_rows(*self.sources(accounts=invalid))
        invalid = self.accounts[:3] + (('02202', '', '应付账款', '贷', '资产负债', -101, 25, 60, -136),)
        with self.assertRaisesRegex(ValueError, '年初余额借贷不平'):
            pilot.read_rows(*self.sources(accounts=invalid))
