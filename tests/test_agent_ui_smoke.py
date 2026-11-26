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

from openpyxl import Workbook, load_workbook

from tb_autofill import agent_tools, agent_ui, gui, workspace
from tb_autofill.agent import ApprovalDecision
from tb_autofill.models import JournalRows, LedgerRow, MatchResult, PaperData, Subject, TargetField

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
        self.sent = []

    def chat_tools(self, messages, tools):
        self.calls += 1
        self.sent.append(json.dumps(messages, ensure_ascii=False))
        if not self.replies:
            raise RuntimeError("脚本化模型回复已用尽")
        return self.replies.pop(0)

    def paper_text(self, _request):
        return {"intent": "audit", "answer": "{}"}


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

    def _run_agent(self, *, replies, out_dir_name, template=True,
                   message="复核 TB 并生成所需交付物", block_workpaper_locations=False,
                   retry_workpaper=False, choose_template_in_chat=False, previous_scan=False):
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
        temp_output = self.root / "user-temp"
        temp_output.mkdir()
        paper_path = out / "货币资金底稿.xlsx"
        retry_path = self.root / "另选位置" / paper_path.name
        denied_paths = []
        before_retry = []

        if block_workpaper_locations:
            self.stack.enter_context(patch.dict(os.environ, {
                "USERPROFILE": str(self.root), "LOCALAPPDATA": str(self.root / "local")}))
            self.stack.enter_context(patch.object(agent_tools, "gettempdir", return_value=str(temp_output)))
            data = PaperData(rows=tuple(ledger_rows()), journal=JournalRows())
            self.stack.enter_context(patch.object(agent_tools.dxn, "read_paper_data", return_value=data))
            self.stack.enter_context(patch.object(agent_tools.papers, "pack_result", return_value={"kind": "local"}))
            check_write = agent_tools.dxn_paper.require_writable

            def require_temp_output(directory):
                allowed = directory == retry_path.parent if retry_workpaper else directory.is_relative_to(temp_output)
                if not allowed:
                    denied_paths.append(directory)
                    raise PermissionError(13, "synthetic permission failure", str(directory))
                return check_write(directory)

            self.stack.enter_context(patch.object(agent_tools.dxn_paper, "require_writable",
                                                  side_effect=require_temp_output))
            if retry_workpaper:
                self.stack.enter_context(patch.object(agent_ui, "prompt_approval",
                    side_effect=lambda _app, _request, done: done(ApprovalDecision(True, str(retry_path)))))

        def approve(_app, request):
            approvals.append(request)
            path = paper_path if "导出底稿" in request.summary else tb_copy
            return ApprovalDecision(True, path=str(path))

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
        if not retry_workpaper:
            self.stack.enter_context(patch.object(agent_ui, "_save_run_record", return_value=None))
        self.stack.enter_context(patch.object(agent_ui.ai.DeepSeekClient, "from_store",
                                              return_value=fake_client))
        self.stack.enter_context(patch.object(agent_ui, "_ask_approval", side_effect=approve))
        starts = []

        def confirm(_app, _title, lines, on_done):
            # 开始确认已改为对话页内的面板：测试里记录内容后直接点「开始」。
            starts.append(lines)
            on_done(True)

        self.stack.enter_context(patch.object(agent_ui, "confirm_start", side_effect=confirm))
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
        if not choose_template_in_chat:
            app.withdraw()

        deadline = time.monotonic() + TIMEOUT_SECONDS
        failures = []

        def begin():
            try:
                # _autodetect 等在初始化时可能置 busy；快照与启动前显式复位。
                app.busy = False
                app.template_var.set(str(self.template) if template else "")
                app.period_var.set("ending")
                # 账套事实在 App 初始化后再设，避免变量 trace 清空。
                app.rows = ledger_rows()
                app.loaded_subject = Subject("S1", "合成企业", 2026, 12, str(self.root),
                                             ledger_id="L1")
                app.scan = None
                app.results = []
                if previous_scan:
                    app.scan = agent_ui.fields.scan_template(self.template)
                    app.results = [MatchResult(TargetField("专属模板页", "B2", "专属待核字段",
                                                           "ending", value=98172365))]
                app.agent_input.insert("1.0", message)
                agent_ui._send(app)
                if choose_template_in_chat:
                    self.assertEqual(starts, [])
                    self.assertEqual(fake_client.calls, 0)
                    dialog = next(child for child in app.winfo_children()
                                  if isinstance(child, tk.Toplevel) and child.title() == "TB 模板")
                    children = list(dialog.winfo_children())
                    while children:
                        widget = children.pop()
                        children.extend(widget.winfo_children())
                        if hasattr(widget, "invoke") and widget.cget("text") == "浏览…":
                            with patch.object(agent_ui.filedialog, "askopenfilename",
                                              return_value=str(self.template)):
                                widget.invoke()
                        if hasattr(widget, "invoke") and widget.cget("text") == "继续任务":
                            continue_button = widget
                    continue_button.invoke()
            except Exception as exc:  # 界面回调里的异常不会让 mainloop 退出，必须自己转达
                failures.append(exc)
                app.quit()

        def poll():
            if time.monotonic() >= deadline:
                app.quit()
                return
            if app.agent_result is not None and not app.agent_running:
                if retry_workpaper and not before_retry:
                    before_retry.append((fake_client.calls, app.agent_artifacts_empty.cget("text"),
                                         app.agent_artifacts.get_children()))
                    app.agent_reexport_btn.invoke()
                else:
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
            approvals=approvals, dialogs=dialogs, starts=starts,
            denied_paths=denied_paths, temp_output=temp_output, before_retry=before_retry,
            retry_path=retry_path,
            record=app.agent_run_record,
            review_ready="disabled" not in app.agent_review_btn.state(),
            artifact_rows=tuple(app.agent_artifacts.item(item, "values")
                                for item in app.agent_artifacts.get_children()),
            events=tuple(app.agent_events), state=app.agent_state_var.get())

    def test_ledger_question_excludes_previous_tb_from_every_request(self):
        run = self._run_agent(previous_scan=True, message="这个账套有多少个科目？",
                              out_dir_name="ledger-only", replies=[
                                  reply([tool_call("get_project_context")]),
                                  reply([tool_call("finish", {"status": "done", "summary": "两个科目"})])])
        self.assertEqual(len(run.client.sent), 2)
        self.assertIn("合成企业", run.client.sent[-1])
        for payload in run.client.sent:
            for private in (self.template.name, "专属模板页", "专属待核字段", "98172365"):
                self.assertNotIn(private, payload)

    def test_local_template_question_survives_reopening_and_exports_once(self):
        template = self.root / "TB-另一个有限公司.xlsx"
        self.template.rename(template)
        out = self.root / "approved-output"
        out.mkdir()
        local = self.root / "local"
        client = ScriptedClient([
            reply([tool_call("ask_user", {"question": "是否保留待核项目？", "options": ["保留", "人工复核"]}, "ask"),
                   tool_call("get_project_context", call_id="after-ask")]),
            reply([tool_call("export_tb_copy", call_id="export")]),
            reply([tool_call("export_tb_copy", call_id="repeat-export")]),
            reply([tool_call("finish", {"status": "done", "summary": "已导出"}, "finish")]),
        ])
        fake_store = self._fake_store()
        starts, approvals = [], []

        def settings_for(app):
            app.store, app._pending_error = fake_store, ""

        def confirm(_app, title, lines, done):
            starts.append((title, dict(lines)))
            done(True)

        def approve(_app, request):
            approvals.append(request.default_path)
            return ApprovalDecision(True, request.default_path)

        def wait_for(app, condition):
            deadline = time.monotonic() + 10
            while not condition() and time.monotonic() < deadline:
                app.update()
                time.sleep(0.02)
            self.assertTrue(condition(), ascii((app.agent_state_var.get(), app.agent_result,
                                                app.agent_chat.get("1.0", "end"))))

        def open_app():
            try:
                app = gui.App()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            self.addCleanup(_dispose, app)
            app.busy = False
            app.rows = ledger_rows()
            app.loaded_subject = Subject("S1", "当前企业有限公司", 2026, 12, str(self.root), ledger_id="L1")
            workspace.navigate(app, "agent")
            return app

        self.stack.enter_context(patch.dict(os.environ, {"LOCALAPPDATA": str(local), "USERPROFILE": str(self.root)}))
        self.stack.enter_context(patch.object(gui.App, "_load_settings", settings_for))
        self.stack.enter_context(patch.object(gui.App, "_autodetect"))
        self.stack.enter_context(patch.object(gui.auditlog, "AuditLog"))
        self.stack.enter_context(patch.object(gui.knowledge, "LocalLibrary"))
        self.stack.enter_context(patch.object(agent_ui.ai.DeepSeekClient, "from_store", return_value=client))
        self.stack.enter_context(patch.object(agent_ui, "confirm_start", side_effect=confirm))
        self.stack.enter_context(patch.object(agent_ui, "_ask_approval", side_effect=approve))

        def write_synthetic(scan, results, output, *, rows, allow_empty, **_kwargs):
            writes = agent_tools.workbook.planned_writes(scan, results, rows=rows, allow_empty=allow_empty)
            book = load_workbook(scan.path)
            try:
                for field, amount in writes:
                    book[field.sheet][field.cell] = amount
                book.save(output)
            finally:
                book.close()
            return output

        self.stack.enter_context(patch.object(agent_tools.workbook, "write_results", side_effect=write_synthetic))

        first = open_app()
        first.agent_input.insert("1.0", f'做期末 TB，模板在 "{template}"，结果保存到："{out}"')
        agent_ui._send(first)
        wait_for(first, lambda: first.agent_result is not None and first.agent_question_submit is not None)
        self.assertEqual(first.agent_result.status, "needs_input")
        self.assertIn("另一个有限公司", first.agent_result.summary)
        self.assertEqual(client.calls, 0)
        self.assertIn("期末", starts[0][1]["TB 设置"])
        pending = local / "ZSZH-TB" / "agent" / "pending"
        first_checkpoint, = pending.glob("*.json")
        _dispose(first)

        reopened = open_app()
        wait_for(reopened, lambda: reopened.agent_has_pending)
        template.write_bytes(self.original_template + b"changed")
        reopened.agent_resume_btn.invoke()
        reopened.agent_input.insert("1.0", "借格式，按当前账套填期末")
        agent_ui._send(reopened)
        self.assertIn("模板内容已变化", reopened.agent_chat.get("1.0", "end"))
        self.assertEqual(client.calls, 0)
        template.write_bytes(self.original_template)

        reopened.agent_resume_btn.invoke()
        reopened.agent_input.insert("1.0", "借格式，按当前账套填期末")
        agent_ui._send(reopened)
        wait_for(reopened, lambda: reopened.agent_result is not None and reopened.agent_question_submit is not None)
        self.assertEqual(reopened.agent_result.summary, "是否保留待核项目？")
        self.assertEqual(client.calls, 1)
        next_checkpoint, = pending.glob("*.json")
        self.assertNotEqual(first_checkpoint, next_checkpoint)
        reopened.agent_input.insert("1.0", "保留，后续人工复核。")
        agent_ui._send(reopened)
        wait_for(reopened, lambda: reopened.agent_result is not None and not reopened.agent_running)
        self.assertEqual(reopened.agent_result.status, "done", reopened.agent_result.error)
        self.assertEqual(sum(item.kind == "question" for item in reopened.agent_result.events), 2)
        self.assertEqual(len(approvals), 1, ascii((approvals, reopened.agent_result.artifacts,
            [(item.kind, item.detail) for item in reopened.agent_events if item.kind == "tool_result"])))
        book_path, = out.glob("*.xlsx")
        self.assertTrue(book_path.with_name(f"{book_path.stem}-待处理.csv").is_file())
        book = load_workbook(book_path, read_only=True)
        try:
            self.assertEqual(book["期末"]["B2"].value, 100)
        finally:
            book.close()
        self.assertEqual(template.read_bytes(), self.original_template)
        self.assertFalse(list(pending.glob("*.json")))
        messages = json.loads(client.sent[1])
        self.assertTrue(any(item.get("tool_call_id") == "ask" and "人工复核" in item["content"] for item in messages))
        self.assertTrue(any(item.get("tool_call_id") == "after-ask" for item in messages))
        self.assertTrue(all(str(self.root).replace("\\", "\\\\") not in payload for payload in client.sent))

        reopened.geometry("1450x660")
        reopened.update()
        side = reopened.agent_side_area
        self.assertTrue(side.winfo_ismapped())
        self.assertLess(side.canvas.yview()[1], 1.0)
        side.canvas.yview_moveto(1.0)
        self.assertAlmostEqual(side.canvas.yview()[1], 1.0)

        reopened.geometry("1060x660")
        reopened.update()
        self.assertFalse(side.winfo_ismapped())
        self.assertTrue(reopened.agent_input.winfo_ismapped())
        reopened.agent_arrange(toggle=True)
        reopened.update()
        self.assertTrue(side.winfo_ismapped())

    def test_chat_keeps_user_messages_in_bubbles_and_drops_template_button(self):
        self.stack.enter_context(patch.dict(os.environ, {
            "LOCALAPPDATA": str(self.root / "local"), "USERPROFILE": str(self.root)}))

        def settings_for(app):
            app.store, app._pending_error = self._fake_store(), ""

        self.stack.enter_context(patch.object(gui.App, "_load_settings", settings_for))
        self.stack.enter_context(patch.object(gui.App, "_autodetect"))
        self.stack.enter_context(patch.object(gui.auditlog, "AuditLog"))
        self.stack.enter_context(patch.object(gui.knowledge, "LocalLibrary"))
        try:
            app = gui.App()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.addCleanup(_dispose, app)
        app.busy = False
        app.rows = ledger_rows()
        app.loaded_subject = Subject("S1", "合成企业", 2026, 12, str(self.root), ledger_id="L1")
        workspace.navigate(app, "agent")
        app.update()
        self.assertFalse(hasattr(app, "agent_template_btn"))
        agent_ui._append_message(app, "me", "做个实收资本的底稿")
        app.update()
        self.assertEqual(len(app.agent_bubbles), 1)
        spacer, holder = app.agent_bubbles[0]
        self.assertGreater(spacer.winfo_width(), 0)
        self.assertGreater(holder.winfo_rootx(),
                           app.agent_chat.winfo_rootx() + app.agent_chat.winfo_width() // 2)
        agent_ui._append_message(app, "me", "这是一段比较长的中文说明，" * 8 + "结束。")
        app.update()
        lines = app.agent_bubbles[-1][1].winfo_children()[0].winfo_children()[0].cget("text").split("\n")
        self.assertGreater(len(lines), 2)
        self.assertFalse([line for line in lines if line[:1] in "，。、；：？！"])

    def test_returning_home_shows_new_greeting_after_opening_selector(self):
        def isolated_settings(app):
            app.store = self._fake_store()
            app._pending_error = ""

        with patch.object(gui.App, "_autodetect"), \
                patch.object(gui.App, "_load_settings", isolated_settings), \
                patch.object(gui.auditlog, "AuditLog"), patch.object(gui.knowledge, "LocalLibrary"):
            try:
                app = gui.App()
            except tk.TclError as exc:
                self.skipTest(str(exc))
            self.addCleanup(_dispose, app)
            first = app.home_greeting.get()
            app.home_select.invoke()
            self.assertEqual(app.home_source.winfo_manager(), "grid")
            workspace.navigate(app, "agent")
            workspace.navigate(app, "tb")
            self.assertNotEqual(app.home_greeting.get(), first)
            self.assertFalse(app.home_source.winfo_manager())
            app.home_select.invoke()
            self.assertEqual(app.home_source.winfo_manager(), "grid")

    def test_tb_only_run_exports_copy_and_pending_list_in_real_app(self):
        run = self._run_agent(
            template=False, choose_template_in_chat=True, message="只填期末 TB，列出待处理项",
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
        self.assertEqual(len(run.starts), 1)
        self.assertIn("发送到", dict(run.starts[0]))
        self.assertIn("期末", dict(run.starts[0])["TB 设置"])
        self.assertNotIn("期初", dict(run.starts[0])["TB 设置"])
        self.assertTrue(run.review_ready)
        self.assertEqual(len(run.approvals), 1)
        self.assertEqual(self.template.read_bytes(), self.original_template)
        self.assertEqual(run.result.error, "")
        self.assertNotEqual(run.result.status, "error")
        self.assertIn("已完成", run.state)

    def test_workpaper_only_does_not_require_user_tb_template(self):
        run = self._run_agent(
            replies=[
                reply([tool_call("finish", {"status": "done", "summary": "完成"},
                                 call_id="call-finish")]),
            ],
            out_dir_name="out-papers-only",
            template=False,
            message="帮我做货币资金底稿")

        self.assertEqual(run.result.status, "partial",
                         f"{run.result.summary} / {run.result.gaps} / {run.result.error}")
        self.assertFalse(any("模板" in gap for gap in run.result.gaps), run.result.gaps)
        self.assertTrue(any("整册底稿未生成：1-010" in gap for gap in run.result.gaps),
                        run.result.gaps)

    def test_papers_with_scope_but_missing_workpaper_is_partial(self):
        run = self._run_agent(
            replies=[
                reply([tool_call("scan_template", call_id="call-scan")]),
                reply([tool_call("finish", {"status": "done", "summary": "已扫描模板"},
                                 call_id="call-finish")]),
            ],
            out_dir_name="out-partial",
            message="复核 TB 并制作货币资金底稿")

        self.assertEqual(run.result.status, "partial",
                         f"{run.result.summary} / {run.result.gaps} / {run.result.error}")
        self.assertTrue(any("整册底稿未生成：1-010" in gap for gap in run.result.gaps),
                        run.result.gaps)
        self.assertTrue(any("未生成" in gap for gap in run.result.gaps), run.result.gaps)
        self.assertEqual(run.result.artifacts, ())
        self.assertEqual(self.template.read_bytes(), self.original_template)
        self.assertEqual(run.result.error, "")
        self.assertIn("部分完成", run.state)

    def test_workpaper_uses_another_directory_and_appears_in_artifacts(self):
        run = self._run_agent(
            template=False, message="制作货币资金底稿", out_dir_name="out-paper",
            block_workpaper_locations=True,
            replies=[
                reply([tool_call("prepare_workpaper", {"index_no": "1-010"}, "prepare")]),
                reply([tool_call("export_workpaper", {"index_no": "1-010"}, "export")]),
                reply([tool_call("finish", {"status": "done", "summary": "已导出"}, "finish")]),
            ])
        self.assertEqual(run.result.status, "done", run.result.error)
        self.assertEqual(len(run.denied_paths), 3)
        self.assertEqual(len(set(run.denied_paths)), 3)
        artifact, = run.result.artifacts
        path = Path(artifact["path"])
        self.assertTrue(path.is_relative_to(run.temp_output))
        self.assertEqual(run.artifact_rows, (("整册底稿", path.name),))
        self.assertIn("1-010", dict(run.starts[0])["交付物"])
        book = load_workbook(path, read_only=True)
        try:
            self.assertGreater(len(book.sheetnames), 1)
        finally:
            book.close()

    def test_failed_export_can_be_saved_elsewhere_without_another_model_request(self):
        run = self._run_agent(
            template=False, message="制作货币资金底稿", out_dir_name="blocked-paper",
            block_workpaper_locations=True, retry_workpaper=True,
            replies=[
                reply([tool_call("prepare_workpaper", {"index_no": "1-010"}, "prepare")]),
                reply([tool_call("export_workpaper", {"index_no": "1-010"}, "export")]),
                reply([tool_call("finish", {"status": "blocked", "summary": "输出位置不可写"}, "finish")]),
            ])
        self.assertEqual(run.before_retry, [(run.client.calls, "1 册草稿待保存", ())])
        self.assertEqual(run.state, "重新导出完成 1/1")
        self.assertEqual(run.artifact_rows, (("整册底稿", run.retry_path.name),))
        self.assertTrue(run.retry_path.is_file())
        self.assertNotIn("整册底稿未生成：1-010", run.result.gaps)
        history = json.loads(run.record.read_text(encoding="utf-8"))
        self.assertEqual(history["original_result"]["status"], "blocked")
        self.assertEqual(history["result"]["summary"], "重新导出完成 1/1")
        self.assertEqual(history["result"]["artifacts"][0]["path"], str(run.retry_path))
        self.assertEqual(history["result"]["artifacts"][0]["sha256"], run.result.artifacts[0]["sha256"])

    def test_requested_period_recognizes_both_after_only(self):
        self.assertEqual(agent_ui._requested_period("只填期初和期末"), "both")
        self.assertEqual(agent_ui._requested_period("不要填期初，只填期末"), "ending")


if __name__ == "__main__":
    unittest.main()
