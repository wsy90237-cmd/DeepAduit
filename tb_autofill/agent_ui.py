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
    """对话式审计 Agent：中间对话，右侧本次任务与产物。"""
    page = app.pages["agent"]
    page.columnconfigure(0, weight=1)
    page.columnconfigure(1, weight=0)
    page.rowconfigure(1, weight=1)
    app.agent_events = []
    app.agent_result = None
    app.agent_running = False
    app.agent_runtime = None
    app.agent_context = None
    app.agent_audit_error = ""
    app.agent_context_var = tk.StringVar(value="尚未读取账套")
    app.agent_state_var = tk.StringVar(value="尚未开始")
    app.agent_stage_vars = {
        "tb": tk.BooleanVar(value=True),
        "papers": tk.BooleanVar(value=False),
    }
    app.agent_reports_var = tk.StringVar(value="")
    app.agent_focus_var = tk.StringVar(value="")
    app.agent_materiality_var = tk.StringVar(value="")
    app.agent_output_var = tk.StringVar(value="")

    top = tk.Frame(page, bg=BG)
    top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=24, pady=(20, 8))
    _label(top, "审计 Agent", fg=TX, size=20, weight="bold", bg=BG).pack(anchor="w")
    context = tk.Frame(top, bg=BG)
    context.pack(fill="x", pady=(6, 0))
    tk.Label(context, textvariable=app.agent_context_var, bg=BG, fg=SEC, font=theme.font(9),
             anchor="w").pack(side="left", fill="x", expand=True)
    ttk.Button(context, text="模型设置", command=app._open_ai_settings).pack(side="right")
    ttk.Button(context, text="工作台", command=lambda: workspace.navigate(app, "tb")).pack(side="right", padx=6)
    ttk.Button(context, text="任务范围", command=lambda: _open_scope(app)).pack(side="right")

    main = tk.Frame(page, bg=BG)
    main.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=24, pady=(0, 16))
    main.columnconfigure(0, weight=1, minsize=420)
    main.columnconfigure(1, weight=0, minsize=300)
    main.rowconfigure(0, weight=1)

    chat = tk.Frame(main, bg=CARD, highlightbackground=BORDER, highlightthickness=1, bd=0)
    chat.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
    chat.rowconfigure(1, weight=1)
    chat.columnconfigure(0, weight=1)

    app.agent_chat = tk.Text(chat, state="disabled", wrap="word", relief="flat", borderwidth=0,
                             width=1, height=1, bg=CARD, fg=TX, font=theme.font(9),
                             padx=14, pady=10, cursor="arrow")
    app.agent_chat.grid(row=1, column=0, sticky="nsew")
    app.agent_chat.tag_configure("me", foreground=ACC, font=theme.font(9, weight="bold"), spacing3=4)
    app.agent_chat.tag_configure("agent", foreground=TX, spacing3=4, lmargin2=10)
    app.agent_chat.tag_configure("sys", foreground=SEC, font=theme.font(8), spacing3=4)
    app.agent_chat.tag_configure("warning", foreground=theme.BAD, spacing3=4)
    scroll = ttk.Scrollbar(chat, orient="vertical", command=app.agent_chat.yview)
    scroll.grid(row=1, column=1, sticky="ns")
    app.agent_chat.configure(yscrollcommand=scroll.set)

    composer = tk.Frame(chat, bg=CARD)
    composer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(6, 12))
    composer.columnconfigure(0, weight=1)
    app.agent_input = tk.Text(composer, height=3, width=1, wrap="word", relief="flat", borderwidth=0,
                              bg=theme.SURFACE_ALT, fg=TX, font=theme.font(9), padx=9, pady=7,
                              highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACC)
    app.agent_input.grid(row=0, column=0, sticky="ew")
    app.agent_input.bind("<Return>", _on_send_key)
    buttons = tk.Frame(composer, bg=CARD)
    buttons.grid(row=1, column=0, sticky="ew", pady=(6, 0))
    app.agent_stop_btn = ttk.Button(buttons, text="停止", command=lambda: stop(app), state="disabled")
    app.agent_stop_btn.pack(side="right")
    app.agent_start_btn = ttk.Button(buttons, text="发送", style="Accent.TButton",
                                     command=lambda: _send(app))
    app.agent_start_btn.pack(side="right", padx=6)

    side = tk.Frame(main, bg=BG)
    side.grid(row=0, column=1, sticky="nsew")
    side.lift()
    side.columnconfigure(0, weight=1)
    side.rowconfigure(1, weight=1)
    status = tk.Frame(side, bg=CARD, highlightbackground=BORDER, highlightthickness=1, bd=0)
    status.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    _label(status, "本次任务", fg=TX, size=10, weight="bold").pack(anchor="w", padx=12, pady=(10, 2))
    tk.Label(status, textvariable=app.agent_state_var, bg=CARD, fg=SEC, font=theme.font(8),
             anchor="w", justify="left", wraplength=225).pack(fill="x", padx=12)
    tk.Button(status, text="清空对话", bg=CARD, fg=SEC, bd=0, activebackground=CARD,
              command=lambda: _clear(app)).pack(anchor="e", padx=10, pady=(2, 6))

    art_card = tk.Frame(side, bg=CARD, highlightbackground=BORDER, highlightthickness=1, bd=0)
    art_card.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
    art_card.rowconfigure(1, weight=1)
    art_card.columnconfigure(0, weight=1)
    _label(art_card, "本次产物", fg=TX, size=10, weight="bold").grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))
    app.agent_artifacts = ttk.Treeview(art_card, columns=("kind", "name"), show="headings", height=7)
    for key, title in (("kind", "类型"), ("name", "文件")):
        app.agent_artifacts.heading(key, text=title)
        app.agent_artifacts.column(key, width=60 if key == "kind" else 185, anchor="w")
    app.agent_artifacts.grid(row=1, column=0, sticky="nsew", padx=(12, 0))
    art_scroll = ttk.Scrollbar(art_card, orient="vertical", command=app.agent_artifacts.yview)
    art_scroll.grid(row=1, column=1, sticky="ns", padx=(4, 8))
    app.agent_artifacts.configure(yscrollcommand=art_scroll.set)
    app.agent_artifacts.bind("<Double-1>", lambda _event: _open_artifact(app))
    bar = tk.Frame(art_card, bg=CARD)
    bar.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 8))
    ttk.Button(bar, text="打开", command=lambda: _open_artifact(app)).pack(side="left")
    ttk.Button(bar, text="定位", command=lambda: _open_folder(app)).pack(side="left", padx=6)

    gaps = tk.Frame(side, bg=CARD, highlightbackground=BORDER, highlightthickness=1, bd=0)
    gaps.grid(row=2, column=0, sticky="ew")
    _label(gaps, "缺口与待复核", fg=TX, size=10, weight="bold").pack(anchor="w", padx=12, pady=(10, 2))
    app.agent_gaps = tk.Text(gaps, height=5, width=1, state="disabled", wrap="word", relief="flat", bd=0,
                             bg=CARD, fg=SEC, font=theme.font(8), padx=12, pady=2)
    app.agent_gaps.pack(fill="x", padx=0, pady=(0, 8))
    refresh(app)


def _choose_output(app):
    path = filedialog.askdirectory(title="选择 Agent 输出目录", parent=app)
    if path:
        app.agent_output_var.set(path)


def _open_scope(app):
    ScopeDialog(app)


class ScopeDialog(tk.Toplevel):
    """任务范围只在需要时弹出，不在对话页常驻字段说明。"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("任务范围")
        self.transient(app)
        theme.fit_window(self, 460, 360, min_width=380, min_height=300)
        self.tb_var = tk.BooleanVar(value=app.agent_stage_vars["tb"].get())
        self.papers_var = tk.BooleanVar(value=app.agent_stage_vars["papers"].get())
        self.reports = tk.StringVar(value=app.agent_reports_var.get())
        self.focus = tk.StringVar(value=app.agent_focus_var.get())
        self.materiality = tk.StringVar(value=app.agent_materiality_var.get())
        self.output = tk.StringVar(value=app.agent_output_var.get())
        body = tk.Frame(self, bg=CARD)
        body.pack(fill="both", expand=True, padx=16, pady=14)
        _label(body, "本次范围", fg=TX, size=11, weight="bold").pack(anchor="w")
        for text, variable in (("TB 副本", self.tb_var), ("整册底稿", self.papers_var)):
            ttk.Checkbutton(body, text=text, variable=variable).pack(anchor="w", pady=(4, 0))
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x", pady=(8, 0))
        _label(row, "报表项目").pack(side="left")
        ttk.Entry(row, textvariable=self.reports, width=22).pack(side="left", padx=6)
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x", pady=(4, 0))
        _label(row, "关注点").pack(side="left")
        ttk.Entry(row, textvariable=self.focus, width=22).pack(side="left", padx=6)
        ttk.Entry(row, textvariable=self.materiality, width=10).pack(side="left")
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x", pady=(4, 0))
        _label(row, "输出目录").pack(side="left")
        ttk.Entry(row, textvariable=self.output, width=26).pack(side="left", padx=6)
        ttk.Button(row, text="选择", command=self._choose).pack(side="left")
        buttons = tk.Frame(self, bg=CARD)
        buttons.pack(fill="x", padx=16, pady=(0, 14))
        ttk.Button(buttons, text="确定", style="Accent.TButton", command=self._ok).pack(side="right")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right", padx=8)
        self.grab_set()

    def _choose(self):
        path = filedialog.askdirectory(title="选择输出目录", parent=self)
        if path:
            self.output.set(path)

    def _ok(self):
        self.app.agent_stage_vars["tb"].set(self.tb_var.get())
        self.app.agent_stage_vars["papers"].set(self.papers_var.get())
        self.app.agent_reports_var.set(self.reports.get().strip())
        self.app.agent_focus_var.set(self.focus.get().strip())
        self.app.agent_materiality_var.set(self.materiality.get().strip())
        self.app.agent_output_var.set(self.output.get().strip())
        self.destroy()


def _append_message(app, role, text):
    if not hasattr(app, "agent_chat"):
        return
    prefix = {"me": "你\n", "agent": "Agent\n", "sys": "· ", "warning": "⚠ "}[role]
    tag = role if role in {"me", "agent", "warning"} else "sys"
    app.agent_chat.configure(state="normal")
    app.agent_chat.insert("end", prefix, tag)
    app.agent_chat.insert("end", str(text).strip() + "\n\n", tag)
    app.agent_chat.configure(state="disabled")
    app.agent_chat.see("end")


def _set_gaps(app, gaps):
    if not hasattr(app, "agent_gaps"):
        return
    app.agent_gaps.configure(state="normal")
    app.agent_gaps.delete("1.0", "end")
    app.agent_gaps.insert("1.0", "\n".join(f"· {item}" for item in gaps) or "暂无缺口。")
    app.agent_gaps.configure(state="disabled")


def _on_send_key(event):
    if event.state & 0x0001:  # Shift+Enter 换行
        return None
    _send(event.widget.winfo_toplevel())
    return "break"


def _send(app):
    if app.agent_running or app.busy:
        return
    text = app.agent_input.get("1.0", "end").strip()
    if not text:
        return
    _append_message(app, "me", text)
    app.agent_input.delete("1.0", "end")
    if not app.rows or app.loaded_subject is None:
        _append_message(app, "agent", "还没有读取账套。请先在工作台读取科目余额并选择模板。")
        return
    template = app.template_var.get().strip()
    if not template or not Path(template).is_file():
        _append_message(app, "agent", "还没有选择模板。请先在工作台选择模板。")
        return
    start(app, objective=text)


def _clear(app):
    if app.agent_running:
        return
    app.agent_events = []
    app.agent_chat.configure(state="normal")
    app.agent_chat.delete("1.0", "end")
    app.agent_chat.configure(state="disabled")
    app.agent_input.delete("1.0", "end")
    for item in app.agent_artifacts.get_children():
        app.agent_artifacts.delete(item)
    _set_gaps(app, [])
    app.agent_state_var.set("已清空对话；可输入新的任务。")


def refresh(app):
    if not hasattr(app, "agent_context_var"):
        return
    subject = getattr(app, "loaded_subject", None)
    if subject is None:
        app.agent_context_var.set("尚未读取账套；请先在「工作台」读取科目余额并选择模板。")
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
    raw = app.template_var.get().strip()
    template = str(Path(raw)) if raw else ""
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


def start(app, objective=None):
    if app.busy or app.agent_running:
        _append_message(app, "sys", "当前任务尚未完成，请先停止或等待。")
        return
    if getattr(app, "store", None) is None or not app.store.has_key or not app.store.data.get("ai_enabled"):
        _append_message(app, "agent", "还没有启用模型。请先在「模型设置」输入 DeepSeek API Key 并启用。")
        return
    if not app.rows or app.loaded_subject is None:
        _append_message(app, "agent", "还没有读取账套。请先在工作台读取科目余额并选择模板。")
        return
    try:
        snapshot = _snapshot(app)
        client = ai.DeepSeekClient.from_store(app.store)
    except (ValueError, ai.AIError, settings.SettingsError, OSError) as exc:
        _append_message(app, "agent", str(exc))
        return
    stages, scope = _stage_scope(app)
    if not stages:
        _append_message(app, "agent", "请先在「任务范围」中选择至少一项交付物。")
        return
    if objective is None:
        objective = app.agent_objective.get("1.0", "end").strip() if hasattr(app, "agent_objective") else ""
    objective = str(objective or "").strip()
    output_dir = app.agent_output_var.get().strip()
    if output_dir:
        try:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showwarning("输出目录不可用", str(exc), parent=app)
            return
    indexes = tuple(item.strip() for item in app.agent_reports_var.get().split(",") if item.strip())
    if "papers" in stages and not indexes:
        _append_message(app, "agent", "整册底稿需要在「任务范围」中填写报表项目索引（如 1-010）。")
        app.agent_state_var.set("需要报表项目索引")
        return
    config = {"objective": objective, "stages": stages, "report_indexes": indexes,
              "output_dir": output_dir, "max_steps": 20, "max_tool_calls": 40,
              "max_model_calls": 12}
    plan = (f"本次范围：{'、'.join({'tb': 'TB 副本', 'papers': '整册底稿'}.get(stage, stage) for stage in stages)}；"
            f"报表项目：{'、'.join(indexes) or '无'}；输出：{output_dir or '默认项目目录'}。")
    _append_message(app, "sys", plan + " 请确认后开始。")
    if not messagebox.askokcancel("开始审计 Agent", _manifest_text(app, snapshot, stages, scope, client, config),
                                  parent=app):
        _append_message(app, "sys", "已取消；未发送任何资料。")
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
    _append_message(app, "sys", "已确认本次范围，开始执行。")

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
    app.agent_events.append(event)
    if event.kind == "run_start":
        _append_message(app, "sys", event.title)
    elif event.kind == "tool":
        _append_message(app, "sys", f"工具：{event.title}")
    elif event.kind == "tool_result" and not event.data.get("ok"):
        _append_message(app, "warning", f"{event.title}：{event.detail}")
    elif event.kind in {"approval", "question"}:
        _append_message(app, "sys", f"{event.title}：{event.detail}")
    elif event.kind == "warning":
        _append_message(app, "warning", event.title + (("：" + event.detail) if event.detail else ""))
    if event.kind not in {"tool", "tool_result", "model_request", "answer"}:
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
        labels = {"tb": "TB 副本", "tb_pending": "待处理清单", "workpaper": "整册底稿"}
        for artifact in result.artifacts:
            app.agent_artifacts.insert("", "end", values=(
                labels.get(artifact.get("kind", ""), artifact.get("kind", "")),
                artifact.get("name", "")))
    _set_gaps(app, result.gaps)
    record = _save_run_record(app, result, config)
    note = f"已保存运行记录：{record}" if record else "运行记录未保存"
    if getattr(app, "agent_audit_error", ""):
        note += "；审计日志写入失败，请核对本机日志"
    if result.status == "done" and context.tb_exported and context.scan is not None:
        if context.snapshot.revision() == _current_fingerprint(app):
            if messagebox.askyesno("载入 TB 结果", "是否把本次 Agent 的 TB 结果载入「工作台」的 TB 工作区？\n"
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
    _append_message(app, "agent" if result.status == "done" else "warning",
                    f"{state}。{result.summary or ''}{gap_text}\n{note}")
    try:
        app.agent_input.focus_set()
    except tk.TclError:
        pass


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
