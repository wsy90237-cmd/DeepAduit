"""审计 Agent 任务范围推断与运行预算的回归测试（不启动 Tk 主循环、不联网）。"""

from __future__ import annotations

import os
import shutil
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tb_autofill import agent_tools
from tb_autofill.agent import AgentError, AgentRuntime, ProjectSnapshot, ToolRegistry, ToolSpec
from tb_autofill.models import Subject

try:
    from tb_autofill import agent_ui
except ImportError:  # 没有 tkinter 的环境只跳过界面相关用例
    agent_ui = None


@unittest.skipIf(agent_ui is None, "需要 tkinter")
class InferScopeTests(unittest.TestCase):
    def test_chat_routes_paper_tb_and_read_only_requests(self):
        for text, expected in (
                ("做 1-010 货币资金底稿", (("papers",), ("1-010",))),
                ("制作货币资金底稿，不做 TB", (("papers",), ("1-010",))),
                ("制作底稿", (("papers",), ())),
                ("复核 TB", (("tb",), ())),
                ("复核 TB，不导出", ((), ())),
                ("生成 TB，底稿已经有了", (("tb",), ())),
                ("复核 TB 并制作货币资金底稿", (("tb", "papers"), ("1-010",))),
                ("查看余额后生成货币资金底稿", (("papers",), ("1-010",))),
                ("列出可做的底稿", ((), ())),
                ("货币资金期末余额多少？", ((), ()))):
            with self.subTest(text=text):
                self.assertEqual(agent_ui._infer_request(text, ()), expected)

    def test_index_next_to_chinese_and_multiple_indexes(self):
        self.assertEqual(agent_ui._infer_request("做货币资金1-010和1-050底稿，1-010 重点看", ()),
                         (("papers",), ("1-010", "1-050")))

    def test_phone_like_numbers_are_not_indexes(self):
        self.assertEqual(agent_ui._infer_report_indexes("电话 021-12345678"), [])


def _snapshot():
    subject = Subject("S1", "合成企业", 2026, 12, ".", ledger_id="L1")
    return ProjectSnapshot(subject=subject, rows=(), subject_label="合成企业 / 2026-12",
                           template_path="", unit="元", periods=("ending",), scan=None,
                           results=(), paper_library=())


def _noop(context, arguments, runtime):
    return {"ok": True, "summary": "已读取"}


class _Client:
    model = "synthetic"
    base_url = "https://example.invalid"

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = 0

    def chat_tools(self, messages, tools):
        self.sent += 1
        if not self.replies:
            raise AgentError("没有更多脚本化回复")
        return self.replies.pop(0)


def _call(name, arguments, call_id):
    return {"id": call_id, "name": name, "arguments": arguments,
            "raw": {"id": call_id, "type": "function",
                    "function": {"name": name, "arguments": "{}"}}}


class BudgetTests(unittest.TestCase):
    def test_model_request_limit_is_checked_before_sending(self):
        look = ToolSpec("look", "读取", {"type": "object", "properties": {"q": {"type": "string"}},
                                         "additionalProperties": False}, "read", _noop)
        replies = [{"content": "", "tool_calls": [_call("look", {"q": str(i)}, f"c{i}")]}
                   for i in range(10)]
        client = _Client(replies)
        context = _snapshot().context(max_model_calls=3, max_steps=50, max_tool_calls=100)
        result = AgentRuntime(client, ToolRegistry([look]), context, audit=lambda *_: None).run()
        self.assertEqual(result.status, "error")
        self.assertIn("模型请求上限", result.error)
        self.assertEqual(client.sent, 3)

    def test_broken_tool_summary_does_not_stop_run(self):
        def broken(_args):
            raise RuntimeError("summary failed")
        look = ToolSpec("look", "读取", {"type": "object", "properties": {},
                                         "additionalProperties": False}, "read", _noop, broken)
        finish = ToolSpec("finish", "结束", {"type": "object", "properties": {
            "status": {"type": "string"}, "summary": {"type": "string"}},
            "additionalProperties": False}, "user", _noop)
        client = _Client([{"content": "", "tool_calls": [_call("look", {}, "a")]},
                          {"content": "", "tool_calls": [_call("finish", {"status": "done",
                                                                           "summary": "完成"}, "b")]}])
        context = _snapshot().context()
        result = AgentRuntime(client, ToolRegistry([look, finish]), context,
                              audit=lambda *_: None).run()
        self.assertEqual(result.status, "done")


class WorkpaperExportTests(unittest.TestCase):
    def test_export_does_not_overwrite_file_created_after_check(self):
        base = Path(__file__).resolve().parents[1] / "build"
        base.mkdir(parents=True, exist_ok=True)
        root = base / f"test-agent-scope-{os.getpid()}-{time.time_ns()}"
        root.mkdir()
        self.addCleanup(shutil.rmtree, root, True)
        target = root / "1-010 货币资金-底稿.xlsx"
        paper = SimpleNamespace(index_no="1-010", name="货币资金", label="1-010 货币资金")
        data = SimpleNamespace(source_path="", rows=(), journal=None, source_fingerprint=())
        context = _snapshot().context(output_dir=str(root))
        context.prepared_workpapers["1-010"] = {"paper": paper, "data": data,
                                               "draft": {"kind": "local"}, "warnings": []}
        context.evidence_rows["1-010"] = ("证据",)

        def build(path, **_kwargs):
            Path(path).write_bytes(b"new")
            target.write_bytes(b"user file")  # 检查之后、发布之前出现的同名文件

        runtime = SimpleNamespace(approved_path=str(target))
        with patch.object(agent_tools.dxn_paper, "build", build), \
                patch.object(agent_tools.os, "rename", side_effect=FileExistsError):
            result = agent_tools.export_workpaper(context, {"index_no": "1-010"}, runtime)
        self.assertFalse(result["ok"])
        self.assertIn("已存在", result["error"])
        self.assertEqual(target.read_bytes(), b"user file")
        self.assertEqual([p.name for p in root.iterdir()], [target.name])


if __name__ == "__main__":
    unittest.main()


class OutputLocationTests(unittest.TestCase):
    """默认输出位置：不可写时改用兜底目录，确认框预填路径。"""

    def setUp(self):
        base = Path(__file__).resolve().parents[1] / "build"
        base.mkdir(parents=True, exist_ok=True)
        self.root = base / f"test-output-{os.getpid()}-{time.time_ns()}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = self.root / "home"
        (self.home / "Documents").mkdir(parents=True)
        patcher = patch.dict(os.environ, {"USERPROFILE": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _context(self, **kwargs):
        return _snapshot().context(**kwargs)

    def test_papers_only_run_never_defaults_next_to_the_ledger_project(self):
        context = self._context()
        paper = SimpleNamespace(index_no="1-010", name="货币资金")
        path = agent_tools._default_workpaper_path(context, paper)
        self.assertEqual(path.parent, self.home / "Attesta输出" / "合成企业 - 2026-12")
        self.assertEqual(path.name, "1-010 货币资金-底稿.xlsx")

    def test_unwritable_output_dir_falls_back_to_documents(self):
        blocked = self.root / "blocked"
        context = self._context(output_dir=str(blocked))
        with patch.object(agent_tools, "_can_write", side_effect=lambda d: Path(d) != blocked):
            base = agent_tools._output_dir(context)
        self.assertEqual(base, agent_tools.fallback_output_dir(context))

    def test_writable_output_dir_is_kept(self):
        chosen = self.root / "out"
        context = self._context(output_dir=str(chosen))
        self.assertEqual(agent_tools._output_dir(context), chosen)

    def test_can_write_tries_once_and_never_loops_on_access_denied(self):
        calls = []

        def denied(*_args, **_kwargs):
            calls.append(1)
            raise PermissionError("受控文件夹访问")

        with patch.object(agent_tools.os, "open", denied):
            self.assertFalse(agent_tools._can_write(self.root))
        self.assertEqual(len(calls), 1)

    def test_can_write_leaves_no_files_behind(self):
        target = self.root / "probe" / "deeper"
        self.assertTrue(agent_tools._can_write(target))
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob("tmp*")), [])

    def test_export_relocates_when_chosen_folder_is_not_writable(self):
        blocked = self.root / "blocked"
        paper = SimpleNamespace(index_no="1-010", name="货币资金", label="1-010 货币资金")
        data = SimpleNamespace(source_path="", rows=(), journal=None, source_fingerprint=())
        context = self._context()
        context.prepared_workpapers["1-010"] = {"paper": paper, "data": data,
                                               "draft": {"kind": "local"}, "warnings": []}
        context.evidence_rows["1-010"] = ("证据",)
        runtime = SimpleNamespace(approved_path=str(blocked / "1-010 货币资金-底稿.xlsx"))
        with patch.object(agent_tools, "_can_write", side_effect=lambda d: Path(d) != blocked), \
                patch.object(agent_tools.dxn_paper, "build",
                             lambda path, **_kw: Path(path).write_bytes(b"x")):
            result = agent_tools.export_workpaper(context, {"index_no": "1-010"}, runtime)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["data"]["relocated"])
        self.assertTrue((agent_tools.fallback_output_dir(context) / "1-010 货币资金-底稿.xlsx").is_file())
        self.assertFalse(blocked.exists())

    def test_write_refused_at_save_time_retries_remaining_default_folders(self):
        local = self.root / "localappdata"
        paper = SimpleNamespace(index_no="1-010", name="货币资金", label="1-010 货币资金")
        data = SimpleNamespace(source_path="", rows=(), journal=None, source_fingerprint=())
        context = self._context()
        context.prepared_workpapers["1-010"] = {"paper": paper, "data": data,
                                               "draft": {"kind": "local"}, "warnings": []}
        context.evidence_rows["1-010"] = ("证据",)
        chosen = self.root / "chosen"

        def build(path, **_kw):
            if Path(path).parent in {chosen, agent_tools.fallback_output_dir(context)}:
                raise PermissionError(13, "denied", str(path))
            Path(path).write_bytes(b"x")

        with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}), \
                patch.object(agent_tools.dxn_paper, "build", build):
            result = agent_tools.export_workpaper(
                context, {"index_no": "1-010"},
                SimpleNamespace(approved_path=str(chosen / "1-010 货币资金-底稿.xlsx")))
            target = agent_tools.last_resort_dir(context) / "1-010 货币资金-底稿.xlsx"
        self.assertTrue(result["ok"], result)
        self.assertTrue(target.is_file())
        self.assertTrue(result["data"]["relocated"])
        self.assertTrue(any("已改存" in warning for warning in result["warnings"]))

    def test_write_approval_is_prefilled_with_the_default_path(self):
        seen = []
        paper = SimpleNamespace(index_no="1-010", name="货币资金", label="1-010 货币资金")
        context = self._context()
        context.prepared_workpapers["1-010"] = {"paper": paper, "data": None,
                                               "draft": {"kind": "local"}, "warnings": []}
        registry = agent_tools.build_registry()
        runtime = AgentRuntime(_Client([]), registry, context, audit=lambda *_: None,
                               approve=lambda request: seen.append(request) or
                               __import__("tb_autofill.agent", fromlist=["ApprovalDecision"]).ApprovalDecision(False))
        runtime._approve_write(registry.get("export_workpaper"), {"index_no": "1-010"})
        self.assertTrue(seen[0].default_path.endswith("1-010 货币资金-底稿.xlsx"), seen[0].default_path)
