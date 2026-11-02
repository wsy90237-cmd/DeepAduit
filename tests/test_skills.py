import json
import tempfile
import unittest
from pathlib import Path

from tb_autofill import ai, skills


def completion(content):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content, ensure_ascii=False)}}]}


def make_client(answer, proposals=None):
    """假 transport：记录请求体，不联网。"""
    calls = []

    def transport(path, body):
        calls.append((path, body))
        return completion({"intent": "audit", "answer": answer, "proposals": proposals or []})

    return ai.DeepSeekClient("sk-test", "deepseek-chat", transport=transport), calls


def workspace_context():
    return {
        "targets": [{"target_id": "T1", "label": "库存现金", "parent": None, "period": "ending",
                     "basis": None, "status": "ok", "expected": "1", "actual": "1",
                     "difference": "0", "local_reason": "本地"}],
        "candidates": [{"code": "1001", "name": "库存现金", "path": "1001", "direction": "借", "basis": None}],
    }


def sent_context(call):
    """从第二次 user 消息里取回 context，确认注入内容真的发给了模型。"""
    body = call
    for message in body["messages"]:
        if message["role"] == "user" and message["content"].startswith("工作区数据"):
            return json.loads(message["content"].split("：", 1)[1])
    raise AssertionError("请求体里没有工作区数据")


class AssetTests(unittest.TestCase):
    def test_assets_present_and_within_size_budget(self):
        manifest = skills.manifest()
        self.assertEqual({item["name"] for item in manifest["skills"]},
                         {"chenyiwei-accounting", "ipo-financial-review", "china-audit"})
        self.assertLessEqual(manifest["total_bytes"], 6 * 1024 * 1024)
        for skill in manifest["skills"]:
            self.assertTrue(skill["origin"].endswith(skill["name"]))
            self.assertTrue(skill["files"])

    def test_load_documents_returns_structured_entries(self):
        entries = skills.load_documents()
        self.assertGreater(len(entries), 1000)
        self.assertEqual(len({item["id"] for item in entries}), len(entries))
        for entry in entries[:200]:
            self.assertEqual(set(entry), {"id", "skill", "skill_label", "path", "heading",
                                          "title", "text", "kind", "tags"})
            self.assertLessEqual(len(entry["text"]), skills.MAX_BODY)
            self.assertTrue(entry["text"].strip())
        self.assertEqual(skills.summary()["entries"], len(entries))

    def test_compressed_qa_is_loaded(self):
        entries = skills.load_documents()
        qa = [item for item in entries if item["kind"] == "qa"]
        self.assertTrue(qa)
        self.assertTrue(all(item["path"].endswith(".jsonl.gz") for item in qa))


class SearchTests(unittest.TestCase):
    def test_search_is_deterministic(self):
        first = skills.search("关联方资金占用", 5)
        second = skills.search("关联方资金占用", 5)
        self.assertTrue(first)
        self.assertEqual([item["id"] for item in first], [item["id"] for item in second])
        self.assertEqual([item["score"] for item in first], [item["score"] for item in second])
        self.assertEqual([item["score"] for item in first],
                         sorted((item["score"] for item in first), reverse=True))

    def test_search_hits_chinese_accounting_terms(self):
        cases = {
            "总额法和净额法怎么区分": ("总额法", "净额法"),
            "未分配利润为负数如何处理": ("未分配利润",),
            "关联方资金占用": ("资金占用", "关联方"),
        }
        for query, keywords in cases.items():
            hits = skills.search(query, 5)
            self.assertTrue(hits, query)
            joined = "\n".join(item["title"] + item["text"] for item in hits)
            self.assertTrue(any(word in joined for word in keywords), (query, keywords))
            self.assertLessEqual(len(hits), 5)

    def test_search_without_match_returns_empty_list(self):
        for query in ("量子纠缠态是什么", "写一首诗", ""):
            self.assertEqual(skills.search(query, 5), [], query)

    def test_search_limit_is_respected(self):
        self.assertLessEqual(len(skills.search("存货跌价准备", 2)), 2)
        self.assertLessEqual(len(skills.search("存货跌价准备", 20)), 20)

    def test_missing_assets_degrade_to_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            empty = Path(folder) / "skills"
            self.assertEqual(skills.load_documents(empty), [])
            self.assertEqual(skills.search("总额法", 5, base=empty), [])
            self.assertEqual(skills.manifest(empty), {})
            self.assertEqual(skills.summary(empty), {"entries": 0, "skills": {}})

    def test_entry_limit_is_enforced(self):
        entries = skills.load_documents(limit=7)
        self.assertLessEqual(len(entries), 7)
        self.assertTrue(entries)


class ReferenceTests(unittest.TestCase):
    def test_citation_ids_are_sequential_and_unique(self):
        entries = skills.search("收入确认时点", 5)
        refs = skills.as_knowledge_refs(entries)
        self.assertTrue(refs)
        self.assertEqual([ref["citation_id"] for ref in refs],
                         [f"K{index}" for index in range(1, len(refs) + 1)])
        self.assertEqual(len({ref["citation_id"] for ref in refs}), len(refs))
        for ref in refs:
            self.assertTrue(ref["title"] and ref["locator"] and ref["excerpt"])
            self.assertLessEqual(len(ref["excerpt"]), skills.MAX_BODY)

    def test_citation_ids_can_continue_after_existing_refs(self):
        entries = skills.search("存货跌价准备", 3)
        refs = skills.as_knowledge_refs(entries, start=3)
        self.assertEqual(refs[0]["citation_id"], "K3")
        self.assertEqual(skills.citation_ceiling([{"citation_id": "K2"}, {"citation_id": "bad"}]), 2)


class InjectionTests(unittest.TestCase):
    def test_converse_with_skills_injects_refs_and_accepts_known_citation(self):
        client, calls = make_client("按总额法确认[K1]，净额法只在代理关系下使用[K2]。",
                                    [{"target_id": "T1", "source_codes": ["1001"], "reason": "依据 K1"}])
        result = client.converse_with_skills("总额法和净额法怎么区分？", context=workspace_context())
        self.assertEqual(result["intent"], "audit")
        self.assertEqual(result["citation_warnings"], [])
        self.assertEqual([item["target_id"] for item in result["proposals"]], ["T1"])
        self.assertEqual([ref["citation_id"] for ref in result["knowledge_refs"]],
                         [f"K{index}" for index in range(1, result["skill_hits"] + 1)])
        payload = sent_context(calls[0][1])
        self.assertEqual([ref["citation_id"] for ref in payload["knowledge_refs"]],
                         [ref["citation_id"] for ref in result["knowledge_refs"]])
        self.assertIn("内置知识库命中", payload["knowledge_note"])

    def test_converse_with_skills_drops_proposals_on_unknown_citation(self):
        client, _ = make_client("依据[K9]处理。", [{"target_id": "T1", "source_codes": ["1001"], "reason": "x"}])
        result = client.converse_with_skills("总额法和净额法怎么区分？", context=workspace_context())
        self.assertEqual(result["proposals"], [])
        self.assertTrue(any("不存在" in warning for warning in result["citation_warnings"]))

    def test_existing_refs_keep_their_numbering_and_skills_continue_after(self):
        context = workspace_context()
        context["knowledge_refs"] = [{"citation_id": "K1", "title": "已选资料", "excerpt": "正文"}]
        client, calls = make_client("先看已选资料[K1]，再看内置条目[K2]。")
        result = client.converse_with_skills("总额法和净额法怎么区分？", context=context)
        self.assertEqual(result["knowledge_refs"][0]["citation_id"], "K2")
        payload = sent_context(calls[0][1])
        self.assertEqual([ref["citation_id"] for ref in payload["knowledge_refs"]][:2], ["K1", "K2"])
        self.assertEqual(result["citation_warnings"], [])

    def test_no_match_injects_note_not_refs(self):
        client, calls = make_client("内置知识库无匹配条目，需人工判断。")
        result = client.converse_with_skills("总额法和净额法怎么区分？", context=workspace_context(),
                                             finder=lambda query, limit=5: [])
        self.assertEqual(result["knowledge_refs"], [])
        self.assertEqual(result["skill_hits"], 0)
        self.assertIn("无匹配", result["knowledge_note"])
        payload = sent_context(calls[0][1])
        self.assertNotIn("knowledge_refs", payload)

    def test_offtopic_question_is_rejected_before_searching(self):
        seen = []

        def finder(query, limit=5):
            seen.append(query)
            return skills.search(query, limit)

        client, _ = make_client("不应到达模型")
        with self.assertRaises(ai.AIError):
            client.converse_with_skills("给我讲个笑话", context=workspace_context(), finder=finder)
        self.assertEqual(seen, [])

    def test_offline_search_failure_does_not_block_answer(self):
        def broken(query, limit=5):
            raise OSError("资产损坏")

        client, _ = make_client("内网无法检索，按已给数据分析。")
        result = client.converse_with_skills("存货跌价准备怎么计提？", context=workspace_context(), finder=broken)
        self.assertEqual(result["intent"], "audit")
        self.assertEqual(result["proposals"], [])
        self.assertIn("无匹配", result["knowledge_note"])


if __name__ == "__main__":
    unittest.main()
