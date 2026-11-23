"""审计 Agent 页面：任务配置、时间线、确认弹窗与产物列表。

界面只负责收集范围、启动工作线程、把运行时的确认请求转成 Tk 弹窗；金额、匹配和
文件写入仍由 ``agent_tools`` 调用现有本地模块完成。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from . import agent as agent_module
from . import agent_tools, ai, fields, settings, theme, workspace
from . import dialogs as messagebox
from .models import ledger_digest

BG, CARD = theme.PAPER, theme.SURFACE
BORDER, TX, SEC = theme.HAIRLINE, theme.INK, theme.MUTED
ACC = theme.ACCENT


def _label(parent, text, *, fg=SEC, size=9, weight="normal", bg=CARD, wrap=0, width=0):
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=theme.font(size, weight=weight),
                    anchor="w", justify="left", wraplength=wrap or 0,
                    width=width or 0)


def _card(parent, title):
    outer = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1, bd=0)
    outer.pack(fill="x", padx=30, pady=(14, 0))
    _label(outer, title, fg=TX, size=10, weight="bold").pack(anchor="w", padx=14, pady=(10, 4))
    body = tk.Frame(outer, bg=CARD)
    body.pack(fill="x", padx=14, pady=(0, 12))
    return outer, body


def build(app):
    """由 workspace.build_pages 调用；所有控件挂在 app.pages['agent']。"""
    page = workspace.scroll_page(app, "agent")
    app.agent_events = []
    app.agent_result = None
    app.agent_running = False
    app.agent_runtime = None
    app.agent_context = None
    app.agent_audit_error = ""
    app.agent_context_var = tk.StringVar(value="尚未读取账套")
    app.agent_state_var = tk.StringVar(value="配置模型、读取账套并选择模板后开始。")
    app.agent_stage_vars = {
        "tb": tk.BooleanVar(value=True),
        "papers": tk.BooleanVar(value=True),
    }
    app.agent_reports_var = tk.StringVar(value="")
    app.agent_focus_var = tk.StringVar(value="")
    app.agent_materiality_var = tk.StringVar(value="")
    app.agent_output_var = tk.StringVar(value="")

    top = tk.Frame(page, bg=BG)
    top.pack(fill="x", padx=30, pady=(24, 0))
    _label(top, "审计 Agent", fg=TX, size=20, weight="bold", bg=BG).pack(anchor="w")
    _label(top, "从账套到 TB 副本与整册底稿；金额本地计算，写文件前确认。附注/报告草稿下一阶段。",
           bg=BG).pack(anchor="w", pady=(4, 0))
    context = tk.Frame(top, bg=BG)
    context.pack(fill="x", pady=(10, 0))
    tk.Label(context, textvariable=app.agent_context_var, bg=BG, fg=SEC, font=theme.font(9),
             anchor="w").pack(side="left", fill="x", expand=True)
    ttk.Button(context, text="模型设置", command=app._open_ai_settings).pack(side="right")
    ttk.Button(context, text="账套与 TB", command=lambda: workspace.navigate(app, "tb")).pack(side="right", padx=6)

    _, form = _card(page, "任务")
    _label(form, "任务目标（可留空）").pack(anchor="w")
    app.agent_objective = tk.Text(form, height=3, wrap="word", font=theme.font(9), relief="flat",
                                  highlightthickness=1, highlightbackground=BORDER,
                                  bg=theme.SURFACE_ALT, fg=TX, padx=8, pady=6)
    app.agent_objective.pack(fill="x", pady=(4, 8))
    stages = tk.Frame(form, bg=CARD)
    stages.pack(fill="x", pady=(0, 6))
    _label(stages, "交付物").pack(side="left")
    for key, text in (("tb", "TB 副本"), ("papers", "整册底稿")):
        ttk.Checkbutton(stages, text=text, variable=app.agent_stage_vars[key]).pack(side="left", padx=(10, 0))
    _label(stages, "附注/报告草稿下一阶段开放", fg=SEC).pack(side="left", padx=(16, 0))
    row = tk.Frame(form, bg=CARD)
    row.pack(fill="x", pady=4)
    _label(row, "报表项目索引", width=10).pack(side="left")
    ttk.Entry(row, textvariable=app.agent_reports_var, width=46).pack(side="left", padx=(6, 12))
    _label(row, "整册底稿必填（如 1-010）；留空将无法开始", fg=SEC).pack(side="left")
    row = tk.Frame(form, bg=CARD)
    row.pack(fill="x", pady=4)
    _label(row, "关注点", width=10).pack(side="left")
    ttk.Entry(row, textvariable=app.agent_focus_var, width=46).pack(side="left", padx=(6, 12))
    _label(row, "例如期末跨期、大额往来", fg=SEC).pack(side="left")
    row = tk.Frame(form, bg=CARD)
    row.pack(fill="x", pady=4)
    _label(row, "重要性（元）", width=10).pack(side="left")
    ttk.Entry(row, textvariable=app.agent_materiality_var, width=20).pack(side="left", padx=(6, 12))
    _label(row, "可选；不填写时报告标注待项目组确定", fg=SEC).pack(side="left")
    row = tk.Frame(form, bg=CARD)
    row.pack(fill="x", pady=4)
    _label(row, "输出目录", width=10).pack(side="left")
    ttk.Entry(row, textvariable=app.agent_output_var, width=50).pack(side="left", padx=(6, 6))
    ttk.Button(row, text="选择…", command=lambda: _choose_output(app)).pack(side="left")
    actions = tk.Frame(form, bg=CARD)
    actions.pack(fill="x", pady=(10, 0))
    app.agent_start_btn = ttk.Button(actions, text="开始 Agent", style="Accent.TButton",
                                     command=lambda: start(app))
    app.agent_start_btn.pack(side="left")
    app.agent_stop_btn = ttk.Button(actions, text="停止", command=lambda: stop(app), state="disabled")
    app.agent_stop_btn.pack(side="left", padx=8)
    ttk.Button(actions, text="清空记录", command=lambda: _clear(app)).pack(side="left")

    _, timeline = _card(page, "运行时间线")
    app.agent_tree = ttk.Treeview(timeline, columns=("time", "type", "summary"),
                                  show="headings", height=10)
    for key, title, width in (("time", "时间", 90), ("type", "类型", 110), ("summary", "摘要", 760)):
        app.agent_tree.heading(key, text=title)
        app.agent_tree.column(key, width=width, anchor="w", stretch=key == "summary")
    app.agent_tree.pack(fill="x")
    app.agent_tree.tag_configure("tool", foreground=SEC)
    app.agent_tree.tag_configure("approval", foreground=ACC)
    app.agent_tree.tag_configure("answer", foreground=TX)
    app.agent_tree.tag_configure("warning", foreground=theme.BAD)

    _, artifacts = _card(page, "本次产物")
    app.agent_artifacts = ttk.Treeview(artifacts, columns=("kind", "name", "sha"),
                                       show="headings", height=5)
    for key, title, width in (("kind", "类型", 100), ("name", "文件", 420), ("sha", "SHA-256", 300)):
        app.agent_artifacts.heading(key, text=title)
        app.agent_artifacts.column(key, width=width, anchor="w", stretch=key == "name")
    app.agent_artifacts.pack(fill="x")
    app.agent_artifacts.bind("<Double-1>", lambda _event: _open_artifact(app))
    bar = tk.Frame(artifacts, bg=CARD)
    bar.pack(fill="x", pady=(6, 0))
    ttk.Button(bar, text="打开文件", command=lambda: _open_artifact(app)).pack(side="left")
    ttk.Button(bar, text="打开所在文件夹", command=lambda: _open_folder(app)).pack(side="left", padx=6)
    _label(page, "", fg=SEC, bg=BG).pack(anchor="w", padx=30, pady=(8, 0))
    tk.Label(page, textvariable=app.agent_state_var, bg=BG, fg=SEC, font=theme.font(9),
             anchor="w", justify="left", wraplength=900).pack(fill="x", padx=30, pady=(0, 24))
    refresh(app)


def _choose_output(app):
    path = filedialog.askdirectory(title="选择 Agent 输出目录", parent=app)
    if path:
        app.agent_output_var.set(path)


def _clear(app):
    if app.agent_running:
        return
    app.agent_events = []
    for item in app.agent_tree.get_children():
        app.agent_tree.delete(item)
    for item in app.agent_artifacts.get_children():
        app.agent_artifacts.delete(item)
    app.agent_state_var.set("已清空记录。")


def refresh(app):
    if not hasattr(app, "agent_context_var"):
        return
    subject = getattr(app, "loaded_subject", None)
    if subject is None:
        app.agent_context_var.set("尚未读取账套；请先在「账套与 TB」读取科目余额并选择模板。")
        return
    template = Path(app.template_var.get().strip()).name if app.template_var.get().strip() else "未选模板"
    state = "已配置" if getattr(app, "store", None) and app.store.has_key and app.store.data.get("ai_enabled") else "未启用模型"
    app.agent_context_var.set(f"{getattr(subject, 'name', '')} · {subject.year}-{subject.month:02d} · "
                              f"{template} · {state}")


def _stage_scope(app):
    stages = tuple(key for key, variable in app.agent_stage_vars.items() if variable.get())
    scope = {"ledger", "tb"}
    if "papers" in stages:
        scope.update({"journal", "workpaper", "knowledge"})
    return stages, scope


def _results_digest(results):
    """TB 结果摘要；运行线程读普通属性，不触碰 Tk 变量。"""
    try:
        from hashlib import sha256
        parts = [(item.target.id, item.status, bool(item.selected), str(item.amount),
                  tuple(item.source_ids)) for item in (results or [])]
        return sha256(repr(parts).encode("utf-8")).hexdigest()[:16]
    except (AttributeError, TypeError, ValueError):
        return ""


def _snapshot(app):
    subject = app.loaded_subject
    template = Path(app.template_var.get().strip())
    if not template.is_file():
        raise ValueError("请先选择存在的模板文件。")
    digest = fields.file_digest(template)
    return agent_module.ProjectSnapshot(
        subject=subject, rows=tuple(app.rows), subject_label=app._subject_label(),
        template_path=str(template), unit=app.unit_var.get(), periods=app._periods(),
        scan=app.scan, results=tuple(app.results), paper_library=tuple(app.paper_library),
        install_path=app.install_var.get().strip(), evidence_root=getattr(app, "evidence_root", ""),
        prepared_by=getattr(app, "prepared_by", ""), reviewed_by=getattr(app, "reviewed_by", ""),
        app_revision=app._context_revision, ledger_digest=ledger_digest(app.rows or []),
        template_digest=digest, results_digest=_results_digest(app.results),
    )


def _current_fingerprint(app):
    template = app.template_var.get().strip()
    try:
        digest = fields.file_digest(template) if template and Path(template).is_file() else ""
    except OSError:
        digest = ""
    return (app._context_revision, ledger_digest(app.rows or []), digest, template,
            _results_digest(app.results))


def _manifest_text(app, snapshot, stages, scope, client, config):
    counts = {
        "ledger": f"科目余额 {len(snapshot.rows)} 条（编码、名称、路径、余额）",
        "tb": f"模板字段与匹配结果 {len(snapshot.results)} 个",
        "journal": "序时账摘要与抽凭样本（制作底稿时）",
        "workpaper": "报表项目科目、客商及月度发生额摘要",
        "knowledge": "本地内置知识命中片段",
        "report": "TB 与底稿汇总事实",
    }
    selected = "；".join(counts[key] for key in sorted(scope) if key in counts)
    return (
        f"目的服务：{client.base_url}\n模型：{client.model}\n\n"
        f"发送范围：{selected}。\n"
        f"不发送：完整工作簿、原始数据库、API Key、未选中的客户资料。\n\n"
        f"交付物：{'、'.join({'tb': 'TB 副本', 'papers': '整册底稿'}.get(stage, stage) for stage in stages) or '未选择'}；"
        f"输出目录：{config['output_dir'] or '默认项目目录'}。\n"
        f"上限：{config['max_steps']} 步、{config['max_tool_calls']} 次工具、{config['max_model_calls']} 次模型请求。\n\n"
        "写入边界：不修改原模板/原数据库；只生成新文件；报告只生成草稿，不自动签发。\n\n"
        "点击“确定”后才会开始发送请求。"
    )


def start(app):
    if app.busy or app.agent_running:
        messagebox.showinfo("请稍候", "当前任务尚未完成。", parent=app)
        return
    if getattr(app, "store", None) is None or not app.store.has_key or not app.store.data.get("ai_enabled"):
        messagebox.showinfo("需要模型服务", "请先在「模型设置」输入 DeepSeek API Key 并启用。", parent=app)
        return
    if not app.rows or app.loaded_subject is None:
        messagebox.showinfo("缺少账套", "请先在「账套与 TB」读取科目余额。", parent=app)
        workspace.navigate(app, "tb")
        return
    try:
        snapshot = _snapshot(app)
        client = ai.DeepSeekClient.from_store(app.store)
    except (ValueError, ai.AIError, settings.SettingsError, OSError) as exc:
        messagebox.showwarning("无法开始", str(exc), parent=app)
        return
    stages, scope = _stage_scope(app)
    if not stages:
        messagebox.showinfo("未选择交付物", "请至少选择一项交付物。", parent=app)
        return
    objective = app.agent_objective.get("1.0", "end").strip() if hasattr(app, "agent_objective") else ""
    output_dir = app.agent_output_var.get().strip()
    if output_dir:
        try:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showwarning("输出目录不可用", str(exc), parent=app)
            return
    indexes = tuple(item.strip() for item in app.agent_reports_var.get().split(",") if item.strip())
    if "papers" in stages and not indexes:
        messagebox.showinfo("缺少报表项目索引", "整册底稿需要填写报表项目索引（如 1-010）；"
                                                  "留空将无法开始，请填写后再试。", parent=app)
        app.agent_state_var.set("整册底稿需要报表项目索引，未开始。")
        return
    config = {"objective": objective, "stages": stages, "report_indexes": indexes,
              "output_dir": output_dir, "max_steps": 20, "max_tool_calls": 40,
              "max_model_calls": 12}
    if not messagebox.askokcancel("开始审计 Agent", _manifest_text(app, snapshot, stages, scope, client, config),
                                  parent=app):
        app.agent_state_var.set("已取消；未发送任何资料。")
        return
    evidence_rows = {}
    if "papers" in stages and indexes:
        try:
            from . import evidence_ui
            evidence_rows = {index: evidence_ui.paper_rows(app, index) for index in indexes}
        except Exception:
            evidence_rows = {}
    def current_fingerprint():
        # 只读普通 Python 属性；不要在运行线程里调 Tk 变量。
        try:
            disk_template = fields.file_digest(snapshot.template_path)
        except OSError:
            disk_template = ""
        return (app._context_revision, snapshot.ledger_digest, disk_template,
                snapshot.template_path, _results_digest(app.results))

    context = snapshot.context(
        output_dir=output_dir, objective=objective, stages=stages, report_indexes=indexes,
        focus=app.agent_focus_var.get().strip(), materiality=app.agent_materiality_var.get().strip(),
        allowed_scope=scope, current_fingerprint=current_fingerprint,
        evidence_rows=evidence_rows,
        max_steps=config["max_steps"], max_tool_calls=config["max_tool_calls"],
        max_model_calls=config["max_model_calls"])
    app.agent_context = context
    app.agent_events = []
    app.agent_running = True
    app.agent_start_btn.configure(state="disabled")
    app.agent_stop_btn.configure(state="normal")
    app._set_busy(True, "审计 Agent 运行中…")
    app.agent_state_var.set("运行中；确认弹窗出现时请处理后继续。")
    runtime = agent_module.AgentRuntime(
        client, agent_tools.build_registry(include_papers="papers" in stages), context,
        emit=lambda event: app._post(lambda: _on_event(app, event)),
        approve=lambda request: _ask_approval(app, request),
        ask_user=lambda question, options: _ask_question(app, question, options),
        audit=lambda event, payload: _audit(app, event, payload))
    app.agent_runtime = runtime
    app.agent_run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    def work():
        try:
            result = runtime.run()
        except Exception as exc:
            result = agent_module.AgentRunResult(
                status="error", summary="本次运行失败，未完成部分见运行记录。",
                artifacts=(), gaps=tuple(context.gaps), events=tuple(runtime.events),
                started_at=getattr(runtime, "_started_at", ""),
                finished_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                error=f"{type(exc).__name__}: {exc}"[:500])
        app._post(lambda: _finish(app, runtime, result, context, config))

    threading.Thread(target=work, daemon=True).start()


def stop(app):
    runtime = getattr(app, "agent_runtime", None)
    if runtime is not None:
        runtime.cancel()
        app.agent_state_var.set("正在停止；等待当前本地写入结束…")


def _on_event(app, event):
    if not hasattr(app, "agent_tree"):
        return
    app.agent_events.append(event)
    label = {"tool": "工具", "tool_result": "结果", "approval": "确认", "question": "询问",
             "answer": "回复", "run_start": "开始", "run_end": "结束"}.get(event.kind, event.kind)
    tag = "approval" if event.kind in {"approval", "question"} else (
        "answer" if event.kind in {"answer", "run_end"} else "tool")
    app.agent_tree.insert("", "end", values=(event.at[11:19], label, (event.title + (" · " + event.detail if event.detail else ""))[:600]),
                          tags=(tag,))
    children = app.agent_tree.get_children()
    if children:
        app.agent_tree.see(children[-1])
    if event.kind not in {"tool", "tool_result"}:
        app.agent_state_var.set(event.title + ((" · " + event.detail) if event.detail else ""))


def _audit(app, event, payload):
    if getattr(app, "audit", None) is None:
        return
    try:
        app.audit.append(event, **(payload or {}))
    except Exception as exc:
        app.agent_audit_error = str(exc)


def _save_run_record(app, result, config):
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ZSZH-TB" / "agent" / "runs"
    try:
        base.mkdir(parents=True, exist_ok=True)
        fallback = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = base / f"{getattr(app, 'agent_run_id', fallback)}.json"
        record = {"run_id": getattr(app, "agent_run_id", ""), "config": config,
                  "result": result.as_dict()}
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return target
    except OSError:
        return None


def _finish(app, runtime, result, context, config):
    app._set_busy(False)
    app.agent_running = False
    app.agent_start_btn.configure(state="normal")
    app.agent_stop_btn.configure(state="disabled")
    app.agent_result = result
    if hasattr(app, "agent_artifacts"):
        for item in app.agent_artifacts.get_children():
            app.agent_artifacts.delete(item)
        for artifact in result.artifacts:
            app.agent_artifacts.insert("", "end", values=(artifact.get("kind", ""), artifact.get("name", ""),
                                                          artifact.get("sha256", "")[:16] + "…"))
    record = _save_run_record(app, result, config)
    note = f"已保存运行记录：{record}" if record else "运行记录未保存"
    if getattr(app, "agent_audit_error", ""):
        note += "；审计日志写入失败，请核对本机日志"
    if result.status == "done" and context.tb_exported and context.scan is not None:
        if context.snapshot.revision() == _current_fingerprint(app):
            if messagebox.askyesno("载入 TB 结果", "是否把本次 Agent 的 TB 结果载入「账套与 TB」工作区？\n"
                                                   "载入只影响界面，不修改原模板。", parent=app):
                app.scan = context.scan
                app.results = list(context.results)
                app.scan_state.set(f"已载入 Agent 结果 · {len(app.results)} 个字段，待处理 "
                                   f"{sum(1 for item in app.results if item.status not in {'matched', 'protected'})} 项")
                app._refresh_tree()
                workspace.refresh_context(app)
    state = {"done": "已完成", "partial": "部分完成", "cancelled": "已停止",
             "stale": "来源已变化", "blocked": "受阻", "needs_input": "等待确认"}.get(result.status, result.status)
    gap_text = ("\n\n未完成事项：\n" + "\n".join(f"· {item}" for item in result.gaps[:8])) if result.gaps else ""
    app.agent_state_var.set(f"{state} · {result.summary or ''} · {note}")
    messagebox.showinfo(f"审计 Agent · {state}", f"{result.summary or '运行结束'}{gap_text}\n\n{note}", parent=app)


def _ask_approval(app, request):
    pending = {"event": threading.Event(), "decision": agent_module.ApprovalDecision(False)}

    def show():
        dialog = ApprovalDialog(app, request)
        app.wait_window(dialog)
        pending["decision"] = dialog.result or agent_module.ApprovalDecision(False, note="用户关闭了确认窗口")
        pending["event"].set()

    app._post(show)
    if not pending["event"].wait(600):
        app._post(lambda: _on_event(app, agent_module.AgentEvent(0, datetime.now().astimezone().isoformat(timespec="seconds"),
                                                                  "warning", "确认超时", "未在 10 分钟内确认，已拒绝写入")))
        return agent_module.ApprovalDecision(False, note="确认超时")
    return pending["decision"]


def _ask_question(app, question, options):
    pending = {"event": threading.Event(), "answer": None}

    def show():
        dialog = QuestionDialog(app, question, options)
        app.wait_window(dialog)
        pending["answer"] = dialog.result
        pending["event"].set()

    app._post(show)
    if not pending["event"].wait(600):
        return None
    return pending["answer"]


class ApprovalDialog(tk.Toplevel):
    def __init__(self, parent, request):
        super().__init__(parent)
        self.title("审计 Agent 确认")
        self.transient(parent)
        theme.fit_window(self, 760, 460, min_width=560, min_height=300)
        self.result = None
        self.request = request
        self.path_var = tk.StringVar(value=request.default_path)
        body = tk.Frame(self, bg=CARD)
        body.pack(fill="both", expand=True, padx=18, pady=14)
        _label(body, request.title, fg=TX, size=13, weight="bold").pack(anchor="w")
        _label(body, request.summary, fg=SEC, wrap=680).pack(anchor="w", pady=(8, 6))
        for key, value in request.details or ():
            _label(body, f"{key}：{value}", fg=SEC, wrap=680).pack(anchor="w", pady=1)
        if request.kind == "write":
            row = tk.Frame(body, bg=CARD)
            row.pack(fill="x", pady=(12, 0))
            _label(row, "输出文件").pack(side="left")
            ttk.Entry(row, textvariable=self.path_var, width=58).pack(side="left", padx=6)
            ttk.Button(row, text="选择…", command=self._choose).pack(side="left")
            _label(body, "留空则使用默认项目目录；不会覆盖原模板/原数据库。", fg=SEC).pack(anchor="w", pady=(4, 0))
            self.auto_var = tk.BooleanVar(value=False)
            if request.allow_auto:
                ttk.Checkbutton(body, text="本次运行内同类写入不再询问（仍只写新文件）",
                                variable=self.auto_var).pack(anchor="w", pady=(8, 0))
        else:
            self.auto_var = tk.BooleanVar(value=False)
        buttons = tk.Frame(self, bg=CARD)
        buttons.pack(fill="x", padx=18, pady=(0, 14))
        ttk.Button(buttons, text="允许并继续", style="Accent.TButton", command=self._allow).pack(side="right")
        ttk.Button(buttons, text="拒绝", command=self._deny).pack(side="right", padx=8)
        ttk.Button(buttons, text="停止本次运行", command=self._stop).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", self._deny)
        self.grab_set()

    def _choose(self):
        path = filedialog.asksaveasfilename(parent=self, title="选择输出文件",
                                            defaultextension=".xlsx",
                                            filetypes=[("Excel 工作簿", "*.xlsx"), ("所有文件", "*.*")])
        if path:
            self.path_var.set(path)

    def _allow(self):
        self.result = agent_module.ApprovalDecision(True, self.path_var.get().strip(),
                                                    self.auto_var.get())
        self.destroy()

    def _deny(self):
        self.result = agent_module.ApprovalDecision(False, note="用户拒绝")
        self.destroy()

    def _stop(self):
        self.result = agent_module.ApprovalDecision(False, note="用户停止本次运行")
        try:
            stop(self.master)
        except Exception:
            pass
        self.destroy()


class QuestionDialog(tk.Toplevel):
    def __init__(self, parent, question, options):
        super().__init__(parent)
        self.title("审计 Agent 询问")
        self.transient(parent)
        theme.fit_window(self, 640, 320, min_width=480, min_height=220)
        self.result = None
        body = tk.Frame(self, bg=CARD)
        body.pack(fill="both", expand=True, padx=18, pady=14)
        _label(body, question, fg=TX, size=11, weight="bold", wrap=580).pack(anchor="w")
        self.answer = tk.StringVar(value="")
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x", pady=(12, 0))
        entry = ttk.Entry(row, textvariable=self.answer, width=56)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event: self._ok())
        if options:
            options_row = tk.Frame(body, bg=CARD)
            options_row.pack(fill="x", pady=(8, 0))
            for option in options[:6]:
                ttk.Button(options_row, text=option,
                           command=lambda value=option: (self.answer.set(value), self._ok())).pack(side="left", padx=(0, 6))
        buttons = tk.Frame(self, bg=CARD)
        buttons.pack(fill="x", padx=18, pady=(0, 14))
        ttk.Button(buttons, text="提交", style="Accent.TButton", command=self._ok).pack(side="right")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    def _ok(self):
        self.result = self.answer.get().strip() or None
        self.destroy()


def _selected_artifact(app):
    selected = app.agent_artifacts.selection()
    if not selected or not getattr(app, "agent_result", None):
        return None
    try:
        return app.agent_result.artifacts[int(selected[0])]
    except (IndexError, ValueError, TypeError):
        return None


def _open_artifact(app):
    artifact = _selected_artifact(app)
    if artifact:
        try:
            os.startfile(artifact["path"])
        except OSError as exc:
            messagebox.showerror("无法打开", str(exc), parent=app)


def _open_folder(app):
    artifact = _selected_artifact(app)
    if artifact:
        try:
            os.startfile(str(Path(artifact["path"]).parent))
        except OSError as exc:
            messagebox.showerror("无法打开", str(exc), parent=app)
