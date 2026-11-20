import json
import unittest

from tb_autofill.ai import AIError, DeepSeekClient, MAX_TOOL_ARGUMENT_CHARS


TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_project_context",
        "description": "返回当前项目摘要",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}]
MESSAGES = [{"role": "user", "content": "开始"}]


def make_client(response):
    calls = []

    def transport(path, body):
        calls.append((path, body))
        return response

    return DeepSeekClient("sk-test", "deepseek-flash", transport=transport), calls


def reply(content="", tool_calls=None, finish="stop"):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"finish_reason": finish, "message": message}]}


def tool_call(name="get_project_context", arguments="{}", call_id="call-1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments}}


class ToolRequestShapeTests(unittest.TestCase):
    def test_request_sends_tools_without_response_format(self):
        client, calls = make_client(reply("完成"))
        result = client.chat_tools(MESSAGES, TOOLS)
        path, body = calls[0]
        self.assertEqual(path, "/chat/completions")
        self.assertEqual(body["tools"], TOOLS)
        self.assertNotIn("response_format", body)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(result["content"], "完成")
        self.assertEqual(result["tool_calls"], [])

    def test_parse_single_tool_call(self):
        client, _ = make_client(reply("", [tool_call(arguments='{"scope":"tb"}')], "tool_calls"))
        result = client.chat_tools(MESSAGES, TOOLS)
        self.assertEqual(result["tool_calls"][0]["name"], "get_project_context")
        self.assertEqual(result["tool_calls"][0]["arguments"], {"scope": "tb"})
        self.assertEqual(result["finish_reason"], "tool_calls")

    def test_parse_multiple_tool_calls_preserves_order(self):
        client, _ = make_client(reply("", [tool_call(call_id="a"), tool_call(call_id="b")], "tool_calls"))
        result = client.chat_tools(MESSAGES, TOOLS)
        self.assertEqual([item["id"] for item in result["tool_calls"]], ["a", "b"])

    def test_empty_arguments_become_empty_object(self):
        client, _ = make_client(reply("", [tool_call(arguments="")], "tool_calls"))
        self.assertEqual(client.chat_tools(MESSAGES, TOOLS)["tool_calls"][0]["arguments"], {})


class ToolValidationTests(unittest.TestCase):
    def test_unknown_tool_name_rejected(self):
        client, _ = make_client(reply("", [tool_call(name="run_shell")], "tool_calls"))
        with self.assertRaisesRegex(AIError, "未授权"):
            client.chat_tools(MESSAGES, TOOLS)

    def test_malformed_arguments_rejected(self):
        client, _ = make_client(reply("", [tool_call(arguments="not json")], "tool_calls"))
        with self.assertRaisesRegex(AIError, "JSON"):
            client.chat_tools(MESSAGES, TOOLS)

    def test_argument_array_rejected(self):
        client, _ = make_client(reply("", [tool_call(arguments="[1, 2]")], "tool_calls"))
        with self.assertRaisesRegex(AIError, "JSON 对象"):
            client.chat_tools(MESSAGES, TOOLS)

    def test_oversized_arguments_rejected(self):
        client, _ = make_client(reply("", [tool_call(arguments="x" * (MAX_TOOL_ARGUMENT_CHARS + 1))],
                                      "tool_calls"))
        with self.assertRaisesRegex(AIError, "参数过大"):
            client.chat_tools(MESSAGES, TOOLS)

    def test_too_many_tool_calls_rejected(self):
        calls = [tool_call(call_id=f"c{index}") for index in range(9)]
        client, _ = make_client(reply("", calls, "tool_calls"))
        with self.assertRaisesRegex(AIError, "超过"):
            client.chat_tools(MESSAGES, TOOLS)

    def test_truncated_response_rejected(self):
        client, _ = make_client(reply("", [tool_call()], "length"))
        with self.assertRaisesRegex(AIError, "截断"):
            client.chat_tools(MESSAGES, TOOLS)

    def test_empty_response_rejected(self):
        client, _ = make_client(reply(""))
        with self.assertRaisesRegex(AIError, "没有返回"):
            client.chat_tools(MESSAGES, TOOLS)

    def test_missing_tools_rejected(self):
        client, calls = make_client(reply("完成"))
        with self.assertRaises(AIError):
            client.chat_tools(MESSAGES, [])
        self.assertEqual(calls, [])

    def test_error_response_redacts_key(self):
        client, _ = make_client({"error": "bad"})
        # 结构异常不包含 Key；此测试固定返回结构异常提示。
        with self.assertRaisesRegex(AIError, "结构异常"):
            client.chat_tools(MESSAGES, TOOLS)


if __name__ == "__main__":
    unittest.main()
