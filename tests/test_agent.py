import json
import os
import shutil
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from tb_autofill.agent import (AgentContext, AgentError, AgentRuntime, ApprovalDecision,
                               ProjectSnapshot, ToolRegistry, ToolSpec)


def snapshot(results=(), rows=()):
    subject = SimpleNamespace(id="S1", name="合成企业", year=2026, month=12, ledger_id="L1")
    return ProjectSnapshot(subject=subject, rows=tuple(rows), subject_label="合成企业 / 2026-12",
                           template_path="synthetic.xlsx", unit="元", periods=("ending",),
                           scan=None, results=tuple(results), paper_library=(),
                           app_revision=1, ledger_digest="ledger", template_digest="template")


def tool_call(name, arguments=None, call_id="call-1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False)}}


def reply(content="", calls=()):
    normalized = []
    for raw in calls:
        normalized.append({
            "id": raw["id"],
            "name": raw["function"]["name"],
            "arguments": json.loads(raw["function"]["arguments"] or "{}"),
            "raw": raw,
        })
    return {"content": content, "tool_calls": normalized,
            "finish_reason": "tool_calls" if calls else "stop"}


class FakeClient:
    model = "deepseek-flash"
    base_url = "https://api.deepseek.com"

    def __init__(self, replies):
        self.replies = list(replies)
        self.messages = []
        self.tools = []

    def chat_tools(self, messages, tools):
        self.messages.append([dict(item) for item in messages])
        self.tools.append(tools)
        if not self.replies:
            raise AgentError("没有更多脚本化回复")
        return self.replies.pop(0)


def noop(context, arguments, runtime):
    return {"ok": True, "summary": "已读取"}


def make_registry(*, writes=None, extra=None):
    specs = [
        ToolSpec("get_project_context", "读取项目摘要", {"type": "object", "properties": {},
                 "additionalProperties": False}, "read", noop, lambda _args: "读取项目"),
        ToolSpec("finish", "结束", {"type": "object", "properties": {
            "status": {"type": "string"}, "summary": {"type": "string"},
            "deliverables": {"type": "array"}, "gaps": {"type": "array"}},
            "additionalProperties": False}, "user", noop, lambda _args: "结束"),
    ]
    if writes:
        specs.append(writes)
    if extra:
        specs.extend(extra)
    return ToolRegistry(specs)


class RuntimeTests(unittest.TestCase):
    def run_agent(self, replies, *, registry=None, approve=None, ask=None, current=None,
                  max_steps=20):
        context = snapshot().context(current_fingerprint=current, max_steps=max_steps)
        client = FakeClient(replies)
        runtime = AgentRuntime(client, registry or make_registry(), context,
                               approve=approve, ask_user=ask, audit=lambda *_: None)
        return runtime.run(), client

    def test_read_tool_then_finish(self):
        result, client = self.run_agent([
            reply(calls=[tool_call("get_project_context")]),
            reply(calls=[tool_call("finish", {"status": "done", "summary": "完成"})]),
        ])
        self.assertEqual(result.status, "done")
        self.assertEqual(result.summary, "完成")
        self.assertTrue(result.artifacts == ())
        self.assertEqual(len(client.messages), 2)
        self.assertIn("tool", {item["role"] for item in client.messages[1]})

    def test_finish_needs_input_saves_pause_and_resume_writes_end_audit_event(self):
        saved = []
        events = []
        client = FakeClient([
            reply(calls=[tool_call("finish", {"status": "needs_input", "summary": "请确认范围。"})]),
            reply(content="已完成"),
        ])
        runtime = AgentRuntime(client, make_registry(), snapshot().context(),
                               save_pause=lambda state: saved.append(state) or Path("synthetic.json"))
        result = runtime.run()
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(runtime.pending_path, Path("synthetic.json"))
        state, = saved
        self.assertEqual(state["question"], "请确认范围。")
        resumed = AgentRuntime(client, make_registry(), snapshot().context(),
                               audit=lambda event, data: events.append((event, data)))
        self.assertEqual(resumed.run(state, "确认").status, "done")
        self.assertEqual(events[-1], ("agent_run_end", {"status": "done", "artifacts": 0}))

    def test_denied_write_does_not_execute_and_is_not_retried(self):
        calls = []

        def write(context, arguments, runtime):
            calls.append(arguments)
            return {"ok": True, "summary": "写入了"}

        spec = ToolSpec("export_tb_copy", "写 TB", {"type": "object", "properties": {},
                       "additionalProperties": False}, "write", write, lambda _args: "准备写 TB")

        def approve(request):
            return ApprovalDecision(False, note="用户拒绝")

        result, _ = self.run_agent([
            reply(calls=[tool_call("export_tb_copy")]),
            reply(calls=[tool_call("export_tb_copy", call_id="call-2")]),
            reply(calls=[tool_call("finish", {"status": "done", "summary": "结束"})]),
        ], registry=make_registry(writes=spec), approve=approve)
        self.assertEqual(calls, [])
        self.assertIn("已拒绝", "".join(event.title for event in result.events))

    def test_approved_write_uses_selected_path(self):
        calls = []

        def write(context, arguments, runtime):
            calls.append(runtime.approved_path)
            return {"ok": True, "summary": "写入了"}

        spec = ToolSpec("export_tb_copy", "写 TB", {"type": "object", "properties": {},
                       "additionalProperties": False}, "write", write, lambda _args: "准备写 TB")
        result, _ = self.run_agent([
            reply(calls=[tool_call("export_tb_copy")]),
            reply(calls=[tool_call("finish", {"status": "done", "summary": "完成"})]),
        ], registry=make_registry(writes=spec),
            approve=lambda _request: ApprovalDecision(True, path="synthetic-out.xlsx"))
        self.assertEqual(calls, ["synthetic-out.xlsx"])
        self.assertEqual(result.status, "done")

    def test_unknown_tool_returns_error_to_model(self):
        result, client = self.run_agent([
            reply(calls=[tool_call("run_shell")]),
            reply(calls=[tool_call("finish", {"status": "done", "summary": "结束"})]),
        ])
        self.assertEqual(result.status, "done")
        observation = client.messages[1][-1]
        self.assertEqual(observation["role"], "tool")
        self.assertIn("未授权", observation["content"])

    def test_argument_validation_blocks_extra_keys(self):
        called = []
        spec = ToolSpec("get_project_context", "读取", {"type": "object", "properties": {},
                       "additionalProperties": False}, "read",
                       lambda context, arguments, runtime: called.append(arguments) or {"ok": True},
                       lambda _args: "读取")
        result, client = self.run_agent([
            reply(calls=[tool_call("get_project_context", {"path": "C:/secret"})]),
            reply(calls=[tool_call("finish", {"status": "done", "summary": "结束"})]),
        ], registry=ToolRegistry([spec, make_registry().get("finish")]))
        self.assertEqual(called, [])
        self.assertIn("未定义参数", client.messages[1][-1]["content"])

    def test_stale_context_stops_run(self):
        state = {"calls": 0}

        def current():
            state["calls"] += 1
            return ("a",) if state["calls"] == 1 else ("b",)

        context = snapshot().context(current_fingerprint=current)
        client = FakeClient([reply(calls=[tool_call("get_project_context")])])
        runtime = AgentRuntime(client, make_registry(), context, audit=lambda *_: None)
        result = runtime.run()
        self.assertEqual(result.status, "stale")
        self.assertIn("变化", result.error or result.summary)

    def test_budget_stops_infinite_tool_loop(self):
        result, client = self.run_agent([
            reply(calls=[tool_call("get_project_context", call_id=f"c{index}")])
            for index in range(10)
        ], max_steps=1)
        self.assertEqual(result.status, "error")
        self.assertIn("上限", result.error or result.summary)

    def test_run_result_does_not_contain_credentials(self):
        result, _ = self.run_agent([
            reply(calls=[tool_call("finish", {"status": "done", "summary": "完成"})]),
        ])
        text = json.dumps(result.as_dict(), ensure_ascii=False)
        self.assertNotIn("sk-", text)

    def test_model_done_is_downgraded_when_required_artifacts_missing(self):
        context = snapshot().context(stages=("tb", "papers"), report_indexes=("1-010",))
        client = FakeClient([reply(calls=[tool_call("finish", {
            "status": "done", "summary": "全部完成"})])])
        result = AgentRuntime(client, make_registry(), context, audit=lambda *_: None).run()
        self.assertEqual(result.status, "partial")
        self.assertIn("TB 副本未生成", result.summary)
        self.assertIn("整册底稿未生成：1-010", result.summary)

    def test_local_artifacts_make_done_status(self):
        base = Path(__file__).resolve().parents[1] / "build"
        base.mkdir(parents=True, exist_ok=True)
        folder = base / f"test-agent-finish-{os.getpid()}-{time.time_ns()}"
        folder.mkdir()
        self.addCleanup(shutil.rmtree, folder, True)
        tb = folder / "tb.xlsx"
        pending = folder / "pending.csv"
        paper = folder / "paper.xlsx"
        for path in (tb, pending, paper):
            path.write_bytes(b"synthetic")
        context = snapshot().context(stages=("tb", "papers"), report_indexes=("1-010",))
        context.tb_exported = True
        context.record_artifact("tb", tb, target="tb")
        context.record_artifact("tb_pending", pending, target="tb_pending")
        context.record_artifact("workpaper", paper, target="1-010")
        client = FakeClient([reply(calls=[tool_call("finish", {
            "status": "done", "summary": "全部完成"})])])
        result = AgentRuntime(client, make_registry(), context, audit=lambda *_: None).run()
        self.assertEqual(result.status, "done", result.summary)
        self.assertEqual(len(result.artifacts), 3)

    def test_tool_result_scope_is_checked_before_sending_to_model(self):
        def secret(context, arguments, runtime):
            return {"ok": True, "summary": "序时账摘要", "data": {"secret": "journal-secret"}}

        spec = ToolSpec("get_journal", "读取序时账", {"type": "object", "properties": {},
                       "additionalProperties": False}, "read", secret,
                       lambda _args: "读取序时账", result_scope=("journal",))
        approvals = []

        def approve_scope(extra):
            approvals.append(set(extra))
            return False

        context = snapshot().context(allowed_scope={"tb"}, scope_approver=approve_scope)
        client = FakeClient([
            reply(calls=[tool_call("get_journal")]),
            reply(calls=[tool_call("finish", {"status": "done", "summary": "结束"})]),
        ])
        result = AgentRuntime(client, make_registry(extra=[spec]), context, audit=lambda *_: None).run()
        self.assertEqual(approvals, [{"journal"}])
        self.assertNotIn("journal-secret", client.messages[1][-1]["content"])
        self.assertIn("未同意", client.messages[1][-1]["content"])

    def test_failed_tool_result_cannot_carry_unauthorized_data(self):
        def failed(context, arguments, runtime):
            return {"ok": False, "error": "失败", "data": {"secret": "JOURNAL-SECRET"}}

        spec = ToolSpec("get_journal", "读取序时账", {"type": "object", "properties": {},
                       "additionalProperties": False}, "read", failed,
                       lambda _args: "读取序时账", result_scope=("journal",))
        context = snapshot().context(allowed_scope={"tb"}, scope_approver=lambda _extra: False)
        client = FakeClient([
            reply(calls=[tool_call("get_journal")]),
            reply(calls=[tool_call("finish", {"status": "done", "summary": "结束"})]),
        ])
        AgentRuntime(client, make_registry(extra=[spec]), context, audit=lambda *_: None).run()
        self.assertNotIn("JOURNAL-SECRET", client.messages[1][-1]["content"])

    def test_finish_schema_is_validated_before_completion(self):
        context = snapshot().context(stages=())
        client = FakeClient([
            reply(calls=[tool_call("finish", {"status": "done", "summary": "完成",
                                              "injected_path": "C:/outside.xlsx"})]),
            reply(calls=[tool_call("finish", {"status": "done", "summary": "完成"})]),
        ])
        result = AgentRuntime(client, make_registry(), context, audit=lambda *_: None).run()
        self.assertEqual(result.status, "done")
        self.assertIn("未定义参数", client.messages[1][-1]["content"])

    def test_cancel_during_write_approval_prevents_write(self):
        calls = []

        def write(context, arguments, runtime):
            calls.append("written")
            return {"ok": True, "summary": "写入了"}

        spec = ToolSpec("export_tb_copy", "写 TB", {"type": "object", "properties": {},
                       "additionalProperties": False}, "write", write, lambda _args: "准备写 TB")
        context = snapshot().context(stages=("tb",))

        def approve(_request):
            context.cancelled.set()
            return ApprovalDecision(True, path="synthetic-out.xlsx")

        result = AgentRuntime(FakeClient([
            reply(calls=[tool_call("export_tb_copy")]),
        ]), make_registry(writes=spec), context, approve=approve,
            audit=lambda *_: None).run()
        self.assertEqual(calls, [])
        self.assertEqual(result.status, "cancelled")

    def test_ask_user_cancellation_aborts(self):
        asked = []

        def ask(question, options):
            asked.append((question, options))
            return None

        spec = ToolSpec("ask_user", "询问", {"type": "object", "properties": {
            "question": {"type": "string"}, "options": {"type": "array"}},
            "required": ["question"], "additionalProperties": False}, "user",
            lambda context, arguments, runtime: {"ok": True, "answer": runtime._ask_user(
                arguments["question"], arguments.get("options") or ())},
            lambda args: args.get("question", "询问"))
        result, _ = self.run_agent([
            reply(calls=[tool_call("ask_user", {"question": "做哪些项目？"})]),
        ], registry=ToolRegistry([spec, make_registry().get("finish")]), ask=ask)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(len(asked), 1)


if __name__ == "__main__":
    unittest.main()
