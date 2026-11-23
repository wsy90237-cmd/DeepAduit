"""桌面 Agent 冒烟测试：真实 ``App`` + 脚本化假模型，不联网、不读真实账套。

覆盖两条路径：

* 只勾选 TB 副本：假模型依次调用 ``scan_template``、``export_tb_copy``、``finish(done)``，
  自动确认启动与写入后，真实 Agent 页应把 TB 副本与待处理清单登记为产物，原模板字节不变。
* 同时勾选整册底稿却不填报表项目索引：假模型只扫描模板后结束，本地核对应把结果降级为
  ``partial``，缺口包含“整册底稿未指定报表项目范围”。

写入 Excel 的步骤替换成本地合成的字节，避免依赖 Excel COM；临时目录放在工作区
``build/`` 下，Windows 沙箱下也可写。没有可用显示时整体跳过。
"""

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
import shutil
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openpyxl import Workbook

from tb_autofill import agent_tools, agent_ui, gui
from tb_autofill.agent import ApprovalDecision
from tb_autofill.models import LedgerRow, Subject

BUILD = Path(__file__).resolve().parents[1] / "build"
TIMEOUT_SECONDS = 60


def make_template(path):
    """最小合成模板：单个“期末余额”字段，供真实本地扫描/匹配使用。"""
    book = Workbook()
    sheet = book.active
    sheet.title = "期末"
    sheet.append(["科目名称", "期末余额"])
    sheet.append(["库存现金", None])
    book.save(path)
    book.close()


def ledger_rows():
    return [
        LedgerRow("1001", "库存现金", path="库存现金", opening=Decimal("50"),
                  debit=Decimal("60"), credit=Decimal("10"), ending=Decimal("100"),
                  subject_id="S1", year=2026, month=12, ledger_id="L1"),
        LedgerRow("1002", "银行存款", path="银行存款", opening=Decimal("200"),
                  debit=Decimal("30"), credit=Decimal("20"), ending=Decimal("210"),
                  subject_id="S1", year=2026, month=12, ledger_id="L1"),
    ]


def tool_call(name, arguments=None, call_id="call-1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments or {}, ensure_ascii=False)}}


def reply(calls=(), content=""):
    normalized = [{"id": raw["id"], "name": raw["function"]["name"],
                   "arguments": json.loads(raw["function"]["arguments"] or "{}"), "raw": raw}
                  for raw in calls]
    return {"content": content, "tool_calls": normalized,
            "finish_reason": "tool_calls" if calls else "stop"}


def _dispose(app):
    """先撤销残留的 after 任务再销毁，避免 Tk 在销毁后执行已删除的回调命令。"""
    try:
        for job in app.tk.call("after", "info"):
            try:
                app.after_cancel(job)
            except tk.TclError:
                pass
    except tk.TclError:
        pass
    try:
        app.destroy()
    except tk.TclError:
        pass


class ScriptedClient:
    """按脚本返回工具调用；接口与 DeepSeekClient.chat_tools 一致。"""

    model = "synthetic-tb-agent"
    base_url = "https://model.invalid"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat_tools(self, messages, tools):
        self.calls += 1
        if not self.replies:
            raise RuntimeError("脚本化模型回复已用尽")
        return self.replies.pop(0)


class AgentUiSmokeTests(unittest.TestCase):
    def setUp(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        self.root = BUILD / f"test-agent-ui-smoke-{os.getpid()}-{time.time_ns()}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.template = self.root / "template.xlsx"
        make_template(self.template)
        self.original_template = self.template.read_bytes()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

    def _fake_store(self):
        return SimpleNamespace(
            has_key=True,
            data={"ai_enabled": True, "model": ScriptedClient.model,
                  "base_url": ScriptedClient.base_url, "prepared_by": "",
                  "reviewed_by": "", "evidence_root": ""},
            get_key=lambda *_args, **_kwargs: "synthetic-key",
            save_signers=lambda *_args, **_kwargs: None,
        )

    def _run_agent(self, *, stages, report_indexes=(), replies, out_dir_name):
        """构造真实 App，跑一轮脚本化 Agent，返回运行结果与调用证据。"""
        fake_client = ScriptedClient(replies)
        out = self.root / out_dir_name
        tb_copy = out / "Agent-TB副本.xlsx"
        dialogs = {
            "askokcancel": MagicMock(return_value=True),
            "askyesno": MagicMock(return_value=False),
            "showinfo": MagicMock(),
            "showwarning": MagicMock(),
            "showerror": MagicMock(),
        }
        approvals = []

        def approve(_app, request):
            approvals.append(request)
            return ApprovalDecision(True, path=str(tb_copy))

        def fake_write_results(_scan, _results, path, **_kwargs):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic-tb-copy")
            return path

        def fake_export_report(report, _unresolved, **_kwargs):
            report = Path(report)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("synthetic-pending", encoding="utf-8")

        fake_store = self._fake_store()

        def isolated_settings(app):
            app.store = fake_store
            app._pending_error = ""

        self.stack.enter_context(patch.object(gui.App, "_load_settings", isolated_settings))
        # 真实 _autodetect 会扫描本机鼎信诺安装目录并改写 install_var；冒烟测试只验证 Agent 接入。
        self.stack.enter_context(patch.object(gui.App, "_autodetect"))
        self.stack.enter_context(patch.object(gui.auditlog, "AuditLog", return_value=MagicMock()))
        self.stack.enter_context(patch.object(gui.knowledge, "LocalLibrary", return_value=MagicMock()))
        self.stack.enter_context(patch.object(agent_ui, "_save_run_record", return_value=None))
        self.stack.enter_context(patch.object(agent_ui.ai.DeepSeekClient, "from_store",
                                              return_value=fake_client))
        self.stack.enter_context(patch.object(agent_ui, "_ask_approval", side_effect=approve))
        for name, mock in dialogs.items():
            self.stack.enter_context(patch.object(agent_ui.messagebox, name, mock))
        self.stack.enter_context(patch.object(agent_tools.workbook, "write_results",
                                              side_effect=fake_write_results))
        self.stack.enter_context(patch.object(agent_tools.workbook, "tb_balance_checks",
                                              return_value=[]))
        self.stack.enter_context(patch.object(agent_tools.workbook, "export_report",
                                              side_effect=fake_export_report))

        try:
            app = gui.App()
        except tk.TclError as exc:  # pragma: no cover - 取决于运行环境
            self.skipTest(f"无可用显示：{exc}")
        self.addCleanup(_dispose, app)
        app.withdraw()

        deadline = time.monotonic() + TIMEOUT_SECONDS
        failures = []

        def begin():
            try:
                # _autodetect 等在初始化时可能置 busy；快照与启动前显式复位。
                app.busy = False
                app.template_var.set(str(self.template))
                app.period_var.set("ending")
                # 账套事实在 App 初始化后再设，避免变量 trace 清空。
                app.rows = ledger_rows()
                app.loaded_subject = Subject("S1", "合成企业", 2026, 12, str(self.root),
                                             ledger_id="L1")
                app.scan = None
                app.results = []
                app.agent_stage_vars["tb"].set("tb" in stages)
                app.agent_stage_vars["papers"].set("papers" in stages)
                app.agent_reports_var.set(",".join(report_indexes))
                app.agent_output_var.set(str(out))
                agent_ui.start(app)
            except Exception as exc:  # 界面回调里的异常不会让 mainloop 退出，必须自己转达
                failures.append(exc)
                app.quit()

        def poll():
            if app.agent_result is not None or time.monotonic() >= deadline:
                app.quit()
                return
            app.after(50, poll)

        app.after(0, begin)
        app.after(50, poll)
        app.after(TIMEOUT_SECONDS * 1000, app.quit)
        app.mainloop()

        if failures:
            raise failures[0]
        result = app.agent_result
        self.assertIsNotNone(
            result,
            "Agent 未在时限内结束："
            f"{app.agent_state_var.get()}；最后事件：{[event.title for event in app.agent_events][-5:]}")
        return SimpleNamespace(
            result=result, out=out, tb_copy=tb_copy, client=fake_client,
            approvals=approvals, dialogs=dialogs,
            events=tuple(app.agent_events), state=app.agent_state_var.get())

    def test_tb_only_run_exports_copy_and_pending_list_in_real_app(self):
        run = self._run_agent(
            stages=("tb",),
            replies=[
                reply([tool_call("scan_template", call_id="call-scan")]),
                reply([tool_call("export_tb_copy", call_id="call-export")]),
                reply([tool_call("finish", {"status": "done", "summary": "已生成 TB 副本"},
                                 call_id="call-finish")]),
            ],
            out_dir_name="out-tb")

        self.assertEqual(run.result.status, "done",
                         f"{run.result.summary} / {run.result.gaps} / {run.result.error}")
        kinds = {item["kind"] for item in run.result.artifacts}
        self.assertTrue({"tb", "tb_pending"} <= kinds, kinds)
        for artifact in run.result.artifacts:
            if artifact["kind"] in {"tb", "tb_pending"}:
                self.assertTrue(Path(artifact["path"]).is_file(), artifact["path"])
        # 启动确认与写入确认都由脚本自动应答，仍走真实 Agent 页面流程。
        run.dialogs["askokcancel"].assert_called_once()
        self.assertEqual(len(run.approvals), 1)
        self.assertEqual(self.template.read_bytes(), self.original_template)
        self.assertEqual(run.result.error, "")
        self.assertNotEqual(run.result.status, "error")
        self.assertIn("已完成", run.state)

    def test_papers_with_scope_but_missing_workpaper_is_partial(self):
        run = self._run_agent(
            stages=("tb", "papers"),
            report_indexes=("1-010",),
            replies=[
                reply([tool_call("scan_template", call_id="call-scan")]),
                reply([tool_call("finish", {"status": "done", "summary": "已扫描模板"},
                                 call_id="call-finish")]),
            ],
            out_dir_name="out-partial")

        self.assertEqual(run.result.status, "partial",
                         f"{run.result.summary} / {run.result.gaps} / {run.result.error}")
        self.assertTrue(any("整册底稿未生成：1-010" in gap for gap in run.result.gaps),
                        run.result.gaps)
        self.assertTrue(any("未生成" in gap for gap in run.result.gaps), run.result.gaps)
        self.assertEqual(run.result.artifacts, ())
        self.assertEqual(self.template.read_bytes(), self.original_template)
        self.assertEqual(run.result.error, "")
        self.assertIn("部分完成", run.state)


if __name__ == "__main__":
    unittest.main()
