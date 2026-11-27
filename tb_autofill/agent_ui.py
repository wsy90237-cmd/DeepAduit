"""审计 Agent 页面：任务配置、时间线、确认弹窗与产物列表。

界面只负责收集范围、启动工作线程、把运行时的确认请求转成 Tk 弹窗；金额、匹配和
文件写入仍由 ``agent_tools`` 调用现有本地模块完成。
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import random
import re
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, ttk

from . import agent as agent_module
from . import agent_checkpoints, agent_paths, agent_tools, ai, audit_index, brand, fields, layout, settings, theme, workspace
from . import dialogs as messagebox
from .models import ledger_digest

BG, CARD = theme.PAPER, theme.SURFACE
BORDER, TX, SEC = theme.HAIRLINE, theme.INK, theme.MUTED
ACC = theme.ACCENT
_GREETINGS = (
    "今天想先核对什么？",
    "我们从哪项审计任务开始？",
    "有什么审计工作可以帮你？",
    "准备好了，想从哪里开始？",
    "你好，今天从哪儿开始？",
)


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
    app.agent_project_artifacts = {}
    app.agent_paper_request = None
    app.agent_question_submit = None
    app.agent_context_var = tk.StringVar(value="尚未读取账套")
    app.agent_state_var = tk.StringVar(value="尚未开始")

    main = tk.Frame(page, bg=BG)
    main.grid(row=0, column=0, rowspan=2, columnspan=2, sticky="nsew", padx=32, pady=(0, 20))
    page.rowconfigure(0, weight=1)
    main.columnconfigure(0, weight=1)
    main.rowconfigure(1, weight=1)
    controls = tk.Frame(main, bg=BG)
    controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
    controls.grid_remove()
    app.agent_resume_btn = ttk.Button(controls, text="继续待办任务", style="Quiet.TButton",
                                      command=lambda: _pick_pending(app))
    details_btn = ttk.Button(controls, text="任务详情", style="Quiet.TButton")

    # ---- 对话：用户消息是浅底块，Agent 回复是正文，工具过程是一行灰字 ----
    chat = tk.Frame(main, bg=BG)
    chat.grid(row=1, column=0, sticky="nsew", padx=(0, 20))
    chat.rowconfigure(0, weight=1)
    chat.columnconfigure(0, weight=1)
    app.agent_chat = tk.Text(chat, state="disabled", wrap="word", relief="flat", borderwidth=0,
                             width=1, height=1, bg=BG, fg=TX, font=theme.font(10),
                             padx=4, pady=8, cursor="arrow", highlightthickness=0,
                             spacing1=2, spacing3=2)
    app.agent_chat.grid(row=0, column=0, sticky="nsew")
    # 用户消息是右侧的浅底气泡（内嵌窗口），回复是正文，工具过程只留一行安静的灰字。
    app.agent_bubbles = []

    def fit_bubbles(_event=None):
        width = app.agent_chat.winfo_width()
        for spacer, holder in app.agent_bubbles:
            try:
                spacer.configure(width=max(0, width - holder.winfo_reqwidth() - theme.px(app, 40)))
            except tk.TclError:
                pass

    app.agent_fit_bubbles = fit_bubbles
    app.agent_chat.bind("<Configure>", fit_bubbles, add="+")
    reply = theme.px(app, 40)
    app.agent_chat.tag_configure("agent", foreground=TX, font=theme.font(10, family="display"),
                                 lmargin1=reply, lmargin2=reply, spacing1=theme.px(app, 4),
                                 spacing3=theme.px(app, 14))
    app.agent_chat.tag_configure("agent_bold", foreground=TX,
                                 font=theme.font(10, weight="bold", family="display"),
                                 lmargin1=reply, lmargin2=reply)
    app.agent_chat.tag_configure("sys", foreground=SEC, font=theme.font(8),
                                 lmargin1=reply, lmargin2=reply, spacing3=2)
    app.agent_chat.tag_configure("warning", foreground=theme.BAD, font=theme.font(9), spacing3=4)
    app.agent_chat.tag_configure("gap", font=theme.font(4))
    app.agent_chat.tag_configure("thought", foreground=theme.INK_SOFT, font=theme.font(10, family="display"),
                                 lmargin1=reply, lmargin2=reply, spacing1=theme.px(app, 8),
                                 spacing3=theme.px(app, 6))
    app.agent_chat.tag_configure("result", foreground=SEC, font=theme.font(8),
                                 lmargin1=reply + theme.px(app, 14), lmargin2=reply + theme.px(app, 14),
                                 spacing3=2)
    app.agent_avatar_photo = _avatar_photo(app)
    app.agent_avatar_canvas = None
    app.agent_turn_avatar = None
    scroll = ttk.Scrollbar(chat, orient="vertical", command=app.agent_chat.yview)
    scroll.grid(row=0, column=1, sticky="ns")
    app.agent_chat.configure(yscrollcommand=scroll.set)

    app.agent_welcome = tk.Frame(chat, bg=BG)
    app.agent_greeting = _label(app.agent_welcome, "", fg=TX, size=16, weight="bold", bg=BG)
    app.agent_greeting.pack(anchor="w", pady=(0, 12))
    for text in ("复核 TB 并列出待处理项", "制作 1-010 货币资金底稿"):
        ttk.Button(app.agent_welcome, text=text, style="Quiet.TButton",
                   command=lambda text=text: suggest(app, text)).pack(anchor="w", pady=2)
    ttk.Button(app.agent_welcome, text="查看可制作的底稿", style="Quiet.TButton",
               command=lambda: workspace.navigate(app, "papers")).pack(anchor="w", pady=2)
    _show_welcome(app)

    # 确认与提问直接显示在对话页里，不再依赖弹窗：弹窗在部分 Windows 环境下看不到。
    app.agent_prompt = tk.Frame(chat, bg=CARD, highlightthickness=2, highlightbackground=ACC,
                                highlightcolor=ACC, bd=0)
    app.agent_prompt.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
    app.agent_prompt.grid_remove()
    app.agent_prompt_queue = []
    app.agent_prompt_active = None

    composer = tk.Frame(chat, bg=CARD, highlightthickness=1, highlightbackground=theme.HAIRLINE_STRONG,
                        highlightcolor=ACC)
    composer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
    composer.columnconfigure(0, weight=1)
    app.agent_input = tk.Text(composer, height=3, width=1, wrap="word", relief="flat", borderwidth=0,
                              bg=CARD, fg=TX, font=theme.font(10), padx=12, pady=10,
                              highlightthickness=0, insertbackground=TX)
    app.agent_input.grid(row=0, column=0, sticky="ew")
    app.agent_input.bind("<Return>", _on_send_key)
    app.agent_input_hint = tk.Label(composer, text="描述你的审计任务，或直接追问…", bg=CARD,
                                    fg=SEC, font=theme.font(10))
    app.agent_input_hint.place(x=theme.px(app, 14), y=theme.px(app, 10))
    app.agent_input_hint.bind("<Button-1>", lambda _event: app.agent_input.focus_set())
    for sequence in ("<KeyPress>", "<Button-1>", "<<Paste>>"):
        app.agent_input.bind(sequence, lambda _event: app.agent_input_hint.place_forget(), add="+")
    app.agent_send_preview = _label(composer, "", fg=SEC, size=8)
    app.agent_send_preview.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4))
    composer.bind("<Configure>", lambda event: app.agent_send_preview.configure(
        wraplength=max(1, event.width - theme.px(app, 24))), add="+")
    buttons = tk.Frame(composer, bg=CARD)
    buttons.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
    app.agent_review_btn = ttk.Button(buttons, text="核对明细", style="CardQuiet.TButton",
                                      command=app._open_tb_review, state="disabled")
    app.agent_review_btn.pack(side="left")
    ttk.Button(buttons, text="清空", style="CardQuiet.TButton",
               command=lambda: _clear(app)).pack(side="left")
    app.agent_start_btn = ttk.Button(buttons, text="发送", style="Accent.TButton",
                                     command=lambda: _send(app))
    app.agent_start_btn.pack(side="right")
    app.agent_stop_btn = ttk.Button(buttons, text="停止", command=lambda: stop(app), state="disabled")
    app.agent_stop_btn.pack(side="right", padx=6)

    # ---- 右侧：本次运行 ----
    side_area = layout.scroll_host(main, bg=BG)
    side_area.canvas.configure(width=theme.px(app, 320), height=1)
    side_area.grid(row=1, column=1, sticky="nsew")
    app.agent_side_area = side_area
    side = side_area.body
    compact_details = False

    def arrange(_event=None, *, show_chat=False, toggle=False):
        nonlocal compact_details
        compact = main.winfo_width() < theme.px(app, 790)
        if show_chat or not compact:
            compact_details = False
        elif toggle:
            compact_details = not compact_details
        has_pending = bool(getattr(app, "agent_has_pending", False))
        if compact or has_pending:
            controls.grid()
        else:
            controls.grid_remove()
        if has_pending:
            app.agent_resume_btn.pack(side="left")
        else:
            app.agent_resume_btn.pack_forget()
        if compact:
            details_btn.configure(text="返回对话" if compact_details else "任务详情")
            details_btn.pack(side="right")
            if compact_details:
                chat.grid_remove()
                side_area.grid(row=1, column=0, sticky="nsew")
            else:
                side_area.grid_remove()
                chat.grid(padx=0)
        else:
            details_btn.pack_forget()
            chat.grid(padx=(0, 20))
            side_area.grid(row=1, column=1, sticky="nsew")

    details_btn.configure(command=lambda: arrange(toggle=True))
    main.bind("<Configure>", arrange, add="+")
    app.agent_arrange = arrange

    def panel(row, title):
        card = tk.Frame(side, bg=CARD, highlightbackground=BORDER, highlightthickness=1, bd=0)
        card.grid(row=row, column=0, sticky="nsew", pady=(0, 12))
        _label(card, title, fg=TX, size=11, weight="bold").pack(anchor="w", padx=14, pady=(12, 6))
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        return body

    task = panel(0, "本次任务")
    tk.Label(task, textvariable=app.agent_state_var, bg=CARD, fg=TX, font=theme.font(9),
             anchor="w", justify="left", wraplength=theme.px(app, 260)).pack(fill="x")
    tk.Label(task, textvariable=app.agent_context_var, bg=CARD, fg=SEC, font=theme.font(8),
             anchor="w", justify="left", wraplength=theme.px(app, 260)).pack(fill="x", pady=(4, 0))
    app.agent_scope_row = tk.Frame(task, bg=CARD)
    app.agent_scope_row.pack(fill="x", pady=(8, 0))

    budget = panel(1, "预算")
    app.agent_meters = {}
    for key, title in (("model", "模型请求"), ("tool", "工具调用"), ("time", "用时")):
        meter = theme.Meter(budget, title)
        meter.pack(fill="x", pady=(0, 8))
        app.agent_meters[key] = meter

    art = panel(2, "产物")
    art.rowconfigure(0, weight=1)
    art.columnconfigure(0, weight=1)
    app.agent_artifacts = ttk.Treeview(art, columns=("kind", "name"), show="headings", height=4,
                                       selectmode="browse")
    app.agent_artifacts.heading("kind", text="类型", anchor="w")
    app.agent_artifacts.heading("name", text="文件", anchor="w")
    app.agent_artifacts.column("kind", width=theme.px(app, 70), anchor="w", stretch=False)
    app.agent_artifacts.column("name", width=theme.px(app, 190), anchor="w")
    app.agent_artifacts.grid(row=0, column=0, sticky="nsew")
    app.agent_artifacts.bind("<Double-1>", lambda event: _open_artifact(app)
                             if app.agent_artifacts.identify_row(event.y) else None)
    app.agent_artifacts.bind("<Return>", lambda _event: _open_artifact(app))
    app.agent_artifacts_empty = tk.Label(art, text="暂无", bg=CARD, fg=SEC, font=theme.font(9))
    app.agent_artifacts_empty.grid(row=0, column=0, sticky="nw", padx=2, pady=2)
    bar = tk.Frame(art, bg=CARD)
    bar.grid(row=1, column=0, sticky="w", pady=(6, 0))
    app.agent_open_btn = ttk.Button(bar, text="打开", style="CardQuiet.TButton",
                                    command=lambda: _open_artifact(app), state="disabled")
    app.agent_folder_btn = ttk.Button(bar, text="所在文件夹", style="CardQuiet.TButton",
                                      command=lambda: _open_folder(app), state="disabled")
    app.agent_open_btn.pack(side="left")
    app.agent_folder_btn.pack(side="left")
    app.agent_reexport_btn = ttk.Button(bar, text="重新导出", style="CardQuiet.TButton",
                                        command=lambda: reexport(app), state="disabled")
    app.agent_reexport_btn.pack(side="left")

    gaps = panel(3, "缺口")
    app.agent_gaps = _label(gaps, "无", fg=TX, wrap=theme.px(app, 250))
    app.agent_gaps.pack(fill="x")
    gaps.bind("<Configure>", lambda event: app.agent_gaps.configure(wraplength=max(1, event.width)), add="+")
    _reset_meters(app)
    _render_scope(app)
    refresh(app)


_SCOPE_LABELS = {"ledger": "科目余额", "tb": "TB", "journal": "序时账", "workpaper": "底稿",
                 "knowledge": "知识库"}


def _render_scope(app, scope=()):
    """显示本次确认的数据类别。"""
    row = getattr(app, "agent_scope_row", None)
    if row is None:
        return
    for child in row.winfo_children():
        child.destroy()
    for key in ("ledger", "tb", "journal", "workpaper", "knowledge"):
        if key in scope:
            theme.Pill(row, _SCOPE_LABELS[key], "info").pack(side="left", padx=(0, 4), pady=(0, 4))


def _reset_meters(app, context=None):
    meters = getattr(app, "agent_meters", None)
    if not meters:
        return
    budget = getattr(context, "budget", None)
    if budget is None:
        meters["model"].set_usage(0, agent_module.DEFAULT_MAX_MODEL_CALLS)
        meters["tool"].set_usage(0, agent_module.DEFAULT_MAX_TOOL_CALLS)
        minutes = agent_module.DEFAULT_MAX_SECONDS // 60
        meters["time"].set_usage(0, agent_module.DEFAULT_MAX_SECONDS, f"0:00 / {minutes}:00")
        return
    _update_meters(app, context)


def _update_meters(app, context):
    meters = getattr(app, "agent_meters", None)
    budget = getattr(context, "budget", None)
    if not meters or budget is None:
        return
    import time as _time
    meters["model"].set_usage(budget.model_calls, budget.max_model_calls)
    meters["tool"].set_usage(budget.tool_calls, budget.max_tool_calls)
    elapsed = int(max(0, _time.monotonic() - budget.started_at))
    limit = int(budget.max_seconds)
    meters["time"].set_usage(elapsed, limit, f"{elapsed // 60}:{elapsed % 60:02d} / {limit // 60}:{limit % 60:02d}")


def _missing_workpapers(context):
    """已准备好草稿、但还没有有效文件的报表项目。"""
    if context is None:
        return []
    done = {str(item.get("target")) for item in context.artifacts
            if item.get("kind") == "workpaper" and item.get("path") and Path(item["path"]).is_file()
            and item.get("sha256") == agent_module._sha256(item["path"])}
    return [index for index in context.prepared_workpapers if index not in done]


def _sync_reexport(app):
    button = getattr(app, "agent_reexport_btn", None)
    if button is None:
        return
    pending = [] if app.agent_running else _missing_workpapers(getattr(app, "agent_context", None))
    button.configure(state="normal" if pending else "disabled",
                     text=f"重新导出 {len(pending)} 册" if pending else "重新导出")
    empty = getattr(app, "agent_artifacts_empty", None)
    if empty is not None:
        empty.configure(text=f"{len(pending)} 册草稿待保存" if pending else "暂无")


def _show_artifacts(app, context=None):
    if not hasattr(app, "agent_artifacts"):
        return
    tree = app.agent_artifacts
    subject = app.loaded_subject
    artifacts = app.agent_project_artifacts.setdefault(subject, {}) if subject is not None else {}
    if context is not None and context.subject == subject:
        artifacts.update((item["path"], item) for item in list(context.artifacts))
    labels = {"tb": "TB 副本", "tb_pending": "待处理清单", "workpaper": "整册底稿"}
    for path in tree.get_children():
        if path not in artifacts:
            tree.delete(path)
    for path, artifact in artifacts.items():
        kind = artifact["kind"]
        values = (labels.get(kind, kind), artifact["name"])
        if tree.exists(path):
            tree.item(path, values=values)
        else:
            tree.insert("", "end", iid=path, values=values)
    if artifacts and not tree.selection():
        selected = next(reversed(artifacts))
        tree.selection_set(selected)
        tree.focus(selected)
    _sync_empty(app, bool(artifacts))
    state = "normal" if artifacts else "disabled"
    app.agent_open_btn.configure(state=state)
    app.agent_folder_btn.configure(state=state)


def _sync_empty(app, has_items):
    label = getattr(app, "agent_artifacts_empty", None)
    if label is None:
        return
    tree = getattr(app, "agent_artifacts", None)
    if tree is not None:
        try:
            tree.configure(show="headings" if has_items else "")
        except tk.TclError:
            pass
    if has_items:
        label.grid_remove()
    else:
        label.grid()
        label.lift()


def _tick(app, runtime):
    """运行中每秒刷新用时；只读预算数字，不碰运行线程的状态。"""
    if app.agent_runtime is not runtime:
        return
    _update_meters(app, getattr(app, "agent_context", None))
    try:
        app.after(1000, lambda: _tick(app, runtime))
    except tk.TclError:
        pass


def reexport(app):
    """只重新导出已准备好的底稿：沿用上次的草稿文字，不再调用模型。"""
    context = getattr(app, "agent_context", None)
    pending = _missing_workpapers(context)
    if app.agent_running or app.busy or not pending or context.subject != app.loaded_subject:
        return
    registry = agent_tools.build_registry()
    spec = registry.get("export_workpaper")
    jobs = []

    def ask(position):
        if position >= len(pending):
            _run_reexport(app, context, jobs)
            return
        index = pending[position]
        arguments = {"index_no": index}
        details = tuple(spec.approval_details(context, arguments)) if spec.approval_details else ()
        request = agent_module.ApprovalRequest(
            kind="write", title="确认写入文件", summary=f"重新导出底稿：{index}（不调用模型）",
            details=(("工具", spec.name), *details),
            default_path=spec.default_path(context, arguments) if spec.default_path else "")

        def done(decision):
            if decision.approved:
                jobs.append((index, decision.path))
            elif "停止" in (decision.note or ""):
                return
            ask(position + 1)

        prompt_approval(app, request, done)

    ask(0)


def _run_reexport(app, context, jobs):
    if not jobs or app.agent_running or app.busy or app.agent_context is not context:
        return
    app.agent_running = True
    app._set_busy(True, "正在重新导出底稿…")
    app.agent_start_btn.configure(state="disabled")
    _sync_reexport(app)
    app.agent_state_var.set(f"正在重新导出 {len(jobs)} 册底稿…")

    def work():
        outcomes = []
        audit_errors = []
        for index, path in jobs:
            runtime = SimpleNamespace(approved_path=path, client=None, _audit=lambda *_a, **_k: None)
            try:
                outcome = agent_tools.export_workpaper(context, {"index_no": index}, runtime)
            except agent_module.AgentError as exc:
                outcome = {"ok": False, "error": str(exc)}
            except Exception as exc:  # 本地导出失败不能让界面卡在“运行中”
                outcome = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
            outcomes.append((index, outcome))
            error = _audit(app, "agent_reexport", {"index_no": index, "ok": bool(outcome.get("ok")),
                                                   "error": str(outcome.get("error") or "")[:300]})
            if error and not audit_errors:
                audit_errors.append(error)
        app._post(lambda: _reexport_done(app, context, outcomes, audit_errors))

    threading.Thread(target=work, daemon=True).start()


def _reexport_done(app, context, outcomes, audit_errors=()):
    app.agent_running = False
    app._set_busy(False)
    app.agent_start_btn.configure(state="normal")
    _show_artifacts(app, context)
    result = getattr(app, "agent_result", None)
    ok = sum(1 for _index, outcome in outcomes if outcome.get("ok"))
    headline = f"重新导出完成 {ok}/{len(outcomes)}"
    if result is not None:
        app.agent_result = dataclasses.replace(
            result, artifacts=tuple(dict(item) for item in context.artifacts), gaps=tuple(context.gaps),
            status=("partial" if context.gaps or ok < len(outcomes) else "done") if ok else result.status,
            summary=headline if ok else result.summary)
        record = getattr(app, "agent_run_record", None)
        if record:
            try:
                history = json.loads(record.read_text(encoding="utf-8"))
                history.setdefault("original_result", history["result"])
                history["result"] = app.agent_result.as_dict()
                history.setdefault("reexports", []).append({
                    "time": datetime.now().isoformat(timespec="seconds"), "outcomes": outcomes})
                agent_checkpoints.write_json(record, history)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                _append_message(app, "warning", f"重导结果未写入运行记录：{exc}")
    _set_gaps(app, context.gaps)
    for error in audit_errors:
        _append_message(app, "warning", error)
    for index, outcome in outcomes:
        if outcome.get("ok"):
            _append_message(app, "agent", f"已重新导出 {index}：{(outcome.get('data') or {}).get('output', '')}")
            for note in outcome.get("warnings") or []:
                _append_message(app, "sys", note)
        else:
            _append_message(app, "warning", f"{index} 未导出：{outcome.get('error')}")
    app.agent_state_var.set(headline)
    _sync_reexport(app)


_CLOSING = "，。、；：？！）】》」』”’,.;:?!)]}%"


def _avatar_photo(app):
    """小尺寸的品牌图标，用于回复左侧的头像。"""
    try:
        from PIL import Image, ImageTk
        source = Image.open(brand.icon(256)).convert("RGBA")
        size = theme.px(app, 22)
        return ImageTk.PhotoImage(source.resize((size, size), Image.LANCZOS), master=app)
    except (ImportError, OSError, ValueError, tk.TclError):
        return None


def _avatar_canvas(app):
    photo = getattr(app, "agent_avatar_photo", None)
    if photo is None:
        return None
    size = theme.px(app, 36)
    canvas = tk.Canvas(app.agent_chat, width=size, height=size, bg=BG, highlightthickness=0, bd=0)
    canvas.create_image(size // 2, size // 2, image=photo)
    return canvas


def _spin_avatar(app, canvas, dot, angle):
    """回复期间图标外有一颗绕圈的小点；结束或换到下一条回复时停下。"""
    if canvas is not getattr(app, "agent_avatar_canvas", None) or not getattr(app, "agent_running", False):
        try:
            canvas.delete(dot)
        except tk.TclError:
            pass
        return
    radius, centre = theme.px(app, 17), theme.px(app, 18)
    radians = angle * math.pi / 12
    x, y = centre + radius * math.cos(radians), centre + radius * math.sin(radians)
    try:
        canvas.coords(dot, x - 2.5, y - 2.5, x + 2.5, y + 2.5)
    except tk.TclError:
        return
    app.after(80, lambda: _spin_avatar(app, canvas, dot, angle + 1))


def _start_avatar(app, canvas):
    if canvas is None or not getattr(app, "agent_running", False):
        return
    dot = canvas.create_oval(0, 0, 0, 0, fill=ACC, outline="")
    app.agent_avatar_canvas = canvas
    _spin_avatar(app, canvas, dot, 0)


def _bubble_lines(app, text, limit):
    """按像素换行，并且不把中文标点放到行首。"""
    font = getattr(app, "agent_bubble_font", None)
    if font is None:
        font = app.agent_bubble_font = tkfont.Font(root=app, font=theme.font(10))
    lines = []
    for paragraph in str(text).split("\n"):
        while paragraph and font.measure(paragraph) > limit:
            low, high = 1, len(paragraph)
            while low < high:
                middle = (low + high + 1) // 2
                if font.measure(paragraph[:middle]) <= limit:
                    low = middle
                else:
                    high = middle - 1
            cut = low
            while cut > 1 and paragraph[cut] in _CLOSING:
                cut -= 1
            lines.append(paragraph[:cut].rstrip())
            paragraph = paragraph[cut:].lstrip(" ")
        lines.append(paragraph)
    return "\n".join(lines)


def _insert_prose(app, text, tag):
    """把模型常用的 **加粗** 渲染成字重，而不是把星号原样显示。"""
    if tag != "agent" or "**" not in text:
        app.agent_chat.insert("end", text + "\n", tag)
        return
    for index, piece in enumerate(re.split(r"\*\*(.+?)\*\*", text, flags=re.S)):
        if piece:
            app.agent_chat.insert("end", piece, "agent_bold" if index % 2 else tag)
    app.agent_chat.insert("end", "\n", tag)


def _append_message(app, role, text):
    if not hasattr(app, "agent_chat"):
        return
    prefix = {"me": "", "agent": "", "sys": "· ", "warning": "! ", "thought": "", "result": "  "}[role]
    tag = role if role in {"me", "agent", "warning", "thought", "result"} else "sys"
    _hide_welcome(app)
    app.agent_chat.configure(state="normal")
    if role == "me":
        app.agent_turn_avatar = None
        width = max(app.agent_chat.winfo_width(), theme.px(app, 520))
        pad = theme.px(app, 14)
        body = _bubble_lines(app, text, min(theme.px(app, 460), int(width * 0.62)) - pad * 2 - 2)
        holder = tk.Frame(app.agent_chat, bg=BG)
        card = tk.Frame(holder, bg=theme.PAPER_DEEP)
        card.pack(side="right")
        tk.Label(card, text=body, bg=theme.PAPER_DEEP, fg=TX, font=theme.font(10), justify="left",
                 padx=pad, pady=theme.px(app, 8)).pack()
        spacer = tk.Frame(app.agent_chat, bg=BG, width=1, height=1)
        app.agent_chat.insert("end", "\n", "gap")
        app.agent_chat.window_create("end", window=spacer)
        app.agent_chat.window_create("end", window=holder, pady=theme.px(app, 4))
        app.agent_chat.insert("end", "\n", "gap")
        app.agent_bubbles.append((spacer, holder))
        holder.update_idletasks()
        app.agent_fit_bubbles()
    else:
        if role in {"agent", "thought"} and getattr(app, "agent_turn_avatar", None) is None:
            canvas = _avatar_canvas(app)
            if canvas is not None:
                app.agent_chat.window_create("end", window=canvas, align="top")
                app.agent_turn_avatar = canvas
                _start_avatar(app, canvas)
        _insert_prose(app, prefix + str(text).strip(), tag)
    app.agent_chat.configure(state="disabled")
    app.agent_chat.see("end")


def suggest(app, text):
    draft = app.agent_input.get("1.0", "end-1c").strip()
    if draft and draft != text and not messagebox.askyesno(
            "替换输入框内容", "输入框里有一条未发送的消息。要替换为选中的任务吗？", parent=app):
        app.agent_input.focus_set()
        return
    app.agent_input.delete("1.0", "end")
    app.agent_input.insert("1.0", text)
    app.agent_input.focus_set()


def _open_tb_template(app, objective="", *, output=None):
    if app.busy or app.agent_running:
        return
    subject = app.loaded_subject
    if subject is None or not app.rows:
        _append_message(app, "agent", "请先在工作台读取账套，再选择这个账套的 TB 模板。")
        return
    window = tk.Toplevel(app)
    window.withdraw()
    window.transient(app)
    window.title("TB 模板")
    window.configure(bg=BG)
    body = tk.Frame(window, bg=CARD, padx=20, pady=18)
    body.pack(fill="both", expand=True, padx=12, pady=12)
    body.columnconfigure(0, weight=1)
    _label(body, "选择要填写或复核的工作簿", fg=TX, size=13, weight="bold").grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
    path = tk.StringVar(master=window, value=app.template_var.get())
    requested_period = _requested_period(objective)
    period = tk.StringVar(master=window, value=requested_period or app.period_var.get())
    unit = tk.StringVar(master=window, value=app.unit_var.get())
    entry = ttk.Entry(body, textvariable=path, width=1)
    entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))

    def browse():
        chosen = filedialog.askopenfilename(parent=window, title="选择 TB 模板",
                                            filetypes=[("Excel 工作簿", "*.xlsx *.xlsm")])
        if chosen:
            path.set(chosen)

    browse_button = ttk.Button(body, text="浏览…", command=browse)
    browse_button.grid(row=1, column=1, sticky="e")
    periods = tk.Frame(body, bg=CARD)
    periods.grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))
    _label(periods, "填列期间").pack(side="left", padx=(0, 10))
    for text, value in (("仅期初", "opening"), ("仅期末", "ending"), ("期初与期末", "both")):
        if not requested_period:
            ttk.Radiobutton(periods, text=text, value=value, variable=period,
                            style="Card.TRadiobutton").pack(side="left", padx=(0, 8))
        elif requested_period == value:
            _label(periods, text, fg=TX).pack(side="left")
    units = tk.Frame(body, bg=CARD)
    units.grid(row=3, column=0, sticky="w", pady=(8, 0))
    _label(units, "金额单位").pack(side="left", padx=(0, 10))
    ttk.Combobox(units, textvariable=unit, state="readonly", width=7,
                 values=("自动", "元", "千元", "万元", "亿元")).pack(side="left")
    status = _label(body, "", fg=theme.BAD)
    status.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    buttons = tk.Frame(body, bg=CARD)
    buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def cancel():
        window.destroy()
        if objective:
            suggest(app, objective)
            app.agent_state_var.set("等待选择 TB 模板")

    def confirm():
        if app.busy or app.loaded_subject != subject:
            status.configure(text="账套或期间已变化。请关闭后重新选择模板。")
            return
        selected = Path(path.get().strip().strip('"'))
        if selected.suffix.lower() not in {".xlsx", ".xlsm"} or not selected.is_file():
            status.configure(text="请选择存在的 .xlsx 或 .xlsm 文件。")
            entry.focus_set()
            return
        app.agent_template_subject = subject
        for variable, value in ((app.template_var, str(selected)), (app.period_var, period.get()),
                                (app.unit_var, unit.get())):
            if variable.get() != value:
                variable.set(value)
        window.destroy()
        if objective:
            start(app, objective, local_output=output)
        else:
            app.agent_input.focus_set()

    ttk.Button(buttons, text="继续任务" if objective else "应用", style="Accent.TButton",
               command=confirm).pack(side="right")
    ttk.Button(buttons, text="取消", style="CardQuiet.TButton", command=cancel).pack(side="right", padx=8)
    window.protocol("WM_DELETE_WINDOW", cancel)
    window.bind("<Escape>", lambda _event: cancel())
    theme.fit_window(window, 550, 310, fit_content=True)
    window.deiconify()
    window.grab_set()
    browse_button.focus_set()


def _hide_welcome(app):
    welcome = getattr(app, "agent_welcome", None)
    if welcome is not None:
        welcome.place_forget()


def _show_welcome(app):
    welcome = getattr(app, "agent_welcome", None)
    if welcome is not None:
        greeting = app.agent_greeting
        previous = greeting.cget("text")
        greeting.configure(text=random.choice([text for text in _GREETINGS if text != previous]))
        welcome.place(relx=0.06, rely=0.18)


def _set_gaps(app, gaps):
    if not hasattr(app, "agent_gaps"):
        return
    app.agent_gaps.configure(text="\n".join(f"· {item}" for item in gaps) or "无")


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
    respond = getattr(app, "agent_question_submit", None)
    if respond is not None:
        app.agent_input.delete("1.0", "end")
        respond(text)
        return
    if getattr(app, "agent_prompt_active", None) is not None:
        return
    _append_message(app, "me", text)
    app.agent_input.delete("1.0", "end")
    if not app.rows or app.loaded_subject is None:
        _append_message(app, "agent", "还没有读取账套。请先在工作台读取科目余额。")
        return
    pending = app.agent_paper_request
    app.agent_paper_request = None
    if pending is not None and pending[0] == app._context_revision:
        library = app.paper_library or audit_index.bundled()
        names = {value for paper in library for value in (paper.name, paper.index_no, paper.label)}
        items = re.split(r"\s*[,，、]\s*|\s*和\s*", text)
        if all(item in names for item in items):
            text = f"{pending[1]}。报表项目：{text}"
    start(app, objective=text)


_INDEX_PATTERN = re.compile(r"(?<![\d-])\d-\d{3}(?![\d])")


def _infer_report_indexes(text, library=()):
    """从任务文字里找报表项目：索引号优先，其次是内置报表项目名称；保持出现顺序、去重。"""
    content = str(text or "")
    found = list(dict.fromkeys(_INDEX_PATTERN.findall(content)))
    if found:
        return found
    named = []
    for paper in library or audit_index.bundled():
        if paper.name and paper.name in content and paper.index_no not in named:
            named.append(paper.index_no)
    return named


def _infer_request(text, library):
    """每条消息独立推断，普通询问默认走只读工具。"""
    content = text.upper()
    tb = r"(?<![A-Z0-9])TB(?![A-Z0-9])|试算(?:平衡表)?|审定表"
    negative = r"(?:暂不|不要|不需要|不用|别|不)\s*(?:做|复核|生成|导出|填写|填)?\s*"
    tb_text = re.sub(negative + f"(?:{tb})", "", content)
    writes = r"制作|生成|导出|出具|编制|填写|填列|做"
    clauses = re.split(r"[，,。；;\n]", tb_text)
    wants_tb = any(re.search(tb, clause) and re.search(writes + r"|复核|核对|另存", clause)
                   for clause in clauses)
    wants_tb = wants_tb or bool(re.search(r"填(?:写|列)?\s*(?:TB|期初|期末)", tb_text))
    if re.search(r"(?:不需要|不用|不要|别|不)\s*(?:写文件|(?:另存|导出)(?!\s*(?:整册)?底稿))", tb_text):
        wants_tb = False
    paper_text = re.sub(r"(?:暂不|不要|不用|别|不)\s*(?:制作|做|生成|导出|编制)\s*[^，,。；;\n]*底稿",
                        "", content)
    indexes = tuple(_infer_report_indexes(paper_text, library))
    wants_papers = False
    for clause in re.split(r"[，,。；;\n]", paper_text):
        listing = re.search(r"哪些|(?:可|能)(?:制作|生成|导出|做|出册)", clause)
        if not listing and re.search(writes, clause) and ("底稿" in clause or _infer_report_indexes(clause, library)):
            wants_papers = True
            break
    stages = tuple(stage for stage, requested in (("tb", wants_tb), ("papers", wants_papers)) if requested)
    return stages, indexes if wants_papers else ()


def _requested_period(text):
    if re.search(r"期初\s*(?:和|与|及|、|\+|到)?\s*期末|期末\s*(?:和|与|及|、|\+)\s*期初", text):
        return "both"
    exclusive = re.search(r"(?:仅|只)(?:需|要)?(?:填(?:写|列)?|复核|核对)?\s*(期初|期末)", text)
    if exclusive:
        return "opening" if exclusive.group(1) == "期初" else "ending"
    matches = set(re.findall(r"(?:填(?:写|列)?|复核|核对|做|制作|生成)\s*(期初|期末)", text))
    if len(matches) == 2:
        return "both"
    return "opening" if "期初" in matches else "ending" if "期末" in matches else ""


def _clear(app):
    if app.agent_running:
        return
    app.agent_events = []
    app.agent_paper_request = None
    app.agent_chat.configure(state="normal")
    app.agent_chat.delete("1.0", "end")
    app.agent_bubbles.clear()
    app.agent_turn_avatar = None
    app.agent_avatar_canvas = None
    app.agent_chat.configure(state="disabled")
    app.agent_input.delete("1.0", "end")
    app.agent_input_hint.place(x=theme.px(app, 14), y=theme.px(app, 10))
    _show_welcome(app)
    _set_gaps(app, [])
    _reset_meters(app)
    _render_scope(app)
    app.agent_state_var.set("尚未开始")


def refresh(app):
    if not hasattr(app, "agent_context_var"):
        return
    subject = getattr(app, "loaded_subject", None)
    store = getattr(app, "store", None)
    preview = getattr(app, "agent_send_preview", None)
    if preview is not None:
        if subject is not None and store is not None and store.has_key and store.data.get("ai_enabled"):
            endpoint = store.data.get("base_url", settings.DEFAULT_BASE_URL)
            model = store.data.get("model", ai.DEFAULT_MODEL)
            host = endpoint.removeprefix("https://").rstrip("/")
            preview.configure(text=f"发送到 {host} · {model}\n"
                                   "普通问答发送问题、主体与期间，以及所需的科目余额和本地知识摘要。")
            preview.grid()
        else:
            preview.grid_remove()
    previous = getattr(app, "agent_view_subject", None)
    if subject is not None:
        app.agent_view_subject = subject
    if subject is not None and previous is not None and subject != previous and not app.agent_running:
        _dismiss_prompt(app)
        app.agent_context = app.agent_result = None
        _sync_reexport(app)
        _clear(app)
    _show_artifacts(app)
    owner = getattr(app, "agent_template_subject", None)
    if subject is not None and owner is not None and subject != owner:
        app.agent_template_subject = None
        app.template_var.set("")
        app.period_var.set("both")
        app.unit_var.set("自动")
    app.agent_review_btn.configure(state="normal" if app.scan is not None and not app.busy else "disabled")
    if subject is None:
        _refresh_pending(app)
        app.agent_context_var.set("请先在「工作台」读取科目余额。")
        return
    template = Path(app.template_var.get().strip()).name if app.template_var.get().strip() else "未选模板"
    state = "已配置" if getattr(app, "store", None) and app.store.has_key and app.store.data.get("ai_enabled") else "未启用模型"
    app.agent_context_var.set(f"{getattr(subject, 'name', '')} · {subject.year}-{subject.month:02d} · "
                              f"{template} · {state}")
    _refresh_pending(app)


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
    """底稿任务不依赖 TB 模板；TB 任务才在 start 中要求模板存在。"""
    subject = app.loaded_subject
    raw = app.template_var.get().strip()
    template = Path(raw) if raw else None
    digest = fields.file_digest(template) if template and template.is_file() else ""
    return agent_module.ProjectSnapshot(
        subject=subject, rows=tuple(app.rows), subject_label=app._subject_label(),
        template_path=str(template) if template else "", unit=app.unit_var.get(),
        periods=app._periods(),
        scan=app.scan, results=tuple(dataclasses.replace(item, sources=list(item.sources)) for item in app.results),
        paper_library=tuple(app.paper_library),
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


def _manifest_lines(snapshot, stages, scope, client, config, indexes=()):
    """开始前要让用户看清的事实：发给谁、发什么、不发什么、产出什么、上限多少。"""
    counts = {
        "ledger": f"科目余额 {len(snapshot.rows)} 条",
        "tb": (f"{Path(snapshot.template_path).name} 内的识别字段、已有值与匹配结果 "
               + (f"{len(snapshot.results)} 项" if snapshot.scan is not None else "（尚未扫描）")),
        "journal": "序时账摘要与抽凭样本",
        "workpaper": "报表项目科目、客商及月度发生额摘要",
        "knowledge": "本地内置知识片段",
    }
    deliverables = "、".join({"tb": "TB 副本", "papers": "整册底稿"}.get(stage, stage) for stage in stages)
    if indexes:
        deliverables += f"（{'、'.join(indexes)}）"
    host = str(client.base_url).replace("https://", "").replace("http://", "").rstrip("/")
    lines = (
        ("发送到", f"{host} · {client.model}"),
        ("发送内容", ("已核对的任务上下文、你的回复、" if config.get("resuming") else "任务文字、") +
         "主体与期间；" + "；".join(counts[key] for key in counts if key in scope)),
        ("不发送", "完整工作簿、原始数据库、API Key、未选中的客户资料"),
        ("交付物", f"{deliverables} · 写入前选择保存位置" if deliverables else "在对话中回答"),
        ("上限", f"{config['max_model_calls']} 次模型请求 · {config['max_tool_calls']} 次工具 · 15 分钟"),
        ("写入边界", "只生成新文件，不改原模板和原数据库；报告只出草稿"),
    )
    if "tb" in stages:
        period = "、".join("期初" if item == "opening" else "期末" for item in snapshot.periods)
        lines = (*lines[:4], ("TB 设置", f"{Path(snapshot.template_path).name} · {period} · 单位{snapshot.unit}"), *lines[4:])
    if config.get("output_dir"):
        lines = (*lines[:4], ("首选保存目录", config["output_dir"]), *lines[4:])
    return lines


def start(app, objective, *, local_output=None):
    if app.busy or app.agent_running:
        _append_message(app, "sys", "当前任务尚未完成，请先停止或等待。")
        return
    if getattr(app, "store", None) is None or not app.store.has_key or not app.store.data.get("ai_enabled"):
        _append_message(app, "agent", "还没有启用模型。请先在「模型设置」输入 DeepSeek API Key 并启用。")
        return
    if not app.rows or app.loaded_subject is None:
        _append_message(app, "agent", "还没有读取账套。请先在工作台读取科目余额。")
        return
    try:
        objective, source, output = agent_paths.local_paths(objective)
        output = output or local_output
        if output is not None and (output.is_file() or output.suffix.lower() in {".xlsx", ".xlsm"}):
            raise ValueError("请在聊天中指定保存目录。文件名可在导出确认时填写。")
    except (OSError, ValueError) as exc:
        _append_message(app, "warning", str(exc))
        return
    stages, indexes = _infer_request(objective, app.paper_library)
    if "papers" in stages:
        if not indexes:
            app.agent_paper_request = (app._context_revision, objective)
            _append_message(app, "agent", "想做哪一册？请直接告诉我项目名称或索引，例如「制作货币资金底稿」。")
            app.agent_state_var.set("需要报表项目")
            return
        missing = [index for index in indexes if audit_index.find(app.paper_library, index) is None]
        if missing:
            _append_message(app, "agent", f"找不到报表项目 {'、'.join(missing)}。请核对索引后再发送。")
            app.agent_state_var.set("需要核对报表项目")
            return
    if source is not None:
        if "tb" not in stages and "papers" not in stages:
            _append_message(app, "agent", "请说明要用这个路径制作 TB、复核 TB，还是制作哪一册底稿。")
            return
        try:
            candidates = agent_paths.template_candidates(source)
        except (OSError, ValueError) as exc:
            _append_message(app, "warning", f"无法读取指定模板位置：{exc}")
            return
        if len(candidates) > 1:
            def chosen(value):
                if not value:
                    return
                selected = next((item for item in candidates if item.name.casefold() == value.casefold()), None)
                if selected is None:
                    _append_message(app, "warning", "请填写该目录内工作簿的完整文件名。")
                    prompt_question(app, question, options, chosen)
                else:
                    _start_with_template(app, objective, stages, indexes, selected, output)

            question = f"指定目录内有 {len(candidates)} 个工作簿。这次用哪一个？也可以填写完整文件名。"
            options = tuple(item.name for item in candidates[:6])
            prompt_question(app, question, options, chosen)
            return
        _start_with_template(app, objective, stages, indexes, candidates[0], output)
        return
    _start_with_template(app, objective, stages, indexes, None, output)


def _start_with_template(app, objective, stages, indexes, selected, output):
    if selected is not None:
        app.agent_template_subject = app.loaded_subject
        app.template_var.set(str(selected))
        _append_message(app, "sys", f"已选用本地模板：{selected.name}")
    template = app.template_var.get().strip()
    has_template = bool(template and Path(template).is_file())
    if "tb" in stages and not has_template:
        app.agent_state_var.set("等待选择 TB 模板")
        _open_tb_template(app, objective, output=output)
        return
    if "tb" in stages:
        period = _requested_period(objective)
        if period and period != app.period_var.get():
            app.period_var.set(period)
    scope = {"ledger", "knowledge"}
    tb_mentioned = re.search(r"\bTB\b|试算|审定表", objective, re.I)
    tb_excluded = re.search(r"(?:暂不|不要|不需要|不用|别|不)\s*(?:做|用|检查|复核|核对)?\s*"
                            r"(?:TB\b|试算|审定表|模板)", objective, re.I)
    if has_template and ("tb" in stages or (("papers" in stages or tb_mentioned) and not tb_excluded)):
        scope.add("tb")
    if "papers" in stages:
        scope.update({"journal", "workpaper"})
    try:
        snapshot = _snapshot(app)
        client = ai.DeepSeekClient.from_store(app.store)
    except (ValueError, ai.AIError, settings.SettingsError, OSError) as exc:
        _append_message(app, "agent", str(exc))
        return
    config = {"objective": objective, "stages": stages, "report_indexes": indexes,
              "max_steps": 20, "max_tool_calls": 40, "max_model_calls": 12,
              "output_dir": str(output) if output else ""}
    if not stages and scope == {"ledger", "knowledge"}:
        _launch(app, snapshot, client, stages, scope, objective, indexes, config)
        return
    app.agent_state_var.set("等待你确认开始")

    def decided(ok):
        if not ok:
            _append_message(app, "sys", "已取消；未发送任何资料。")
            app.agent_state_var.set("已取消；未发送任何资料。")
            return
        if snapshot.revision() != _current_fingerprint(app):
            _append_message(app, "warning", "账套、模板或 TB 已变化，请重新发送任务。")
            app.agent_state_var.set("来源已变化")
            return
        _launch(app, snapshot, client, stages, scope, objective, indexes, config)

    # 开始前的发送范围确认放在对话页里，不再弹窗；内容与原确认框一致。
    confirm_start(app, f"开始 {brand.AGENT_NAME} 任务", _manifest_lines(snapshot, stages, scope, client, config, indexes),
                  decided)


def _refresh_pending(app):
    if not hasattr(app, "agent_resume_btn"):
        return
    subject = getattr(app, "loaded_subject", None)
    app.agent_has_pending = bool(subject and next(agent_checkpoints.pending_for(subject), None))
    app.agent_resume_btn.configure(state="disabled" if app.busy or app.agent_running else "normal")
    app.agent_arrange()


def _pick_pending(app):
    if app.busy or app.agent_running or app.agent_prompt_active is not None or app.loaded_subject is None:
        return
    pending = list(agent_checkpoints.pending_for(app.loaded_subject))
    if not pending:
        _refresh_pending(app)
        return
    if len(pending) == 1:
        _present_pending(app, *pending[0])
        return
    options = {f"{index} · {record['config']['objective'][:50]}": (path, record)
               for index, (path, record) in enumerate(pending[:6], 1)}

    def choose(value):
        selected = options.get(value)
        if selected:
            _present_pending(app, *selected)

    prompt_question(app, "要继续哪一个待办任务？", tuple(options), choose)


def _present_pending(app, path, record):
    state = record["state"]
    app.agent_state_var.set("等待你回答 · 进度已保存")

    def answered(answer):
        if not answer:
            return
        if len(answer) > 2000:
            _append_message(app, "warning", "请将答复控制在 2000 字以内。")
            _present_pending(app, path, record)
            return
        if state["kind"] == "template":
            if answer in {"更换模板", "更换账套", "去工作台更换账套"}:
                _append_message(app, "agent", "请选择正确的账套或发送新的模板路径，再开始该任务。旧进度仍可在待办中查看。")
                if answer != "更换模板":
                    workspace.navigate(app, "tb")
                else:
                    suggest(app, record["config"]["objective"].replace("〔本地路径〕", "").strip())
                return
            if answer not in state["options"] and not re.fullmatch(
                    r"(?:确认)?借(?:用)?(?:模板)?格式[，,\s]*(?:(?:按|用)当前账套(?:继续|填(?:写|列)?(?:期初|期末)?)?)?[。.]?", answer):
                _append_message(app, "warning", "请确认是否借用格式，并按当前账套继续。")
                _present_pending(app, path, record)
                return
            requested = _requested_period(answer)
            if requested and record["identity"]["periods"] != (["opening", "ending"] if requested == "both" else [requested]):
                _append_message(app, "warning", "你的答复改变了填列期间。请带上模板路径和新期间重新发起任务。")
                return
        if state["kind"] == "budget" and not re.fullmatch(r"(?:确认)?继续(?:本次)?(?:任务)?[。.]?", answer):
            _append_message(app, "warning", "如需批准下一段模型预算，请选择「继续任务」。")
            _present_pending(app, path, record)
            return
        _resume_pending(app, path, record, answer)

    prompt_question(app, state["question"], state["options"], answered, cancel_text="稍后处理")


def _resume_pending(app, path, record, answer):
    if app.busy or app.agent_running:
        return
    identity, config = record["identity"], record["config"]
    if app.loaded_subject is None or dataclasses.asdict(app.loaded_subject) != identity["subject"]:
        _append_message(app, "warning", "请先在工作台读取该任务的账套与期间，再继续。")
        return
    try:
        if ledger_digest(app.rows) != identity["ledger_digest"]:
            raise ValueError("待办任务的账套余额已变化，请重新复核后发起任务。")
        raw_template = app.template_var.get().strip()
        if raw_template and Path(raw_template) != Path(identity["template"]):
            raise ValueError("当前选择了不同的模板，请重新选回待办任务的模板，或发起新任务。")
        if app.results and _results_digest(app.results) != record["initial_results_digest"]:
            raise ValueError("当前 TB 已重新复核，请重新发起任务以使用新结果。")
        template = identity["template"]
        if template and fields.file_digest(template) != identity["template_digest"]:
            raise ValueError("待办任务的模板内容已变化，请重新核对后发起任务。")
        client = ai.DeepSeekClient.from_store(app.store)
        app.agent_template_subject = app.loaded_subject if template else None
        periods = identity["periods"]
        for variable, value in ((app.template_var, template), (app.unit_var, identity["unit"]),
                                (app.period_var, "both" if len(periods) == 2 else periods[0])):
            if variable.get() != value:
                variable.set(value)
        snapshot = _snapshot(app)
    except (OSError, ValueError, AttributeError, ai.AIError, settings.SettingsError) as exc:
        _append_message(app, "warning", f"当前无法继续待办任务：{exc}")
        return
    stages, indexes, scope = config["stages"], config["report_indexes"], set(record["scope"])
    answer = agent_paths.without_paths(answer)
    _append_message(app, "me", answer)

    def decided(ok):
        if not ok:
            return
        if snapshot.revision() != _current_fingerprint(app):
            _append_message(app, "warning", "账套、模板或 TB 已变化。请重新打开待办核对。")
            return
        _launch(app, snapshot, client, stages, scope, config["objective"], indexes, config,
                continuation=(path, record, answer))

    preview = dict(config, resuming=True)
    confirm_start(app, "继续审计任务", _manifest_lines(snapshot, stages, scope, client, preview, indexes), decided)


def _launch(app, snapshot, client, stages, scope, objective, indexes, config, *, continuation=None):
    if app.busy or app.agent_running:
        return
    evidence_rows = {}
    evidence_gap = ""
    if "papers" in stages and indexes:
        try:
            from . import evidence_ui
            evidence_rows = {index: evidence_ui.paper_rows(app, index) for index in indexes}
        except Exception:
            evidence_gap = agent_tools.EVIDENCE_READ_GAP
    def current_fingerprint():
        # 只读普通 Python 属性；不要在运行线程里调 Tk 变量。
        try:
            disk_template = fields.file_digest(snapshot.template_path)
        except OSError:
            disk_template = ""
        return (app._context_revision, snapshot.ledger_digest, disk_template,
                snapshot.template_path, _results_digest(app.results))

    context = snapshot.context(
        objective=objective, stages=stages, report_indexes=indexes,
        output_dir=config.get("output_dir", ""),
        allowed_scope=scope, current_fingerprint=current_fingerprint,
        evidence_rows=evidence_rows,
        max_steps=config["max_steps"], max_tool_calls=config["max_tool_calls"],
        max_model_calls=config["max_model_calls"])
    if evidence_gap:
        context.add_gap(evidence_gap)
        _append_message(app, "warning", evidence_gap)
    if "tb" not in scope:
        context.template_path, context.scan, context.results = "", None, []
    app.agent_context = context
    app.agent_result = None
    app.agent_events = []
    _render_scope(app, scope)
    _reset_meters(app, context)
    app.agent_running = True
    app.agent_start_btn.configure(state="disabled")
    app.agent_stop_btn.configure(state="normal")
    _sync_reexport(app)
    _show_artifacts(app, context)
    app._set_busy(True, f"{brand.AGENT_NAME} 正在处理任务…")
    app.agent_state_var.set("运行中")

    def current_run():
        return app.agent_runtime is runtime and app._context_revision == snapshot.app_revision

    def show_event(event):
        if current_run():
            _on_event(app, event)

    run_id = continuation[1]["run_id"] if continuation else datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    checkpoint = continuation[0] if continuation else None
    checkpoint_record = continuation[1] if continuation else {}
    exports = dict(checkpoint_record.get("pending_exports", {}))
    audit_errors = []

    def audit(event, payload):
        error = _audit(app, event, {**payload, "run_id": run_id})
        if error and not audit_errors:
            audit_errors.append(error)

    def save_pause(state):
        return agent_checkpoints.save(run_id, config, context, state, target=checkpoint, exports=exports)

    def checkpoint_export(key, phase, target):
        agent_checkpoints.track_export(checkpoint, checkpoint_record, context, exports, key, phase, target)

    runtime = agent_module.AgentRuntime(
        client, agent_tools.build_registry(include_papers="papers" in stages,
                                           include_tb="tb" in scope, write_tb="tb" in stages), context,
        emit=lambda event: app._post(lambda: show_event(event)),
        approve=lambda request: _ask_approval(app, request) if current_run() else agent_module.ApprovalDecision(False),
        audit=audit,
        save_pause=save_pause,
        export_checkpoint=checkpoint_export if continuation else None,
        preflight=(lambda: agent_tools.check_template_identity(context)) if "tb" in scope and stages else None)
    app.agent_runtime = runtime
    app.agent_run_id = run_id
    app.agent_run_record = None
    app.after(1000, lambda: _tick(app, runtime))
    _start_hang_trace(app)

    def work():
        claimed = False
        try:
            if continuation:
                path, record, answer = continuation
                with agent_checkpoints.claim(path, record):
                    claimed = True
                    agent_checkpoints.restore(record, context)
                    result = runtime.run(record["state"], answer)
                    if result.status == "done":
                        try:
                            runtime.completed_run_record = _save_run_record(app, result, config, run_id)
                        except (OSError, TypeError, ValueError) as exc:
                            result = runtime._result("error", "任务产物已生成，待办保留供继续保存运行记录。", str(exc))
                        else:
                            try:
                                path.unlink(missing_ok=True)
                            except OSError:
                                pass  # 待办列表会根据已完成的运行记录隐藏此进度。
            else:
                if not stages and "ledger" in scope and agent_tools.is_company_overview(objective):
                    context.ensure_current()
                    context.ledger_overview = agent_tools.company_overview(context.rows, context.paper_library)
                result = runtime.run()
        except agent_module.StaleContextError as exc:
            result = runtime._result("stale", str(exc), str(exc))
        except (OSError, ValueError, KeyError) as exc:
            result = runtime._result("error", "无法读取待办任务进度。", str(exc))
        except Exception as exc:
            result = runtime._result("error", "本次运行失败，未完成部分见运行记录。",
                                     f"{type(exc).__name__}: {exc}"[:500])
        if continuation and not getattr(runtime, "completed_run_record", None):
            runtime.skip_run_record = not claimed
            runtime.resume_attempt_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        app._post(lambda: _finish(app, runtime, result, context, config, run_id, audit_errors))

    threading.Thread(target=work, daemon=True).start()


def stop(app):
    runtime = getattr(app, "agent_runtime", None)
    if runtime is not None:
        runtime.cancel()
        app.agent_state_var.set("正在停止；等待当前本地写入结束…")
    # 正在等确认/回答的运行线程立即放行（按拒绝处理），不再空等超时。
    for pending in list(getattr(app, "agent_pending", ())):
        pending.setdefault("decision", agent_module.ApprovalDecision(False, note="用户停止本次运行"))
        pending["event"].set()
    try:
        _dismiss_prompt(app)
    except (AttributeError, tk.TclError):
        pass
    if runtime is not None:
        try:
            app.after(10_000, lambda: _detach_if_stuck(app, runtime))
        except (AttributeError, tk.TclError):
            pass


def _detach_if_stuck(app, runtime):
    """停止 10 秒后后台步骤仍未返回（如正在读写大文件）：先解锁界面，结果到了再补报。"""
    if not getattr(app, "agent_running", False) or getattr(app, "agent_runtime", None) is not runtime:
        return
    app.agent_runtime = None
    _stop_hang_trace(app)
    app.agent_running = False
    app._set_busy(False)
    app.agent_start_btn.configure(state="normal")
    app.agent_stop_btn.configure(state="disabled")
    app.agent_state_var.set("已停止；后台步骤仍在收尾")
    _append_message(app, "warning", "已停止。当前本地步骤还没结束，结束后会在这里补报结果；可以开始新的任务。")


def _hang_trace_path():
    """源码运行时把卡顿现场写到项目 local-data（已被 .gitignore 排除），发布版不写。"""
    root = Path(__file__).resolve().parents[1]
    return root / "local-data" / "agent-hang.txt" if (root / ".git").exists() else None


def _start_hang_trace(app):
    path = _hang_trace_path()
    if path is None:
        return
    try:
        import faulthandler
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("w", encoding="utf-8")
        stream.write(f"run {getattr(app, 'agent_run_id', '')} started {datetime.now():%H:%M:%S}\n")
        stream.flush()
        faulthandler.dump_traceback_later(60, repeat=True, file=stream)
        app.agent_hang_stream = stream
    except (OSError, ValueError, RuntimeError):
        app.agent_hang_stream = None


def _stop_hang_trace(app):
    stream = getattr(app, "agent_hang_stream", None)
    if stream is None:
        return
    try:
        import faulthandler
        faulthandler.cancel_dump_traceback_later()
        stream.close()
    except (OSError, ValueError, RuntimeError):
        pass
    app.agent_hang_stream = None


def _on_event(app, event):
    app.agent_events.append(event)
    context = getattr(app, "agent_context", None)
    _update_meters(app, context)
    if event.kind == "tool_result" and context is not None:
        # 产物随写随显示，不等整次运行结束。
        _show_artifacts(app, context)
    if event.kind == "thinking":
        app.agent_state_var.set("思考中…")
        return
    if event.kind == "progress":
        app.agent_state_var.set(event.title)
        _append_message(app, "sys", event.title)
        return
    if event.kind == "run_start":
        pass  # 开始只更新状态，不在对话里留一行日志：对话从你的消息直接进回复。
    elif event.kind == "thought":
        _append_message(app, "thought", event.detail)
    elif event.kind == "tool":
        _append_message(app, "sys", event.title)
    elif event.kind == "tool_result":
        if event.data.get("ok"):
            if event.detail:
                _append_message(app, "result", event.detail[:200])
        else:
            _append_message(app, "warning", f"{event.title}：{event.detail}")
    elif event.kind in {"approval", "question"}:
        _append_message(app, "sys", f"{event.title}：{event.detail}")
    elif event.kind == "warning":
        _append_message(app, "warning", event.title + (("：" + event.detail) if event.detail else ""))
    if event.kind not in {"tool", "tool_result", "model_request", "answer", "thought"}:
        detail = str(event.detail or "")
        detail = detail if len(detail) <= 40 else detail[:40] + "…"
        app.agent_state_var.set(event.title + ((" · " + detail) if detail else ""))


def _audit(app, event, payload):
    if getattr(app, "audit", None) is None:
        return "审计日志不可用，未能留痕。"
    try:
        app.audit.append(event, **(payload or {}))
    except Exception as exc:
        return f"审计日志写入失败（{app.audit.path}）：{type(exc).__name__}: {exc}"


def _save_run_record(app, result, config, run_id):
    target = agent_checkpoints.data_dir() / "runs" / f"{run_id}.json"
    record = {"run_id": run_id, "config": config, "result": result.as_dict()}
    agent_checkpoints.write_json(target, record)
    return target


def _finish(app, runtime, result, context, config, run_id, audit_errors=()):
    paused = getattr(result, "status", None) == "needs_input"
    record, record_error = getattr(runtime, "completed_run_record", None), ""
    if not paused and record is None and not getattr(runtime, "skip_run_record", False):
        try:
            record = _save_run_record(app, result, config, getattr(runtime, "resume_attempt_id", run_id))
        except (OSError, TypeError, ValueError) as exc:
            record_error = f"运行记录保存失败（{agent_checkpoints.data_dir() / 'runs'}）：{type(exc).__name__}: {exc}"
    same_source = app._context_revision == context.snapshot.app_revision
    if app.agent_runtime is not runtime:
        if not app.agent_running and app.agent_context is context and same_source:
            app.agent_run_record = record
            app.agent_result = result
            _show_artifacts(app, context)
            _set_gaps(app, result.gaps)
            _append_message(app, "sys", f"后台步骤已结束：{result.status}")
            for error in (*audit_errors, record_error):
                if error:
                    _append_message(app, "warning", error)
            _sync_reexport(app)
        return
    _stop_hang_trace(app)
    app.agent_runtime = None
    app._set_busy(False)
    app.agent_running = False
    app.agent_start_btn.configure(state="normal")
    app.agent_stop_btn.configure(state="disabled")
    _refresh_pending(app)
    if not same_source:
        app.agent_context = app.agent_result = None
        _show_artifacts(app)
        _sync_reexport(app)
        _set_gaps(app, [])
        app.agent_state_var.set("来源已变化")
        _append_message(app, "warning", "来源已变化，请重新发送任务。")
        return
    app.agent_result = result
    app.agent_run_record = record
    _show_artifacts(app, context)
    for error in audit_errors:
        _append_message(app, "warning", error)
    _update_meters(app, context)
    _sync_reexport(app)
    gaps = list(result.gaps)
    if context.scan is not None and result.status in {"needs_input", "error", "partial", "done"}:
        remaining = agent_tools._pending(context, 1)[1]
        if remaining:
            gaps.append(f"TB 有 {remaining} 个字段待复核。")
    if not gaps and result.status in {"error", "needs_input"}:
        gaps.append("任务尚未完成全部核对。")
    _set_gaps(app, gaps)
    if paused:
        path = runtime.pending_path
        try:
            waiting = agent_checkpoints.load(path)
            _append_message(app, "sys", "任务已暂停，进度保存在本机。重新打开 Attesta 并读取同一账套后也可继续。")
            _present_pending(app, path, waiting)
        except (OSError, ValueError, KeyError) as exc:
            app.agent_state_var.set("待办进度不可读")
            _append_message(app, "warning", f"无法打开待办任务：{exc}")
        return
    if (result.status in {"done", "partial", "blocked"} and context.scan is not None
            and not getattr(runtime, "resume_attempt_id", None)):
        if context.snapshot.revision() == _current_fingerprint(app):
            app.scan = context.scan
            app.results = list(context.results)
            context.revision_at_start = _current_fingerprint(app)
            app.scan_state.set(f"{len(app.results)} 个字段 · {context.scan.unit}")
            app._refresh_tree()
    state = {"done": "已完成", "partial": "部分完成", "cancelled": "已停止", "error": "执行中断",
             "stale": "来源已变化", "blocked": "受阻"}.get(result.status, result.status)
    # 右侧只放一句状态；完整答复只在对话里出现一次，正文不整段标红。
    state_label = "底稿草稿已导出" if result.status == "done" and any(
        item.get("kind") == "workpaper" for item in result.artifacts) else state
    if "tb" in config.get("stages", ()) and any(item.get("kind") == "tb" for item in result.artifacts):
        state_label += " · TB 副本已导出"
    app.agent_state_var.set(state_label + (f" · 缺口 {len(gaps)} 项" if gaps else ""))
    if result.status != "done":
        headline = result.error or "；".join(result.gaps[:3])
        _append_message(app, "warning", f"{state}" + (f"：{headline}" if headline else ""))
    summary = str(result.summary or "").strip()
    for prefix in ("部分完成：", "部分完成。"):
        if summary.startswith(prefix):
            summary = summary[len(prefix):].lstrip()
    if summary:
        _append_message(app, "agent", summary)
    if record:
        _append_message(app, "sys", f"已保存运行记录：{record}")
    elif record_error:
        _append_message(app, "warning", record_error)
    try:
        app.agent_input.focus_set()
    except tk.TclError:
        pass


def _ask_approval(app, request):
    pending = {"event": threading.Event(), "decision": agent_module.ApprovalDecision(False)}
    _track(app, pending)

    def done(decision):
        pending["decision"] = decision
        pending["event"].set()

    def show():
        if pending["event"].is_set():
            return
        try:
            app.agent_state_var.set("等待你确认写入")
            _append_message(app, "sys", f"等待你确认写入：{request.summary}")
            prompt_approval(app, request, done)
        except Exception as exc:  # 面板都画不出来时如实拒绝，不让运行线程空等
            note = f"确认面板无法显示：{type(exc).__name__}: {exc}"[:300]
            _append_message(app, "warning", note)
            done(agent_module.ApprovalDecision(False, note=note))

    app._post(show)
    try:
        if not pending["event"].wait(600):
            app._post(lambda: (_dismiss_prompt(app), _on_event(app, agent_module.AgentEvent(
                0, datetime.now().astimezone().isoformat(timespec="seconds"),
                "warning", "确认超时", "未在 10 分钟内确认，已拒绝写入"))))
            return agent_module.ApprovalDecision(False, note="确认超时")
        return pending["decision"]
    finally:
        _untrack(app, pending)


# ---------- 对话页内的确认/提问面板 ----------

def _enqueue_prompt(app, render):
    """同一时间只显示一个确认；后来的排队，前一个处理完再显示。"""
    if getattr(app, "agent_prompt_active", None) is None:
        app.agent_prompt_active = render
        render()
    else:
        app.agent_prompt_queue.append(render)


def _close_prompt(app):
    panel = app.agent_prompt
    for child in panel.winfo_children():
        child.destroy()
    panel.grid_remove()
    app.agent_prompt_active = None
    app.agent_question_submit = None
    if app.agent_prompt_queue:
        render = app.agent_prompt_queue.pop(0)
        app.agent_prompt_active = render
        render()


def _dismiss_prompt(app):
    """停止或超时：收起面板并丢弃排队的确认（等待方已按拒绝放行）。"""
    if not hasattr(app, "agent_prompt"):
        return
    app.agent_prompt_queue = []
    _close_prompt(app)


def _open_prompt(app):
    """显示面板并把注意力带过来：切到 Agent 页、窗口置前、提示音。"""
    panel = app.agent_prompt
    for child in panel.winfo_children():
        child.destroy()
    panel.grid()
    arrange = getattr(app, "agent_arrange", None)
    if arrange is not None:
        arrange(show_chat=True)
    try:
        if getattr(app, "active_page", "agent") != "agent":
            workspace.navigate(app, "agent")
        app.deiconify()
        app.lift()
        app.bell()
    except tk.TclError:
        pass
    if arrange is None:
        body = tk.Frame(panel, bg=CARD)
        body.pack(fill="x", padx=16, pady=12)
        return body

    area = layout.scroll_host(panel, bg=CARD)
    area.canvas.configure(width=1, height=1)
    area.pack(fill="x", padx=16, pady=12)
    outer = panel.master

    def fit(_event=None):
        limit = min(theme.px(app, 360), max(theme.px(app, 120), int(outer.winfo_height() * 0.54)))
        area.canvas.configure(height=min(limit, area.body.winfo_reqheight()))

    def wrap(event):
        for child in area.body.winfo_children():
            columns = child.winfo_children() if child.winfo_class() == "Frame" else (child,)
            for widget in columns:
                if widget.winfo_class() == "Label" and int(widget.cget("wraplength")):
                    offset = 120 if widget.winfo_manager() == "grid" else 0
                    widget.configure(wraplength=max(50, event.width - theme.px(app, offset)))

    area.body.bind("<Configure>", fit, add="+")
    area.canvas.bind("<Configure>", wrap, add="+")
    on_resize = outer.bind("<Configure>", fit, add="+")
    area.bind("<Destroy>", lambda event: outer.unbind("<Configure>", on_resize)
              if event.widget is area else None, add="+")
    area.after_idle(fit)
    return area.body


def prompt_approval(app, request, on_done):
    """在对话页里请求写入确认；结果通过 on_done(ApprovalDecision) 返回。"""

    def render():
        body = _open_prompt(app)
        path_var = tk.StringVar(master=app, value=request.default_path or "")
        auto_var = tk.BooleanVar(master=app, value=False)
        _label(body, request.title, fg=TX, size=11, weight="bold").pack(anchor="w")
        _label(body, request.summary, fg=SEC, wrap=theme.px(app, 640)).pack(anchor="w", pady=(4, 6))
        for key, value in request.details or ():
            if key in {"工具", "默认输出"}:
                continue
            _label(body, f"{key}：{value}", fg=SEC, wrap=theme.px(app, 640)).pack(anchor="w", pady=1)
        if request.kind == "write":
            row = tk.Frame(body, bg=CARD)
            row.pack(fill="x", pady=(10, 0))
            row.columnconfigure(1, weight=1)
            _label(row, "保存到").grid(row=0, column=0, sticky="w")
            ttk.Entry(row, textvariable=path_var).grid(row=0, column=1, sticky="ew", padx=6)

            def choose():
                current = Path(path_var.get().strip()) if path_var.get().strip() else None
                path = filedialog.asksaveasfilename(
                    parent=app, title="选择输出文件",
                    initialdir=str(current.parent) if current else None,
                    initialfile=current.name if current else None,
                    defaultextension=current.suffix if current else ".xlsx",
                    filetypes=[("Excel 工作簿", "*.xlsx *.xlsm"), ("所有文件", "*.*")])
                if path:
                    path_var.set(path)

            ttk.Button(row, text="选择…", command=choose).grid(row=0, column=2)
            _label(body, "此位置不可写时会尝试本账号的备用目录，最后尝试临时目录。",
                   fg=SEC, wrap=theme.px(app, 450)).pack(anchor="w", pady=(5, 0))
            if request.allow_auto:
                ttk.Checkbutton(body, text="本次运行同类写入不再询问", variable=auto_var,
                                style="Card.TCheckbutton").pack(anchor="w", pady=(8, 0))
        buttons = tk.Frame(body, bg=CARD)
        buttons.pack(fill="x", pady=(12, 0))

        def finish(decision):
            _close_prompt(app)
            on_done(decision)

        def approve():
            path = path_var.get().strip()
            if request.kind == "write" and not path:
                choose()
                path = path_var.get().strip()
                if not path:
                    return
            finish(agent_module.ApprovalDecision(True, path, auto_var.get()))

        allow = ttk.Button(buttons, text="允许并继续", style="Accent.TButton", command=approve)
        allow.pack(side="right")
        ttk.Button(buttons, text="拒绝",
                   command=lambda: finish(agent_module.ApprovalDecision(False, note="用户拒绝"))).pack(
            side="right", padx=8)

        def halt():
            finish(agent_module.ApprovalDecision(False, note="用户停止本次运行"))
            try:
                stop(app)
            except Exception:
                pass

        ttk.Button(buttons, text="停止本次运行", style="CardQuiet.TButton", command=halt).pack(side="left")
        allow.focus_set()

    _enqueue_prompt(app, render)


def confirm_start(app, title, lines, on_done):
    """开始前的确认：在对话页里列出事实，「开始」或「取消」。"""

    def render():
        body = _open_prompt(app)
        _label(body, title, fg=TX, size=11, weight="bold").pack(anchor="w")
        grid = tk.Frame(body, bg=CARD)
        grid.pack(fill="x", pady=(8, 0))
        grid.columnconfigure(1, weight=1)
        for row, (key, value) in enumerate(lines):
            _label(grid, key, fg=SEC).grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=1)
            _label(grid, value, fg=TX, wrap=theme.px(app, 560)).grid(row=row, column=1, sticky="w", pady=1)
        buttons = tk.Frame(body, bg=CARD)
        buttons.pack(fill="x", pady=(12, 0))

        def finish(value):
            _close_prompt(app)
            on_done(value)

        go = ttk.Button(buttons, text="开始", style="Accent.TButton", command=lambda: finish(True))
        go.pack(side="right")
        ttk.Button(buttons, text="取消", style="CardQuiet.TButton",
                   command=lambda: finish(False)).pack(side="right", padx=8)
        go.focus_set()

    _enqueue_prompt(app, render)


def prompt_question(app, question, options, on_done, *, cancel_text="取消"):
    """在对话页里回答 Agent 的提问；取消返回 None。"""

    def render():
        body = _open_prompt(app)
        answer = tk.StringVar(master=app, value="")
        _label(body, question, fg=TX, size=10, weight="bold", wrap=theme.px(app, 640)).pack(anchor="w")

        def finish(value):
            _close_prompt(app)
            on_done(value)

        def submit(value):
            if value.strip():
                finish(value.strip())

        if options:
            row = tk.Frame(body, bg=CARD)
            row.pack(fill="x", pady=(8, 0))
            for index, option in enumerate(options[:6], 1):
                long = len(option) > 34
                ttk.Button(row, text=f"选项 {index}" if long else option,
                           command=lambda value=option: finish(value)).pack(anchor="w", pady=2)
                if long:
                    _label(row, option, wrap=theme.px(app, 420)).pack(fill="x", pady=(0, 2))
        line = tk.Frame(body, bg=CARD)
        line.pack(fill="x", pady=(8, 0))
        entry = ttk.Entry(line, textvariable=answer)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event: submit(answer.get()))
        ttk.Button(line, text="提交", style="Accent.TButton",
                   command=lambda: submit(answer.get())).pack(side="left", padx=(6, 0))
        ttk.Button(line, text=cancel_text, command=lambda: finish(None)).pack(side="left", padx=(6, 0))
        app.agent_question_submit = submit
        entry.focus_set()

    _enqueue_prompt(app, render)


def _track(app, pending):
    if not hasattr(app, "agent_pending"):
        app.agent_pending = []
    app.agent_pending.append(pending)


def _untrack(app, pending):
    try:
        app.agent_pending.remove(pending)
    except (AttributeError, ValueError):
        pass


def _selected_artifact(app):
    selected = app.agent_artifacts.selection()
    artifacts = app.agent_project_artifacts.get(app.loaded_subject, {})
    if not selected:
        return next(reversed(artifacts.values()), None)
    return artifacts.get(selected[0])


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
