"""Attesta workspace pages.

One rule for every page: show the state, the next action, and the result.
No slogans, no duplicated headings, no explanatory paragraphs; amounts stay local.
"""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from . import APP_NAME, __version__, brand, dialogs as messagebox, theme, workflow

BG, INK, MUTED = theme.PAPER, theme.INK, theme.MUTED
NAV, ACCENT = theme.RAIL, theme.ACCENT
PAGES = {"overview": "工作台", "agent": "审计 Agent", "tb": "账套与 TB",
         "papers": "审计底稿", "notes": "财务报表附注", "report": "报告中心",
         "security": "设置"}


class ScrollArea(tk.Frame):
    """竖向滚动容器：内容高于可视区时可滚动，避免小屏把下文裁掉。"""

    def __init__(self, parent, *, bg=BG, height=260):
        super().__init__(parent, bg=bg)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, height=height)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scroll.grid(row=0, column=1, sticky="ns")
        self.scroll.grid_remove()
        self.canvas.configure(yscrollcommand=self._sync_scroll)
        self.body = tk.Frame(self.canvas, bg=bg)
        self.body.columnconfigure(0, weight=1)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", self._wheel_on, add="+")
        self.canvas.bind("<Leave>", self._wheel_off, add="+")

    def set_height(self, height):
        self.canvas.configure(height=max(80, int(height)))

    def fit(self, available, *, ratio=0.62, low=150, high=520):
        """按内容与可用高度分配表单区高度：放得下就完整显示，否则滚动。

        low/high 是设计稿像素，按当前 DPI 折算。表单是主输入区，优先给足高度，
        但始终为结果表和底部操作栏保留约 320 设计像素，避免「读取科目余额」
        或「另存 Excel」被挤出可视区。
        """
        needed = self.body.winfo_reqheight()
        low, high = theme.px(self, low), theme.px(self, high)
        if available < theme.px(self, 560):
            ratio = min(ratio, 0.5)
        target = min(needed, available * ratio, max(low, available - theme.px(self, 320)))
        self.set_height(min(high, max(low, target)))

    def _sync_scroll(self, first, last):
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.scroll.grid_remove()
        else:
            self.scroll.grid()
        self.scroll.set(first, last)

    def _fit_width(self, width=None):
        width = width or self.canvas.winfo_width()
        if width > 1:
            self.canvas.itemconfigure(self._window, width=width)

    def _on_body(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._fit_width()

    def _on_canvas(self, event):
        self._fit_width(event.width)

    def _wheel_on(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._wheel)

    def _wheel_off(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")

    def _wheel(self, event):
        if self.body.winfo_reqheight() <= self.canvas.winfo_height():
            return
        self.canvas.yview_scroll(-1 * int(event.delta / 120 or -1), "units")


def scroll_page(app, key):
    """静态页面统一放进滚动容器，返回可直接放置内容的 body。"""
    holder = app.pages[key]
    holder.rowconfigure(0, weight=1)
    holder.columnconfigure(0, weight=1)
    area = ScrollArea(holder, bg=BG, height=520)
    area.grid(row=0, column=0, sticky="nsew")
    app.page_scrolls[key] = area
    return area.body


class NavItem(tk.Frame):
    """导航项：纯文字 + 左侧细线，选中时用品牌橙标记。"""

    def __init__(self, parent, title, command):
        super().__init__(parent, bg=theme.RAIL, height=38)
        self.pack_propagate(False)
        self._title, self._command = title, command
        self._active, self._hover = False, False
        self.bar = tk.Frame(self, bg=theme.RAIL, width=2)
        self.bar.pack(side="left", fill="y", pady=6)
        self.text = tk.Label(self, text=title, bg=theme.RAIL, fg=theme.INK_SOFT,
                             font=(theme.FAMILY["body"], 10), anchor="w", padx=12)
        self.text.pack(side="left", fill="both", expand=True)
        for widget in (self, self.text, self.bar):
            widget.bind("<Button-1>", lambda _event: self._command())
            widget.bind("<Enter>", self._enter, add="+")
            widget.bind("<Leave>", self._leave, add="+")
            widget.configure(cursor="hand2", takefocus=1)

    def _enter(self, _event=None):
        self._hover = True
        self._paint()

    def _leave(self, _event=None):
        self._hover = False
        self._paint()

    def set_active(self, active):
        self._active = bool(active)
        self._paint()

    def _paint(self):
        back = theme.RAIL_ACTIVE if self._active else (theme.RAIL_HOVER if self._hover else theme.RAIL)
        self.configure(bg=back)
        self.text.configure(bg=back, fg=theme.ACCENT if self._active else theme.INK_SOFT,
                            font=(theme.FAMILY["body"], 10, "bold" if self._active else "normal"))
        self.bar.configure(bg=theme.ACCENT if self._active else back)


def build_shell(app):
    """品牌栏 + 内容区。导航只有六个词，品牌只有一个名字。"""
    theme.install(app)
    app.columnconfigure(1, weight=1)
    app.rowconfigure(0, weight=1)
    # 导航宽度随 DPI 折算；分隔线用 1px 网格列，不再用绝对坐标 place(207)，
    # 否则缩放后线会脱离导航边缘。
    rail_width = theme.px(app, 208)
    sidebar = tk.Frame(app, bg=NAV, width=rail_width)
    sidebar.grid(row=0, column=0, sticky="ns")
    sidebar.pack_propagate(False)
    tk.Frame(app, bg=theme.HAIRLINE, width=1).grid(row=0, column=0, sticky="nse")

    brand_row = tk.Frame(sidebar, bg=NAV)
    brand_row.pack(fill="x", padx=20, pady=(24, 22))
    tk.Label(brand_row, image=app.brand_mark, bg=NAV, bd=0).pack(side="left")
    tk.Label(brand_row, text=APP_NAME, bg=NAV, fg=theme.INK,
             font=(theme.FAMILY["latin"], 19, "bold")).pack(side="left", padx=(9, 0))

    app.nav_buttons = {}
    app.nav_rows = {}
    app.active_page = "overview"
    for key, title in PAGES.items():
        item = NavItem(sidebar, title, lambda key=key: navigate(app, key))
        item.pack(fill="x", padx=10, pady=1)
        app.nav_buttons[key] = item
        app.nav_rows[key] = item

    footer = tk.Frame(sidebar, bg=NAV)
    footer.pack(side="bottom", fill="x", padx=22, pady=18)
    tk.Label(footer, text=f"v{__version__} · {brand.COPYRIGHT}", bg=NAV, fg=theme.MUTED,
             font=(theme.FAMILY["mono"], 8)).pack(anchor="w")

    app.content = tk.Frame(app, bg=BG)
    app.content.grid(row=0, column=1, sticky="nsew")
    app.content.columnconfigure(0, weight=1)
    app.content.rowconfigure(1, weight=1)
    app.page_title = tk.StringVar(value=PAGES["overview"])
    app.page_context = tk.StringVar(value="尚未读取账套")
    host = tk.Frame(app.content, bg=BG)
    host.grid(row=1, column=0, sticky="nsew")
    host.columnconfigure(0, weight=1)
    host.rowconfigure(0, weight=1)
    app.pages = {key: tk.Frame(host, bg=BG) for key in PAGES}
    for page in app.pages.values():
        page.grid(row=0, column=0, sticky="nsew")
        page.columnconfigure(0, weight=1)
    app.workspace = app.pages["tb"]
    app.workspace.columnconfigure(0, weight=1)
    app.workspace.rowconfigure(1, weight=1, minsize=theme.px(app, 120))
    app.page_scrolls = {}


def build_pages(app):
    app.review_status = tk.StringVar(value="")
    app.overview_entity = tk.StringVar(value="尚未读取账套")
    app.overview_meta = tk.StringVar(value="")
    app.primary_action = tk.StringVar(value="读取账套")
    _build_overview(app)
    from . import agent_ui
    agent_ui.build(app)
    _build_papers(app)
    _build_notes(app)
    _build_report(app)
    _build_settings(app)
    refresh_context(app)


def _build_overview(app):
    page = scroll_page(app, "overview")
    page.columnconfigure(0, weight=1)

    top = tk.Frame(page, bg=BG)
    top.grid(row=0, column=0, sticky="ew", padx=30, pady=(24, 0))
    top.columnconfigure(0, weight=1)
    head = theme.RoundCard(top)
    head.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
    surface = theme.SURFACE
    tk.Label(head.body, text="当前审计项目", bg=surface, fg=MUTED,
             font=theme.font(9, weight="bold")).pack(anchor="w")
    entity = tk.Label(head.body, textvariable=app.overview_entity, bg=surface, fg=INK,
                      font=theme.font(21, weight="bold"), anchor="w", justify="left", wraplength=400)
    entity.pack(anchor="w", pady=(10, 4))
    meta = tk.Label(head.body, textvariable=app.overview_meta, bg=surface, fg=MUTED,
                    font=theme.mono(), anchor="w", justify="left", wraplength=400)
    meta.pack(anchor="w")
    head.body.bind("<Configure>", lambda event: (entity.configure(wraplength=event.width),
                                                   meta.configure(wraplength=event.width)))

    signers = theme.RoundCard(top)
    signers.grid(row=0, column=1, sticky="nsew")
    tk.Label(signers.body, text="底稿人员", bg=surface, fg=INK,
             font=theme.font(11, weight="bold")).grid(row=0, column=0, columnspan=2, sticky="w")
    for row, (title, variable) in enumerate((("编制人", app.prepared_by_var),
                                              ("复核人", app.reviewed_by_var)), 1):
        tk.Label(signers.body, text=title, bg=surface, fg=MUTED).grid(row=row, column=0, sticky="w", pady=(8, 0))
        entry = ttk.Entry(signers.body, textvariable=variable, width=18)
        entry.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=(8, 0))
        entry.bind("<Return>", app._save_signers)
        entry.bind("<FocusOut>", app._save_signers)
    tk.Label(signers.body, text="自动保存到本机，导出底稿时带入。", bg=surface, fg=MUTED,
             font=theme.font(8)).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

    facts = tk.Frame(page, bg=BG)
    facts.grid(row=1, column=0, sticky="ew", padx=30, pady=(34, 0))
    for col in range(3):
        facts.columnconfigure(col, weight=0, uniform="stat")
    facts.columnconfigure(3, weight=1)
    app.stat_tiles = {}
    for col, (key, title) in enumerate((("accounts", "已载入科目"), ("pending", "待确认"),
                                        ("differences", "差异"))):
        holder = tk.Frame(facts, bg=BG)
        holder.grid(row=0, column=col, sticky="w", padx=(0 if col == 0 else 40, 0))
        tile = theme.StatTile(holder, title, "—")
        tile.pack(anchor="w")
        app.stat_tiles[key] = tile
    ttk.Button(facts, textvariable=app.primary_action, style="Accent.TButton",
               command=lambda: navigate(app, "tb")).grid(row=0, column=4, sticky="e", pady=(6, 0))

    tk.Frame(page, bg=theme.HAIRLINE, height=1).grid(row=2, column=0, sticky="ew", padx=30, pady=(30, 0))
    page.rowconfigure(3, weight=1)
    theme.reveal([top, facts], delay=20, stagger=40)


def _build_papers(app):
    """底稿：两个动作和当前待复核文件，没有流程图。"""
    page = scroll_page(app, "papers")
    actions = tk.Frame(page, bg=BG)
    actions.grid(row=0, column=0, sticky="ew", padx=30, pady=(34, 0))
    ttk.Button(actions, text="制作报表项目底稿", style="Accent.TButton",
               command=app._draft_paper).pack(side="left")
    from . import evidence_ui
    ttk.Button(actions, text="项目资料与自动底稿",
               command=lambda: evidence_ui.open_project(app)).pack(side="left", padx=6)
    ttk.Button(actions, text="选择待复核底稿", style="Quiet.TButton",
               command=lambda: app._attach_review_document("底稿")).pack(side="left", padx=(8, 0))
    ttk.Button(actions, text="复核底稿文字", style="Quiet.TButton",
               command=lambda: app._agent_review("底稿")).pack(side="left", padx=(2, 0))
    status = tk.Label(page, textvariable=app.review_status, bg=BG, fg=theme.MUTED,
                      font=(theme.FAMILY["body"], 9), anchor="w", justify="left",
                      wraplength=820)
    status.grid(row=1, column=0, sticky="ew", padx=30, pady=(14, 0))
    theme.wrap_to_parent(status, padding=60)
    page.rowconfigure(2, weight=1)
    app.intake_status = tk.StringVar(master=app, value="读取账套后建立公司年度资料夹。")
    intake = ttk.Label(page, textvariable=app.intake_status, wraplength=820)
    intake.grid(row=2, column=0, sticky="nw", padx=30)
    theme.wrap_to_parent(intake, padding=60)


def _build_notes(app):
    page = scroll_page(app, "notes")
    actions = tk.Frame(page, bg=BG)
    actions.grid(row=0, column=0, sticky="ew", padx=30, pady=(34, 0))
    ttk.Button(actions, text="选择待复核附注", style="Accent.TButton",
               command=lambda: app._attach_review_document("附注")).pack(side="left")
    ttk.Button(actions, text="按知识依据复核", style="Quiet.TButton",
               command=lambda: app._agent_review("附注")).pack(side="left", padx=(8, 0))
    status = tk.Label(page, textvariable=app.review_status, bg=BG, fg=theme.MUTED,
                      font=(theme.FAMILY["body"], 9), anchor="w", justify="left",
                      wraplength=820)
    status.grid(row=1, column=0, sticky="ew", padx=30, pady=(14, 0))
    theme.wrap_to_parent(status, padding=60)
    page.rowconfigure(2, weight=1)


def _build_report(app):
    page = app.pages["report"]
    card = theme.RoundCard(page, pad=0)
    card.grid(row=0, column=0, sticky="new", padx=30, pady=(30, 0))
    body = tk.Frame(card.body, bg=theme.SURFACE)
    body.pack(fill="both", expand=True, padx=12, pady=12)
    body.columnconfigure(0, weight=1)
    app.readiness_tree = ttk.Treeview(body, columns=("item", "status", "detail"),
                                      show="headings", height=10)
    for key, title, width in (("item", "审计环节", 175), ("status", "当前状态", 190),
                              ("detail", "依据与缺口", 600)):
        app.readiness_tree.heading(key, text=title)
        app.readiness_tree.column(key, width=width, minwidth=80, stretch=True)
    theme.fit_columns(app.readiness_tree, {"item": 175, "status": 190, "detail": 600})
    app.readiness_tree.grid(row=0, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(body, orient="vertical", command=app.readiness_tree.yview)
    scroll.grid(row=0, column=1, sticky="ns", padx=(6, 0))
    app.readiness_tree.configure(yscrollcommand=scroll.set)
    app.readiness_tree.tag_configure("odd", background=theme.SURFACE_ALT)
    app.readiness_tree.bind("<Double-1>", lambda _e: _show_gap(app))
    actions = tk.Frame(page, bg=BG)
    actions.grid(row=1, column=0, sticky="ew", padx=30, pady=(14, 0))
    ttk.Button(actions, text="导出准备清单", style="Accent.TButton",
               command=lambda: _export_readiness(app)).pack(side="left")
    ttk.Button(actions, text="按知识依据复核", style="Quiet.TButton",
               command=lambda: app._agent_review("报告")).pack(side="left", padx=(8, 0))
    status = tk.Label(page, textvariable=app.review_status, bg=BG, fg=theme.MUTED,
                      font=(theme.FAMILY["body"], 9), anchor="w", justify="left",
                      wraplength=820)
    status.grid(row=2, column=0, sticky="ew", padx=30, pady=(14, 0))
    theme.wrap_to_parent(status, padding=60)
    page.rowconfigure(3, weight=1)


def _build_settings(app):
    page = scroll_page(app, "security")
    box = tk.Frame(page, bg=BG)
    box.grid(row=0, column=0, sticky="ew", padx=30, pady=(34, 0))
    tk.Label(box, text="模型", bg=BG, fg=theme.MUTED,
             font=(theme.FAMILY["body"], 9)).pack(anchor="w")
    tk.Label(box, textvariable=app.ai_state, bg=BG, fg=theme.INK,
             font=(theme.FAMILY["body"], 11)).pack(anchor="w", pady=(2, 12))
    actions = tk.Frame(box, bg=BG)
    actions.pack(anchor="w")
    ttk.Button(actions, text="模型设置", style="Accent.TButton",
               command=app._open_ai_settings).pack(side="left")
    ttk.Button(actions, text="校验日志", style="Quiet.TButton",
               command=app._verify_audit).pack(side="left", padx=(8, 0))
    ttk.Button(actions, text="运行日志", style="Quiet.TButton",
               command=app._open_log).pack(side="left", padx=(2, 0))
    tk.Frame(page, bg=theme.HAIRLINE, height=1).grid(row=1, column=0, sticky="ew", padx=30, pady=(30, 0))
    tk.Label(page, text=brand.FOOTER_TEXT, bg=BG, fg=theme.MUTED,
             font=(theme.FAMILY["mono"], 8)).grid(row=2, column=0, sticky="w", padx=30, pady=(14, 0))
    page.rowconfigure(3, weight=1)


def _state(app):
    return workflow.readiness(app.rows, app.results, app.loaded_subject, app.scan, app.knowledge_refs)


def _refresh_report(app):
    for iid in app.readiness_tree.get_children():
        app.readiness_tree.delete(iid)
    for index, item in enumerate(_state(app)["checks"]):
        app.readiness_tree.insert("", "end", iid=str(index),
                                  values=(item["item"], item["status"], item["detail"]),
                                  tags=("odd",) if index % 2 else ())


def _show_gap(app):
    selected = app.readiness_tree.selection()
    if selected:
        item = _state(app)["checks"][int(selected[0])]
        messagebox.showinfo(item["item"], item["status"] + "\n\n" + item["detail"], parent=app)


def _export_readiness(app):
    if app.busy:
        return
    path = filedialog.asksaveasfilename(title="导出准备清单", defaultextension=".html",
                                      filetypes=[("准备清单", "*.html")],
                                      initialfile=f"{APP_NAME}-准备清单.html")
    if not path:
        return
    state = _state(app)
    try:
        output = workflow.export_readiness(path, state)
    except (OSError, ValueError) as exc:
        messagebox.showerror("无法导出", str(exc), parent=app)
        return
    app._audit("export_readiness", output=str(output),
               source_digest=state["source_digest"], ready_to_issue=False)
    messagebox.showinfo("准备清单已导出", str(output), parent=app)


def navigate(app, key):
    app.pages[key].tkraise()
    app.active_page = key
    app.page_title.set(PAGES[key])
    for name, button in app.nav_buttons.items():
        button.set_active(name == key)
    refresh_context(app)
    if key == "report":
        _refresh_report(app)
    children = [child for child in app.pages[key].winfo_children() if isinstance(child, tk.Frame)]
    if children:
        theme.reveal(children[:2], delay=0, stagger=30)


def context_meta(subject, template="") -> str:
    """首页第二行只放可核对的数据：期间、账套、模板文件。"""
    if subject is None:
        return ""
    parts = []
    year, month = getattr(subject, "year", None), getattr(subject, "month", None)
    if year and month:
        parts.append(f"{year}-{month:02d}")
    ledger = str(getattr(subject, "ledger_id", "") or "").strip()
    if ledger:
        parts.append(f"账套 {ledger}")
    name = Path(str(template)).name if str(template).strip() else ""
    if name:
        parts.append(name)
    return " · ".join(parts)


def refresh_context(app):
    if not hasattr(app, "review_status"):
        return
    pending = sum(result.status not in {"matched", "protected"} for result in app.results)
    differences = sum(result.difference not in (None, 0) for result in app.results)
    subject = app._subject_label()
    app.page_context.set(subject if subject else "尚未读取账套")
    if hasattr(app, "overview_entity"):
        app.overview_entity.set(app.loaded_subject.name if app.loaded_subject else "尚未读取账套")
    meta = getattr(app, "overview_meta", None)
    if meta is not None:
        meta.set(context_meta(app.loaded_subject, app.template_var.get()))
    action = getattr(app, "primary_action", None)
    if action is not None:
        if not app.rows:
            action.set("读取账套")
        elif app.scan is None:
            action.set("选择模板")
        elif pending:
            action.set(f"确认 {pending} 项")
        elif differences:
            action.set(f"核对 {differences} 处差异")
        else:
            action.set("查看结果")
    document = app.review_document
    app.review_status.set(
        (f"{document['role']}：{document['name']} · 最多发送 12000 字符"
         + ("（内容已截取）" if document["truncated"] else "")) if document else "")
    tiles = getattr(app, "stat_tiles", None)
    if tiles:
        accounts = len({row.code for row in app.rows if getattr(row, "code", "")})
        tiles["accounts"].update_value(accounts)
        tiles["pending"].update_value(pending, tone=theme.WARN if pending else theme.INK)
        tiles["differences"].update_value(differences, tone=theme.BAD if differences else theme.INK)
    if hasattr(app, "agent_context_var"):
        from . import agent_ui
        agent_ui.refresh(app)
