import json
import unittest
from decimal import Decimal

from tb_autofill.ai import AIError, DeepSeekClient, assistance_payload
from tb_autofill.matching import apply_ai_suggestions, match_fields
from tb_autofill.models import LedgerRow, TargetField


def row(code="1001", name="库存现金", **kwargs):
    values = dict(code=code, name=name, opening=Decimal("5"), ending=Decimal("12"),
                  subject_id="S1", year=2026, month=6)
    values.update(kwargs)
    return LedgerRow(**values)


def target(label="库存现金", **kwargs):
    values = dict(sheet="期末", cell="D10", label=label, period="ending")
    values.update(kwargs)
    return TargetField(**values)


def completion(content):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content, ensure_ascii=False)}}]}


def make_client(response, model="deepseek-chat"):
    calls = []

    def transport(path, body):
        calls.append((path, body))
        return response

    return DeepSeekClient("sk-test", model, transport=transport), calls


class ModelDiscoveryTests(unittest.TestCase):
    def test_list_models_reads_account_models(self):
        client, _ = make_client({"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]})
        self.assertEqual(client.list_models(), ["deepseek-chat", "deepseek-reasoner"])

    def test_test_connection_rejects_model_not_granted(self):
        client, _ = make_client({"data": [{"id": "other-model"}]})
        with self.assertRaises(AIError) as caught:
            client.test_connection()
        self.assertIn("未开通", str(caught.exception))

    def test_empty_models_is_error(self):
        client, _ = make_client({"data": []})
        with self.assertRaises(AIError):
            client.list_models()


class RequestShapeTests(unittest.TestCase):
    def test_request_body_contains_verified_parameters_only(self):
        client, calls = make_client(completion({"summary": "无", "suggestions": []}))
        client.assist({"targets": [], "candidates": []})
        path, body = calls[0]
        self.assertEqual(path, "/chat/completions")
        self.assertEqual(set(body), {"model", "messages", "stream", "thinking",
                                     "temperature", "max_tokens", "response_format"})
        # 关闭推理，避免 reasoning_content 挤占 max_tokens 导致 JSON 截断
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_empty_key_rejected(self):
        with self.assertRaises(AIError):
            DeepSeekClient("   ")

    def test_unknown_model_name_still_usable_but_bounded(self):
        with self.assertRaises(AIError):
            DeepSeekClient("sk-test", "x" * 65)
        DeepSeekClient("sk-test", "deepseek-chat-preview")


class AssistValidationTests(unittest.TestCase):
    def payload(self, count=1):
        return {"targets": [{"target_id": f"期末!D{10 + i}"} for i in range(count)],
                "candidates": [{"code": "1001"}]}

    def test_valid_suggestion_accepted(self):
        client, _ = make_client(completion({"summary": "可匹配",
            "suggestions": [{"target_id": "期末!D10", "source_codes": ["1001"], "reason": "名称一致"}]}))
        value = client.assist(self.payload())
        self.assertEqual(value["suggestions"][0]["source_codes"], ["1001"])

    def test_empty_suggestions_allowed(self):
        client, _ = make_client(completion({"summary": "无法判断", "suggestions": []}))
        self.assertEqual(client.assist(self.payload())["suggestions"], [])

    def test_unknown_target_rejected(self):
        client, _ = make_client(completion({"summary": "x",
            "suggestions": [{"target_id": "期末!Z99", "source_codes": ["1001"], "reason": "x"}]}))
        with self.assertRaises(AIError):
            client.assist(self.payload())

    def test_unknown_or_duplicate_code_rejected(self):
        for codes in (["9999"], ["1001", "1001"]):
            with self.subTest(codes=codes):
                client, _ = make_client(completion({"summary": "x",
                    "suggestions": [{"target_id": "期末!D10", "source_codes": codes, "reason": "x"}]}))
                with self.assertRaises(AIError):
                    client.assist(self.payload())

    def test_truncated_response_rejected(self):
        client, _ = make_client({"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]})
        with self.assertRaises(AIError):
            client.assist(self.payload())

    def test_non_json_content_rejected(self):
        client, _ = make_client({"choices": [{"finish_reason": "stop", "message": {"content": "不是JSON"}}]})
        with self.assertRaises(AIError):
            client.assist(self.payload())

    def test_payload_limits(self):
        client, _ = make_client(completion({"summary": "", "suggestions": []}))
        with self.assertRaises(AIError):
            assistance_payload([type("R", (), {"target": target(cell=f"D{i}")})() for i in range(31)], [])
        with self.assertRaises(AIError):
            assistance_payload([], [row(code=str(1000 + i)) for i in range(301)])


class ApplySuggestionTests(unittest.TestCase):
    def test_ai_choice_recomputes_amount_locally(self):
        rows = [row("660201", "其他", level=2, path="管理费用/其他"),
                row("660301", "其他", level=2, path="财务费用/其他")]
        results = match_fields([target("其他")], rows)
        self.assertEqual(results[0].status, "ambiguous")
        applied, notes = apply_ai_suggestions(results, [
            {"target_id": "期末!D10", "source_codes": ["660201"], "reason": "按管理费用归类", "amount": "999999"}], rows)
        self.assertEqual(applied, 0)
        self.assertTrue(notes)
        self.assertEqual(results[0].status, "ambiguous")
        rows = rows[:1]
        results = match_fields([target("其他")], rows)
        applied, notes = apply_ai_suggestions(results, [
            {"target_id": "期末!D10", "source_codes": ["660201"], "reason": "唯一候选", "amount": "999999"}], rows)
        self.assertEqual((applied, notes), (1, []))
        self.assertEqual(results[0].status, "matched")
        self.assertEqual(results[0].amount, Decimal(12))
        self.assertEqual(results[0].source_ids, ["660201"])

    def test_ai_cannot_touch_formula_or_foreign_source(self):
        results = match_fields([target(value="=SUM(D11:D14)")], [row()])
        applied, notes = apply_ai_suggestions(results, [{"target_id": "期末!D10", "source_codes": ["1001"]}], [row()])
        self.assertEqual(applied, 0)
        self.assertEqual(results[0].status, "protected")
        self.assertTrue(notes)

        plain = match_fields([target()], [row()])
        applied, _ = apply_ai_suggestions(plain, [{"target_id": "期末!D10", "source_codes": ["8888"]}], [row()])
        self.assertEqual(applied, 0)
        self.assertEqual(plain[0].status, "matched")

    def test_ai_suggestion_marks_overwrite_false(self):
        results = match_fields([target(value=0)], [row()])
        applied, _ = apply_ai_suggestions(results, [{"target_id": "期末!D10", "source_codes": ["1001"]}], [row()])
        self.assertEqual(applied, 1)
        self.assertFalse(results[0].overwrite)


class AuditScopeTests(unittest.TestCase):
    def test_offtopic_blocked_locally_without_request(self):
        client, calls = make_client(completion({"summary": "", "suggestions": []}))
        with self.assertRaises(AIError):
            client.converse("帮我写一首关于秋天的诗")
        self.assertEqual(calls, [])

    def test_audit_question_passes_local_filter(self):
        client, calls = make_client({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(
            {"intent": "audit", "answer": "按科目余额合计。", "proposals": []}, ensure_ascii=False)}}]})
        value = client.converse("本期管理费用为什么比上期多？")
        self.assertEqual(value["intent"], "audit")
        self.assertEqual(calls[0][0], "/chat/completions")
        self.assertEqual(calls[0][1]["messages"][0]["role"], "system")

    def test_model_declared_offtopic_drops_proposals(self):
        client, _ = make_client({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(
            {"intent": "offtopic", "answer": "本通道仅用于审计工作。",
             "proposals": [{"target_id": "期末!D10", "source_codes": ["1001"], "reason": "x"}]},
            ensure_ascii=False)}}]})
        # 这句不含审计词也不含离题词，本地判为 unknown 放行，由模型判定为离题
        value = client.converse("帮我总结一下这篇文章", context={"targets": [{"target_id": "期末!D10"}],
                                                        "candidates": [{"code": "1001"}]})
        self.assertEqual(value["intent"], "offtopic")
        self.assertEqual(value["proposals"], [])

    def test_proposal_outside_context_rejected(self):
        client, _ = make_client({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(
            {"intent": "audit", "answer": "x",
             "proposals": [{"target_id": "期末!D10", "source_codes": ["9999"], "reason": "x"}]},
            ensure_ascii=False)}}]})
        with self.assertRaises(AIError):
            client.converse("把这个科目对应到其他应收款", context={"targets": [{"target_id": "期末!D10"}],
                                                          "candidates": [{"code": "1001"}]})

    def test_empty_or_too_long_question_rejected(self):
        client, _ = make_client(completion({"summary": "", "suggestions": []}))
        with self.assertRaises(AIError):
            client.converse("   ")
        with self.assertRaises(AIError):
            client.converse("审计" * 2001)


class FactGroundingTests(unittest.TestCase):
    facts = {
        "subject": {"id": "S1", "year": 2026, "month": 6},
        "counts": {"accounts": 5, "leaves": 4},
        "movements": [{"code": "6602", "name": "管理费用", "path": "管理费用", "direction": "借",
                       "opening": "100000", "ending": "140000", "change": "40000",
                       "rate": "40.00%", "debit": None, "credit": None, "profit_loss": False}],
        "no_rate": [],
        "totals": {"debit": "0", "credit": "0"},
    }

    def test_numbers_outside_facts_are_flagged(self):
        answer = "管理费用（6602）由 100000 增至 140000，增加 40000；另有一笔 99999999 无法核对。"
        client, _ = make_client(completion({"intent": "audit", "answer": answer, "proposals": []}))
        value = client.converse("管理费用为什么增加？",
                                context={"targets": [], "candidates": [], "facts": self.facts})
        self.assertEqual(value["warnings"], ["99999999"])
        self.assertEqual(value["unknown_codes"], [])

    def test_invented_code_flagged(self):
        answer = "参见 6602.99 与 9999.01 的明细。"
        client, _ = make_client(completion({"intent": "audit", "answer": answer, "proposals": []}))
        facts = dict(self.facts, all_codes=["6602", "6602.99"])
        value = client.converse("管理费用明细？",
                                context={"targets": [], "candidates": [], "facts": facts})
        self.assertEqual(value["unknown_codes"], ["9999.01"])

    def test_without_facts_no_warning_check(self):
        client, _ = make_client(completion({"intent": "audit", "answer": "增加 88888888。", "proposals": []}))
        value = client.converse("管理费用为什么增加？", context={"targets": [], "candidates": []})
        self.assertEqual(value["warnings"], [])
        self.assertEqual(value["unknown_codes"], [])

    def test_facts_are_sent_to_model(self):
        client, calls = make_client(completion({"intent": "audit", "answer": "略", "proposals": []}))
        client.converse("管理费用为什么增加？",
                        context={"targets": [], "candidates": [], "facts": self.facts})
        body = calls[0][1]
        joined = " ".join(message["content"] for message in body["messages"])
        self.assertIn("facts", joined)
        self.assertIn("6602", joined)


if __name__ == "__main__":
    unittest.main()
