"""复核摘要不把未知值、层级重复或结账后的零余额当作可靠证据。"""
from dataclasses import replace
from decimal import Decimal
import unittest

from tb_autofill import ai
from tb_autofill.models import LedgerRow, MatchResult, TargetField


class AccountEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.target = TargetField('Synthetic', 'B2', '往来', 'ending')
        self.item = MatchResult(self.target)
        self.root = LedgerRow('1', '往来', path='往来', ending=Decimal(100))
        self.child = LedgerRow('11', '甲', path='往来/甲', level=2, ending=Decimal(100))

    def evidence(self, rows, item=None):
        return ai.account_evidence(item or self.item, rows)['candidates'][0]

    def test_reconcile_only_direct_children(self):
        rows = [self.root, replace(self.child, ending=Decimal(60)),
                LedgerRow('12', '乙', path='往来/乙', level=2, ending=Decimal(40)),
                LedgerRow('111', '孙级', path='往来/甲/孙级', level=3, ending=Decimal(60))]
        value = self.evidence(rows)
        self.assertEqual(value['child_count'], 2)
        self.assertTrue(value['parent_equals_children'])

    def test_known_mismatch_and_zero_are_distinct_from_unknown(self):
        self.assertFalse(self.evidence([self.root, replace(self.child, ending=Decimal(99))])['parent_equals_children'])
        self.assertTrue(self.evidence([replace(self.root, ending=Decimal(0)),
                                       replace(self.child, ending=Decimal(0))])['parent_equals_children'])
        for parent, child in ((None, None), (Decimal(0), None), (None, Decimal(0))):
            with self.subTest(parent=parent, child=child):
                self.assertIsNone(self.evidence([replace(self.root, ending=parent),
                                                replace(self.child, ending=child)])['parent_equals_children'])

    def test_balances_use_common_debit_direction_and_keep_reversals(self):
        rows = [self.root, replace(self.child, ending=Decimal(150)),
                LedgerRow('12', '乙', path='往来/乙', level=2, direction='贷', ending=Decimal(50))]
        self.assertTrue(self.evidence(rows)['parent_equals_children'])
        rows[1] = replace(rows[1], ending=Decimal(-150))
        self.assertFalse(self.evidence(rows)['parent_equals_children'])

    def test_incomplete_or_mixed_context_does_not_claim_equality(self):
        cases = [replace(self.child, level=3), replace(self.child, year=2025),
                 replace(self.child, basis='audited'), replace(self.child, ledger_id='other')]
        for child in cases:
            with self.subTest(child=child):
                self.assertIsNone(self.evidence([self.root, child])['parent_equals_children'])
        self.assertIsNone(self.evidence([self.root])['parent_equals_children'])
        self.assertIsNone(self.evidence([self.root, self.child, replace(self.child, code='12')])['parent_equals_children'])

    def test_split_balance_is_not_an_additive_parent_check(self):
        item = MatchResult(replace(self.target, balance_side='借'))
        self.assertIsNone(self.evidence([self.root, self.child], item)['parent_equals_children'])

    def test_closed_profit_loss_retains_occurrence_review(self):
        item = MatchResult(replace(self.target, label='管理费用', statement='profit_loss', measure='profit_loss'))
        row = LedgerRow('6602', '管理费用', path='管理费用', ending=Decimal(0),
                        debit=Decimal(100), credit=Decimal(100), is_profit_loss=True)
        payload = ai.review_payload([item], [row])
        self.assertEqual(len(payload['targets']), 1)
        evidence = payload['targets'][0]['account_evidence']['candidates'][0]
        self.assertEqual(Decimal(evidence['value']), 100)
        self.assertEqual(Decimal(payload['candidates'][0]['debit']), 100)

    def test_opening_profit_uses_opening_balance_in_review(self):
        target = replace(self.target, label='加：（一）年初未分配利润', measure='opening_balance', statement='profit_loss')
        row = LedgerRow('410401', '未分配利润', path='利润分配/未分配利润', level=2,
                        direction='贷', opening=Decimal(100), ending=Decimal(0))
        self.assertEqual(len(ai.review_payload([MatchResult(target)], [row])['targets']), 1)


if __name__ == '__main__':
    unittest.main()
