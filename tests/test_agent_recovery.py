"""更智能的 Agent：本地序时账线索、失败后的下一步、缺证据的替代程序、只重新导出。"""

from __future__ import annotations

import os
import hashlib
import shutil
import time
import unittest
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tb_autofill import agent_checkpoints, agent_tools, analysis, papers
from tb_autofill.agent import AgentError, AgentRuntime, ProjectSnapshot, StaleContextError, ToolRegistry, ToolSpec, recovery_hint
from tb_autofill.models import JournalRow, JournalRows, Subject

try:
    from tb_autofill import agent_ui
except ImportError:  # 没有 tkinter 的环境只跳过界面相关用例
    agent_ui = None

NAMES = {"1002": "银行存款", "1002.01": "合成银行经营户", "1002.04": "合成银行监管户"}


def journal():
    return JournalRows([
        JournalRow(3, "2025-03-02", "记-1", "1", "收房款", "1002.01", debit=D("254000000"), contra="预收账款"),
        JournalRow(5, "2025-05-02", "记-2", "1", "转监管户", "1002.01", credit=D("105000000"), contra="银行存款"),
        JournalRow(5, "2025-05-02", "记-2", "2", "转监管户", "1002.04", debit=D("105000000"), contra="银行存款"),
        JournalRow(12, "2025-12-30", "记-9", "1", "付工程款", "1002.04", credit=D("2500000.55"),
                   contra="应付账款"),
    ])


def unknown(text, facts):
    return analysis.suspicious_numbers(
        text, analysis.fact_amounts(facts, include_counts=False), analysis.fact_codes(facts),
        tolerance=D("0.005"), extra=analysis.fact_tokens(facts), strict=True, rates=set(),
        counts=analysis.fact_counts(facts), code_names=analysis.fact_code_names(facts))


class JournalInsightTests(unittest.TestCase):
    def setUp(self):
        self.facts = {"journal": papers._journal_facts(journal(), None, names=NAMES)}
        self.insights = self.facts["journal"]["insights"]

    def test_per_account_totals_are_computed_locally(self):
        by_code = {item["code"]: item for item in self.insights["by_account"]}
        self.assertEqual(D(by_code["1002.04"]["debit"].replace(",", "")), D("105000000"))
        self.assertEqual(by_code["1002.04"]["name"], "合成银行监管户")

    def test_transfers_between_own_accounts_are_separated(self):
        self.assertEqual(self.insights["internal_transfers"]["count"], 2)

    def test_round_large_and_year_end_entries(self):
        self.assertEqual(self.insights["round_large"]["count"], 3)
        self.assertEqual(self.insights["year_end"]["count"], 1)

    def test_figures_from_insights_pass_the_number_check(self):
        text = "监管账户 1002.04 全年借方 105,000,000.00 元，来自本项目内互转；最大一笔收款 254,000,000.00 元。"
        self.assertEqual(unknown(text, self.facts), [])

    def test_invented_sum_is_still_rejected(self):
        self.assertTrue(unknown("两户净流入 149,000,000.00 元。", self.facts))


class RecoveryHintTests(unittest.TestCase):
    def test_permission_error_points_to_output_folder(self):
        self.assertIn("重新导出", recovery_hint("export_workpaper", "没有写入权限"))

    def test_repeated_failure_asks_user_or_blocks(self):
        hint = recovery_hint("export_workpaper", "任何错误", repeated=True)
        self.assertIn("ask_user", hint)
        self.assertIn("blocked", hint)

    def test_failed_tool_result_carries_next_step_to_the_model(self):
        def failing(_context, _arguments, _runtime):
            return {"ok": False, "error": "未生成底稿：没有写入权限（D:/x）。"}

        spec = ToolSpec("export_workpaper", "导出", {"type": "object", "properties": {"q": {"type": "string"}},
                                                   "additionalProperties": False}, "read", failing)
        subject = Subject("S1", "合成企业", 2026, 12, ".", ledger_id="L1")
        context = ProjectSnapshot(subject=subject, rows=(), subject_label="合成企业 / 2026-12",
                                  template_path="", unit="元", periods=(), scan=None, results=(),
                                  paper_library=()).context()
        runtime = AgentRuntime(SimpleNamespace(model="m", base_url="u"), ToolRegistry([spec]), context,
                               audit=lambda *_: None)
        first = runtime._execute({"name": "export_workpaper", "arguments": {"q": "1"}})
        second = runtime._execute({"name": "export_workpaper", "arguments": {"q": "2"}})
        self.assertIn("重新导出", first["next"])
        self.assertIn("两次", second["next"])


class AlternativeProcedureTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / "build"
        base.mkdir(parents=True, exist_ok=True)
        self.root = base / f"test-recovery-{os.getpid()}-{time.time_ns()}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _context(self, index_no, name):
        subject = Subject("S1", "合成企业", 2026, 12, ".", ledger_id="L1")
        context = ProjectSnapshot(subject=subject, rows=(), subject_label="合成企业 / 2026-12",
                                  template_path="", unit="元", periods=(), scan=None, results=(),
                                  paper_library=()).context(output_dir=str(self.root))
        paper = SimpleNamespace(index_no=index_no, name=name, label=f"{index_no} {name}")
        data = SimpleNamespace(source_path="", rows=(), journal=None, source_fingerprint=())
        context.prepared_workpapers[index_no] = {"paper": paper, "data": data,
                                                 "draft": {"kind": "local"}, "warnings": []}
        return context

    def _export(self, context, index_no):
        runtime = SimpleNamespace(approved_path="")
        with patch.object(agent_tools.dxn_paper, "build", lambda path, **_kw: Path(path).write_bytes(b"x")):
            return agent_tools.export_workpaper(context, {"index_no": index_no}, runtime)

    def test_missing_confirmations_come_with_alternative_procedures(self):
        context = self._context("1-070", "应收账款")
        result = self._export(context, "1-070")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["data"]["next_steps"])
        self.assertIn("期后收款", context.gaps[0])

    def test_every_item_without_evidence_is_flagged_not_only_cash(self):
        context = self._context("1-280", "固定资产")
        self._export(context, "1-280")
        self.assertIn("未关联已确认的外部证据", context.gaps[0])


@unittest.skipIf(agent_ui is None, "需要 tkinter")
class ReexportTests(unittest.TestCase):
    def test_only_prepared_items_without_a_valid_file_are_offered(self):
        root = Path(__file__).resolve().parents[1] / "build" / f"test-reexport-{os.getpid()}-{time.time_ns()}"
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, True)
        done = root / "1-010.xlsx"
        done.write_bytes(b"ok")
        context = SimpleNamespace(
            prepared_workpapers={"1-010": {}, "1-070": {}},
            artifacts=[{"kind": "workpaper", "target": "1-010", "path": str(done),
                        "sha256": agent_ui.agent_module._sha256(done)}])
        self.assertEqual(agent_ui._missing_workpapers(context), ["1-070"])
        done.write_bytes(b"changed")
        self.assertEqual(agent_ui._missing_workpapers(context), ["1-010", "1-070"])


class ThinkingStreamTests(unittest.TestCase):
    def test_reasoning_written_with_a_tool_call_is_published(self):
        def noop(_context, _arguments, _runtime):
            return {"ok": True, "summary": "已读取"}

        look = ToolSpec("look", "读取", {"type": "object", "properties": {},
                                         "additionalProperties": False}, "read", noop)
        finish = ToolSpec("finish", "结束", {"type": "object", "properties": {
            "status": {"type": "string"}, "summary": {"type": "string"}},
            "additionalProperties": False}, "user", noop)
        replies = [
            {"content": "先看项目状态，确认账套已读取。", "tool_calls": [
                {"id": "a", "name": "look", "arguments": {}, "raw": {"id": "a"}}]},
            {"content": "", "tool_calls": [
                {"id": "b", "name": "finish", "arguments": {"status": "done", "summary": "完成"},
                 "raw": {"id": "b"}}]},
        ]
        client = SimpleNamespace(model="m", base_url="u", chat_tools=lambda _m, _t: replies.pop(0))
        subject = Subject("S1", "合成企业", 2026, 12, ".", ledger_id="L1")
        context = ProjectSnapshot(subject=subject, rows=(), subject_label="合成企业 / 2026-12",
                                  template_path="", unit="元", periods=(), scan=None, results=(),
                                  paper_library=()).context()
        events = []
        AgentRuntime(client, ToolRegistry([look, finish]), context, emit=events.append,
                     audit=lambda *_: None).run()
        kinds = [event.kind for event in events]
        self.assertIn("thinking", kinds)
        thought = [event for event in events if event.kind == "thought"]
        self.assertEqual(thought[0].detail, "先看项目状态，确认账套已读取。")
        self.assertLess(kinds.index("thought"), kinds.index("tool"))


class LargeSourceFingerprintTests(unittest.TestCase):
    def test_large_table_detects_changed_content_with_unchanged_size_and_timestamp(self):
        from tb_autofill import dxn
        root = Path(__file__).resolve().parents[1] / "build" / f"test-source-{os.getpid()}-{time.time_ns()}"
        (root / "powersj").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, True)
        table = root / "powersj" / "c_kmyek.MYD"
        with table.open("wb") as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
        stat = table.stat()
        with table.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        data = SimpleNamespace(source_path=str(root), source_tables=("c_kmyek",), source_missing_tables=(),
                               source_fingerprint=((str(table.resolve()), stat.st_size, stat.st_mtime_ns, digest),))
        self.assertTrue(dxn.validate_paper_data_source(data))
        with table.open("r+b") as stream:
            stream.seek(-1, os.SEEK_END)
            stream.write(b"x")
        os.utime(table, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(table.stat().st_mtime_ns, stat.st_mtime_ns)
        self.assertFalse(dxn.validate_paper_data_source(data))


@unittest.skipUnless(os.name == "nt", "待办进程锁使用 Windows 文件锁")
class PendingResumeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / "build" / f"test-pending-{os.getpid()}-{time.time_ns()}"
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.enterContext(patch.object(agent_checkpoints, "data_dir", return_value=self.root))
        self.snapshot = ProjectSnapshot(subject=Subject("S1", "合成企业", 2026, 12, "."),
                                        rows=(), subject_label="合成企业", template_path="", unit="元",
                                        periods=(), scan=None, results=(), paper_library=())
        self.context = self.snapshot.context()
        self.state = {"sequence": 1, "question": "继续？", "kind": "budget", "calls": [], "messages": [],
                      "blocked_tools": [], "call_history": [], "failures": [], "started_at": ""}
        self.path = agent_checkpoints.save("synthetic", {}, self.context, self.state)
        self.record = agent_checkpoints.load(self.path)

    def test_failed_model_call_keeps_record_and_concurrent_resume_is_rejected(self):
        def offline(*_args):
            raise AgentError("合成网络错误")

        runtime = AgentRuntime(SimpleNamespace(chat_tools=offline), ToolRegistry([]), self.context)
        with agent_checkpoints.claim(self.path, self.record):
            agent_checkpoints.restore(self.record, self.context)
            with self.assertRaises(StaleContextError):
                with agent_checkpoints.claim(self.path, self.record):
                    self.fail("同一待办不应同时继续")
            self.assertEqual(runtime.run(self.record["state"], "继续").status, "error")
        self.assertEqual(agent_checkpoints.load(self.path), self.record)
        with agent_checkpoints.claim(self.path, self.record):
            agent_checkpoints.save("synthetic", {}, self.context, {**self.state, "sequence": 2}, target=self.path)
        with self.assertRaises(StaleContextError):
            with agent_checkpoints.claim(self.path, self.record):
                self.fail("旧问题界面不应领取新进度")
        self.assertEqual(list(agent_checkpoints.pending_for(self.snapshot.subject))[0][1]["state"]["sequence"], 2)

    def test_uncertain_export_is_guarded_and_registered_export_survives_retry(self):
        output = self.root / "synthetic.xlsx"
        exports = {}
        agent_checkpoints.track_export(self.path, self.record, self.context, exports, "tb", "before", output)
        agent_checkpoints.track_export(self.path, self.record, self.context, exports, "tb", "guard", None)
        output.write_bytes(b"synthetic")
        interrupted = agent_checkpoints.load(self.path)
        restored_exports = interrupted["pending_exports"].copy()
        with self.assertRaisesRegex(AgentError, "synthetic.xlsx"):
            agent_checkpoints.track_export(self.path, interrupted, self.context, restored_exports, "tb", "guard", None)
        self.context.record_artifact("tb", output, target="tb")
        self.context.tb_exported = True
        agent_checkpoints.track_export(self.path, self.record, self.context, exports, "tb", "after", None)
        restored = self.snapshot.context()
        saved = agent_checkpoints.load(self.path)
        agent_checkpoints.restore(saved, restored)
        self.assertTrue(restored.tb_exported)
        self.assertEqual(restored.artifacts, self.context.artifacts)
        self.assertEqual(saved["pending_exports"], {})

    def test_durable_done_record_hides_a_checkpoint_if_cleanup_was_interrupted(self):
        (self.root / "runs").write_bytes(b"synthetic path conflict")
        self.assertEqual(list(agent_checkpoints.pending_for(self.snapshot.subject)), [(self.path, self.record)])
        with agent_checkpoints.claim(self.path, self.record):
            agent_checkpoints.restore(self.record, self.context)
        (self.root / "runs").unlink()
        agent_checkpoints.write_json(self.root / "runs" / "synthetic.json", {"result": {"status": "done"}})
        self.assertEqual(list(agent_checkpoints.pending_for(self.snapshot.subject)), [])
        with self.assertRaises(StaleContextError):
            with agent_checkpoints.claim(self.path, self.record):
                self.fail("已完成任务不应重新运行")


@unittest.skipIf(agent_ui is None, "需要 tkinter")
class StuckApprovalTests(unittest.TestCase):
    def test_late_completion_preserves_new_active_task(self):
        new_runtime = object()
        new_context = object()
        app = SimpleNamespace(_context_revision=2, agent_running=True, agent_runtime=new_runtime,
                              agent_context=new_context, agent_result=None)
        old_context = SimpleNamespace(snapshot=SimpleNamespace(app_revision=1))
        result = object()
        with patch.object(agent_ui, "_save_run_record") as saved, \
                patch.object(agent_ui, "_stop_hang_trace") as stop_trace:
            agent_ui._finish(app, object(), result, old_context, {}, "previous-run")
        saved.assert_called_once_with(app, result, {}, "previous-run")
        stop_trace.assert_not_called()
        self.assertIs(app.agent_runtime, new_runtime)
        self.assertIs(app.agent_context, new_context)
        self.assertIsNone(app.agent_result)
        self.assertTrue(app.agent_running)

    def test_stop_releases_a_confirmation_that_never_appeared(self):
        import threading
        from tb_autofill.agent import ApprovalRequest
        state = SimpleNamespace(set=lambda _value: None)
        app = SimpleNamespace(_post=lambda _action: None, agent_runtime=None, agent_state_var=state)
        outcome = {}
        worker = threading.Thread(target=lambda: outcome.setdefault(
            "decision", agent_ui._ask_approval(app, ApprovalRequest("write", "确认", "导出底稿"))))
        worker.start()
        for _ in range(100):
            if getattr(app, "agent_pending", None):
                break
            time.sleep(0.01)
        agent_ui.stop(app)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(outcome["decision"].approved)
        self.assertEqual(app.agent_pending, [])



@unittest.skipIf(agent_ui is None, "需要 tkinter")
class InlinePromptTests(unittest.TestCase):
    """确认面板画在主窗口里：排队显示、按钮回传决定、处理完收起。"""

    def setUp(self):
        import tkinter as tk
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except Exception as exc:  # pragma: no cover - 无显示或非真实 Tk
            self.skipTest(f"无可用显示：{exc}")
        self.addCleanup(self.root.destroy)
        self.root.agent_prompt = tk.Frame(self.root)
        self.root.agent_prompt.grid(row=0, column=0)
        self.root.agent_prompt_queue = []
        self.root.agent_prompt_active = None
        self.root.active_page = "agent"
        self.root.bell = lambda: None

    def _buttons(self):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() in {"TButton", "Button"}:
                    found.append(child)
                walk(child)

        walk(self.root.agent_prompt)
        return {button.cget("text"): button for button in found}

    def test_two_confirmations_are_shown_one_after_another(self):
        from tb_autofill.agent import ApprovalRequest
        decisions = []
        for index in ("1-010", "1-070"):
            agent_ui.prompt_approval(self.root, ApprovalRequest(
                "write", "确认写入文件", f"导出底稿：{index}", default_path=f"C:/out/{index}.xlsx",
                allow_auto=True), decisions.append)
        self.assertEqual(len(self.root.agent_prompt_queue), 1)
        self._buttons()["允许并继续"].invoke()
        self.assertTrue(decisions[0].approved)
        self.assertEqual(decisions[0].path, "C:/out/1-010.xlsx")
        self._buttons()["拒绝"].invoke()
        self.assertFalse(decisions[1].approved)
        self.assertIsNone(self.root.agent_prompt_active)
        self.assertFalse(self.root.agent_prompt.winfo_children())

    def test_question_returns_the_chosen_option(self):
        answers = []
        agent_ui.prompt_question(self.root, "1002.04 是否为受限账户？", ("是", "否"), answers.append)
        self._buttons()["是"].invoke()
        self.assertEqual(answers, ["是"])


if __name__ == "__main__":
    unittest.main()
