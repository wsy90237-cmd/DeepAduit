import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

from pypdf import PdfWriter
from tb_autofill import ai, knowledge, settings, workflow
from test_ai import completion


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "settings.json"
        self.store = settings.SettingsStore(self.path)

    def test_model_secret_is_encrypted_and_bound_to_endpoint(self):
        self.store.save_ai("synthetic-model-secret", enabled=True, model="model-a")
        self.assertNotIn("synthetic-model-secret", self.path.read_text())
        self.assertEqual(self.store.get_key(), "synthetic-model-secret")
        with self.assertRaisesRegex(settings.SettingsError, "复用"):
            self.store.get_key("https://other.example/v1")
        with self.assertRaises(settings.SettingsError):
            self.store.save_ai(None, enabled=True, model="model-b", base_url="https://other.example/v1")
        self.assertEqual(self.store.data["base_url"], settings.DEFAULT_BASE_URL)

    def test_new_service_requires_and_uses_its_own_key(self):
        self.store.save_ai("synthetic-old-secret", enabled=True)
        self.store.save_ai("synthetic-new-secret", enabled=True, model="custom-model", base_url="https://OTHER.example/v1/")
        client = ai.DeepSeekClient.from_store(self.store)
        self.assertEqual(client.base_url, "https://other.example/v1")
        self.assertEqual(client.key, "synthetic-new-secret")
        self.assertNotIn("synthetic-new-secret", self.path.read_text())

    def test_legacy_dpapi_storage_is_read_without_exposing_key(self):
        self.store.save_ai("synthetic-legacy-secret", enabled=True)
        saved = json.loads(self.path.read_text())
        saved = {key: value for key, value in saved.items() if key not in {"base_url", "key_base_url"}}
        self.path.write_text(json.dumps(saved))
        self.assertEqual(settings.SettingsStore(self.path).get_key(), "synthetic-legacy-secret")

    def test_ima_credentials_are_separate_encrypted_and_removable(self):
        self.store.save_ai("synthetic-model-secret", enabled=True)
        self.store.save_ima("synthetic-client-id", "synthetic-ima-secret")
        reopened = settings.SettingsStore(self.path)
        self.assertEqual(reopened.get_ima(), ("synthetic-client-id", "synthetic-ima-secret"))
        self.assertNotIn("synthetic-client-id", self.path.read_text())
        self.assertNotIn("synthetic-ima-secret", self.path.read_text())
        reopened.delete_ima()
        self.assertFalse(reopened.has_ima)
        self.assertEqual(reopened.get_key(), "synthetic-model-secret")

    def test_failed_atomic_save_preserves_previous_key_and_file(self):
        self.store.save_ai("synthetic-original", enabled=True)
        before = self.path.read_bytes()
        with patch.object(settings.os, "replace", side_effect=PermissionError("synthetic failure")):
            with self.assertRaises(settings.SettingsError):
                self.store.save_ai("synthetic-replacement", enabled=True)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.get_key(), "synthetic-original")

    def test_default_connection_file_is_isolated_from_legacy_app(self):
        base = Path(self.folder.name) / "ZSZH-TB"
        base.mkdir()
        legacy = base / "settings.json"
        old = settings.SettingsStore(legacy)
        old.save_ai("synthetic-old-key", enabled=True)
        before = legacy.read_bytes()
        with patch.dict(settings.os.environ, {"LOCALAPPDATA": self.folder.name}):
            current = settings.SettingsStore()
            self.assertEqual(current.get_key(), "synthetic-old-key")
            current.save_ai("synthetic-custom-key", enabled=True, base_url="https://compatible.example/v1")
            self.assertEqual(current.path.name, "connections.json")
        self.assertEqual(legacy.read_bytes(), before)
        self.assertEqual(settings.SettingsStore(legacy).get_key(), "synthetic-old-key")

    def test_failed_directory_creation_rolls_back_settings(self):
        before = dict(self.store.data)
        with patch.object(settings.Path, "mkdir", side_effect=PermissionError("synthetic denied")):
            with self.assertRaises(settings.SettingsError):
                self.store.save_ai("synthetic-key", enabled=True)
        self.assertEqual(self.store.data, before)

    def test_invalid_urls_and_header_secrets_are_rejected(self):
        for endpoint in ("http://example.com", "https://u:p@example.com", "https://example.com?key=x", "https://example.com/#key", "https://exa\x00mple.com"):
            with self.subTest(endpoint=endpoint), self.assertRaises(settings.SettingsError):
                settings.normalize_base_url(endpoint)
        for key in ("", "key\nInjected: value", "a" * 4097):
            with self.subTest(key_length=len(key)), self.assertRaises(ai.AIError):
                ai.DeepSeekClient(key)


class ModelConnectionTests(unittest.TestCase):
    def test_model_and_ima_redirects_never_forward_credentials(self):
        for handler, error in ((ai._NoRedirect(), ai.AIError), (knowledge._NoRedirect(), knowledge.KnowledgeError)):
            with self.subTest(handler=type(handler).__module__), self.assertRaises(error):
                handler.redirect_request(None, None, 302, "redirect", {}, "https://other.example")

    def test_compatible_request_does_not_send_deepseek_specific_thinking(self):
        calls = []
        client = ai.DeepSeekClient("synthetic-key", "custom-model", base_url="https://compatible.example/v1",
            transport=lambda path, body: calls.append((path, body)) or completion({"summary": "无", "suggestions": []}))
        client.assist({"targets": [], "candidates": []})
        self.assertNotIn("thinking", calls[0][1])
        self.assertNotIn("synthetic-key", json.dumps(calls))

    def test_chat_capability_requires_valid_json_and_complete_finish(self):
        valid = ai.DeepSeekClient("synthetic-key", transport=lambda *_: completion({"ok": True}))
        self.assertTrue(valid.probe_chat())
        invalid = ai.DeepSeekClient("synthetic-key", transport=lambda *_: completion({"ok": False}))
        with self.assertRaises(ai.AIError):
            invalid.probe_chat()

    def test_http_error_does_not_echo_server_body_or_key(self):
        secret = "synthetic-sensitive-secret"
        error = urllib.error.HTTPError("https://api.deepseek.com/models", 401, secret, {}, io.BytesIO(secret.encode()))
        with patch.object(ai.urllib.request, "build_opener") as factory:
            factory.return_value.open.side_effect = error
            with self.assertRaises(ai.AIError) as caught:
                ai.DeepSeekClient(secret).list_models()
        self.assertNotIn(secret, str(caught.exception))

    def test_missing_or_invented_knowledge_citations_drop_proposals(self):
        context = {"targets": [{"target_id": "Synthetic!B2"}], "candidates": [{"code": "1001"}],
                   "knowledge_refs": [{"citation_id": "K1", "title": "Synthetic", "excerpt": "合成参考"}]}
        proposals = [{"target_id": "Synthetic!B2", "source_codes": ["1001"], "reason": "合成"}]
        for answer in ("依据[K99]建议调整。", "建议调整但没有引用。"):
            with self.subTest(answer=answer):
                client = ai.DeepSeekClient("synthetic-key", transport=lambda *_: completion({"answer": answer, "proposals": proposals}))
                result = client.converse("请复核审计TB", context=context)
                self.assertEqual(result["proposals"], [])
                self.assertTrue(result["citation_warnings"])


class ImaReadOnlyTests(unittest.TestCase):
    def client(self, data, code=0):
        self.calls = []
        def transport(method, payload):
            self.calls.append((method, payload))
            return {"code": code, "msg": "synthetic-secret-must-not-echo", "data": data}
        return knowledge.ImaClient("synthetic-client", "synthetic-key", transport=transport)

    def test_official_search_fields_and_pagination_are_preserved(self):
        client = self.client({"info_list": [{"id": "KB1", "name": "合成财会库"}], "is_end": False, "next_cursor": "next"})
        items, cursor = client.search_libraries("财会")
        self.assertEqual(items[0]["id"], "KB1")
        self.assertEqual(cursor, "next")
        self.assertEqual(self.calls, [("search_knowledge_base", {"query": "财会", "cursor": "", "limit": 20})])

    def test_content_excerpt_is_plain_text_and_title_only_is_explicit(self):
        client = self.client({"info_list": [{"media_id": "M1", "title": "合成", "highlight_content": "<em>坏账</em>准备"},
                                          {"media_id": "M2", "title": "无正文"}], "is_end": True})
        refs, cursor = client.search("KB1", "坏账")
        self.assertIn("坏账", refs[0]["excerpt"])
        self.assertNotIn("<em>", refs[0]["excerpt"])
        self.assertEqual(refs[1]["excerpt"], "")
        self.assertEqual(cursor, "")

    def test_permission_failure_is_not_treated_as_empty_success(self):
        client = self.client({}, code=110030)
        with self.assertRaises(knowledge.KnowledgeError) as caught:
            client.search("KB1", "坏账")
        self.assertIn("无权", str(caught.exception))
        self.assertNotIn("synthetic-secret", str(caught.exception))

    def test_incomplete_pagination_and_write_operations_are_rejected(self):
        client = self.client({"info_list": [], "is_end": False})
        with self.assertRaises(knowledge.KnowledgeError):
            client.search_libraries("财会")
        with self.assertRaisesRegex(knowledge.KnowledgeError, "只读"):
            client._post("add_knowledge", {})


class LocalReferenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.library = knowledge.LocalLibrary(self.root / "library.sqlite3")

    def test_import_dedup_and_search_preserve_content_identity(self):
        path = self.root / "坏账准备.md"
        path.write_text("前言" * 800 + "\n坏账准备的合成测试依据：核对估计和证据。", encoding="utf-8")
        digest, added = self.library.import_file(path)
        self.assertTrue(added)
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertFalse(self.library.import_file(path)[1])
        refs = self.library.search("坏账准备")
        self.assertEqual(len(refs), 1)
        self.assertIn("合成测试依据", refs[0]["excerpt"])
        self.assertEqual(len(self.library.list_documents()), 1)

    def test_docx_text_and_xml_entity_rejection(self):
        path = self.root / "synthetic.docx"
        xml = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic audit evidence</w:t></w:r></w:p></w:body></w:document>'
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("word/document.xml", xml)
        self.assertIn("Synthetic audit evidence", knowledge.read_document(path)[0])
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("word/document.xml", b'<!DOCTYPE x [<!ENTITY x "synthetic">]><x>&x;</x>')
        with self.assertRaises(knowledge.KnowledgeError):
            knowledge.read_document(path)

    def test_blank_or_encrypted_pdf_is_not_claimed_as_ocr(self):
        path = self.root / "synthetic.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with path.open("wb") as stream:
            writer.write(stream)
        with self.assertRaisesRegex(knowledge.KnowledgeError, "OCR"):
            knowledge.read_document(path)
        writer.encrypt("synthetic-password")
        with path.open("wb") as stream:
            writer.write(stream)
        with self.assertRaisesRegex(knowledge.KnowledgeError, "加密"):
            knowledge.read_document(path)

    def test_reference_import_never_changes_source(self):
        path = self.root / "synthetic.txt"
        path.write_bytes("合成审计依据，不是实际客户资料。".encode("gb18030"))
        before = path.read_bytes()
        self.library.import_file(path)
        self.assertEqual(path.read_bytes(), before)


class WorkflowReadinessTests(unittest.TestCase):
    def test_balance_and_references_alone_never_authorize_report_issuance(self):
        state = workflow.readiness([], [], references=[{"title": "合成规则"}])
        self.assertFalse(state["ready_to_issue"])
        self.assertTrue(any(item["status"] == "未就绪" for item in state["checks"]))

    def test_readiness_export_escapes_untrusted_titles_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "readiness.html"
            state = workflow.readiness([], [], references=[{"title": "<script>synthetic</script>"}])
            workflow.export_readiness(path, state)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("<script>", text)
            self.assertIn("&lt;script&gt;", text)
            self.assertIn("不是审计意见", text)
            with self.assertRaises(FileExistsError):
                workflow.export_readiness(path, state)


if __name__ == "__main__":
    unittest.main()
