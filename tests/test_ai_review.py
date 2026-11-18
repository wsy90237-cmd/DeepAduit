"""AI 复核闭环：模型只判来源范围，本地负责校验与金额。"""

import json
import unittest
from decimal import Decimal
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tb_autofill import ai, gui
from tb_autofill.models import LedgerRow, MatchResult, TargetField
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
        batch = ai.tb_draft_batches([result], rows)[0]
        hint = ai.review(client(decisions), [result], rows, payload=batch, include_unconfirmed=True)[0]
        self.assertEqual(hint["action"], "hold")
        self.assertEqual(hint["unconfirmed_codes"], ["660201"])
        self.assertEqual(hint["source_codes"], [])

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
        app = SimpleNamespace(_context_revision=1, scan=object(), scan_state=Mock(), _async=Mock())
        service = SimpleNamespace(model="synthetic", base_url="https://example.invalid")
        with patch.object(gui.messagebox, "askokcancel", return_value=False) as preview:
            gui.App._confirm_tb_draft(app, service, [pending()], ledger(), [{"targets": [{}]}])
        preview.assert_called_once()
        app._async.assert_not_called()

    def test_tb_draft_batches_exclude_existing_amounts_formulas_and_audited_fields(self):
        target = TargetField("S", "B2", "银行存款", "ending")
        good = [MatchResult(replace(target, cell=f"B{number}"), status="unmatched") for number in range(2, 30)]
        excluded = [MatchResult(replace(target, cell="C2", value=0), status="ambiguous"),
                    MatchResult(replace(target, cell="D2", is_formula=True), status="protected"),
                    MatchResult(replace(target, cell="E2", basis="audited"), status="ambiguous")]
        batches = ai.tb_draft_batches(good + excluded, ledger())
        self.assertEqual([len(batch["targets"]) for batch in batches], [12, 12, 4])
        self.assertTrue(all(not batch["candidates"] for batch in batches))
        self.assertEqual([item["target_id"] for batch in batches for item in batch["targets"]],
                         [item.target.id for item in good])

    def test_tb_draft_inserts_local_amount_only_if_it_does_not_conflict(self):
        target = TargetField("S", "B2", "银行存款", "ending")
        unrelated = TargetField("S", "B3", "应收账款", "ending")
        collision = replace(target, cell="B4")
        results = [resolve_match(target, [ledger()[0]], rows=ledger()),
                   MatchResult(unrelated, status="unmatched"), MatchResult(collision, status="unmatched")]
        decisions = [{"target_id": unrelated.id, "action": "fill", "source_codes": ["1122"], "amount": "9"},
                     {"target_id": collision.id, "action": "fill", "source_codes": ["1002"]}]
        app = SimpleNamespace(results=results, rows=ledger(), filter_var=Mock(), scan_state=Mock(),
                              _refresh_tree=Mock(), _audit=Mock())
        gui.App._apply_tb_draft(app, decisions)
        self.assertIs(app.results[0], results[0])
        self.assertTrue(app.results[0].selected)
        self.assertEqual(app.results[1].amount, Decimal("2000"))
        self.assertTrue(app.results[1].selected)
        self.assertFalse(app.results[2].selected)

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
