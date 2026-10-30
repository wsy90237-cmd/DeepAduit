"""AI 复核闭环：模型只判来源范围，本地负责校验与金额。"""

import json
import unittest
from decimal import Decimal

from tb_autofill import ai
from tb_autofill.models import LedgerRow, TargetField
from tb_autofill.matching import resolve_match


def ledger():
    return [LedgerRow("1002", "银行存款", path="1002", ending=Decimal("1000"), year=2025, month=12),
            LedgerRow("1122", "应收账款", path="1122", ending=Decimal("2000"), year=2025, month=12),
            LedgerRow("1529", "投资性房地产", path="1529", ending=Decimal("5000"), year=2025, month=12),
            LedgerRow("1529.01", "投资性房地产-成本", path="1529/1529.01", ending=Decimal("3000"),
                      year=2025, month=12)]


def pending(label="银行存款", cell="B2"):
    target = TargetField("S", cell, label, "ending")
    return resolve_match(target, ledger(), rows=ledger())


def client(decisions):
    def transport(path, payload=None):
        body = {"decisions": decisions}
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": json.dumps(body, ensure_ascii=False)}}]}
    return ai.DeepSeekClient("sk-" + "a" * 32, "deepseek-flash", transport=transport)


class ReviewGuardTests(unittest.TestCase):
    def test_payload_lists_pending_items_and_candidates(self):
        target = TargetField("S", "B2", "投资性房地产", "ending")
        result = resolve_match(target, ledger(), rows=ledger())
        payload = ai.review_payload([result], ledger())
        self.assertTrue(payload["targets"])
        codes = {item["code"] for item in payload["candidates"]}
        self.assertIn("1529", codes)

    def test_codes_outside_candidates_are_dropped(self):
        result = pending()
        decisions = [{"target_id": result.target.id, "action": "fill",
                      "source_codes": ["9999"], "reason": "编造编码"}]
        checked = ai.review(client(decisions), [result], ledger())
        self.assertEqual(checked[0]["action"], "hold")     # 无有效来源 → 转人工
        self.assertEqual(checked[0]["source_codes"], [])

    def test_unknown_target_is_ignored(self):
        result = pending()
        decisions = [{"target_id": "不存在!A1", "action": "fill", "source_codes": ["1002"], "reason": "x"}]
        self.assertEqual(ai.review(client(decisions), [result], ledger()), [])

    def test_invalid_action_is_ignored(self):
        result = pending()
        decisions = [{"target_id": result.target.id, "action": "overwrite", "source_codes": [], "reason": "x"}]
        self.assertEqual(ai.review(client(decisions), [result], ledger()), [])

    def test_valid_fill_keeps_amount_local(self):
        result = pending()
        decisions = [{"target_id": result.target.id, "action": "fill",
                      "source_codes": ["1002"], "reason": "同名一级科目"}]
        checked = ai.review(client(decisions), [result], ledger())
        self.assertEqual(checked[0]["action"], "fill")
        check = resolve_match(result.target, [ledger()[0]], method="manual", rows=ledger())
        self.assertEqual(check.amount, Decimal("1000"))    # 金额来自本地，不来自模型


if __name__ == "__main__":
    unittest.main()
