import unittest
from tb_autofill.evidence_ownership import check_ownership, MAX_PAGES


class OwnershipTests(unittest.TestCase):
    def test_explicit_conflicts_and_exclusions(self):
        result = check_ownership(['客户：其他公司\n对方户名：别家公司\n印制截止日期：2024-01-01\n印制日期：2024-01-01\n户名：甲公司\n余额日期：2025年12月31日', '对账单户名：乙公司\n余额截止日：2025-11-30'], '甲公司', 2025, 12)
        self.assertEqual(len(result['issues']), 2)
        self.assertEqual([item['page'] for item in result['issues']], [2, 2])
        self.assertIn('乙公司', result['issues'][0]['original'])
        self.assertFalse(result['truncated'])

    def test_invalid_date_and_limits(self):
        result = check_ownership(['被审计单位：甲公司\n截止日：2025-02-30'] + [''] * MAX_PAGES, '甲公司', 2025, 2)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['issues'][0]['kind'], '日期待核')
        with self.assertRaises(ValueError):
            check_ownership([], '', 2025, 12)
