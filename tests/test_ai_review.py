"""AI 复核闭环：模型只判来源范围，本地负责校验与金额。"""

import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tb_autofill import ai, gui
from tb_autofill.models import LedgerRow, TargetField
from tb_autofill.matching import match_fields, resolve_match


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
    def test_duplicate_target_decisions_are_rejected_before_display_or_apply(self):
        result = pending()
        decision = {'target_id': result.target.id, 'action': 'fill', 'source_codes': ['1002']}
        with self.assertRaisesRegex(ai.AIError, '重复'):
            ai.review(client([decision, decision]), [result], ledger())

    def test_review_requires_a_decision_list(self):
        result = pending()
        for decisions in ({'unexpected': True}, 'invalid', None):
            with self.subTest(decisions=decisions), self.assertRaises(ai.AIError):
                ai.review(client(decisions), [result], ledger())

    def test_ambiguous_sources_cannot_be_resolved_by_ai_review(self):
        target = TargetField("S", "B2", "其他", "ending", parent="管理费用")
        rows = [LedgerRow("6602", "管理费用", path="管理费用"),
                LedgerRow("660201", "其他", path="管理费用/其他", level=2, ending=Decimal(10)),
                LedgerRow("660202", "其他", path="管理费用/其他", level=2, ending=Decimal(20))]
        result = match_fields([target], rows)[0]
        self.assertEqual(result.status, "ambiguous")
        decisions = [{"target_id": target.id, "action": "fill", "source_codes": ["660201"], "reason": "挑一个"}]
        checked = ai.review(client(decisions), [result], rows)
        self.assertEqual(checked[0]["action"], "hold")
        self.assertEqual(checked[0]["source_codes"], [])

    def test_review_rejects_partial_or_truncated_source_sets(self):
        result = pending()
        for codes in (["1002", "9999"], ["1002"] * 4):
            with self.subTest(codes=codes):
                decisions = [{"target_id": result.target.id, "action": "fill", "source_codes": codes, "reason": "x"}]
                checked = ai.review(client(decisions), [result], ledger())
                self.assertEqual(checked[0]["action"], "hold")
                self.assertEqual(checked[0]["source_codes"], [])

    def test_legacy_review_method_keeps_ai_semantic_guard(self):
        target = TargetField("S", "B2", "应收账款", "ending")
        bank = ledger()[0]
        check = resolve_match(target, [bank], method="ai-review", rows=ledger())
        self.assertNotEqual(check.status, "matched")
        self.assertFalse(check.selected)
        self.assertEqual(check.method, "ai")

    def test_cancelled_payload_preview_starts_no_request(self):
        app = SimpleNamespace(_context_revision=1, results=[pending()], rows=ledger(), store=object())
        dialog = SimpleNamespace(app=app, state=Mock(), wait_window=Mock(), running=False)
        service = SimpleNamespace(model="synthetic", base_url="https://example.invalid")
        with patch.object(ai.DeepSeekClient, "from_store", return_value=service), \
                patch.object(gui, "PayloadDialog", return_value=SimpleNamespace(confirmed=False)) as preview, \
                patch.object(gui.threading, "Thread") as thread:
            gui.ReviewDialog._load(dialog)
        preview.assert_called_once()
        thread.assert_not_called()
        self.assertFalse(dialog.running)

    def test_stale_review_cannot_apply_to_changed_context(self):
        app = SimpleNamespace(_context_revision=2)
        dialog = SimpleNamespace(app=app, context_revision=1, state=Mock(), tree=Mock())
        gui.ReviewDialog._apply(dialog)
        dialog.tree.get_children.assert_not_called()
        self.assertIn("旧建议", dialog.state.set.call_args.args[0])

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
