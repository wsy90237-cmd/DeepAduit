"""Attesta workspace pages.

One rule for every page: show the state, the next action, and the result.
No slogans, no duplicated headings, no explanatory paragraphs; amounts stay local.
"""

import random
import tkinter as tk
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import filedialog, ttk

from . import APP_NAME, __version__, brand, dialogs as messagebox, theme, workflow

BG, INK, MUTED = theme.PAPER, theme.INK, theme.MUTED
NAV, ACCENT = theme.RAIL, theme.ACCENT
PAGES = {"tb": "工作台", "agent": "审计对话", "papers": "底稿",
         "notes": "附注", "report": "报告", "security": "设置"}

HOME_GREETINGS = (
    f"欢迎使用 {APP_NAME}",
    "你好，开始本期审计",
    "今天，从账套开始",
    "很高兴见到你",
    "欢迎来到审计工作台",
)


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


class NavItem(tk.Canvas):
    """导航项：线性图标 + 文字；选中与悬停用圆角浅底，不用侧边竖线。"""

    def __init__(self, parent, key, title, command):
        super().__init__(parent, bg=theme.RAIL, height=theme.px(parent, 40), bd=0,
                         highlightthickness=0, cursor="hand2", takefocus=1)
        self.key, self._title, self._command = key, title, command
        self._active, self._hover, self._badge = False, False, ""
        self.bind("<Configure>", lambda _event: self._paint())
        self.bind("<Button-1>", lambda _event: self._command())
        self.bind("<Return>", lambda _event: self._command())
        self.bind("<space>", lambda _event: self._command())
        self.bind("<FocusIn>", lambda _event: self._paint())
        self.bind("<FocusOut>", lambda _event: self._paint())
        self.bind("<Enter>", self._enter, add="+")
        self.bind("<Leave>", self._leave, add="+")

    def _enter(self, _event=None):
        self._hover = True
        self._paint()

    def _leave(self, _event=None):
        self._hover = False
        self._paint()

    def set_active(self, active):
        self._active = bool(active)
        self._paint()

    def set_badge(self, text=""):
        self._badge = str(text or "")
        self._paint()

    def _paint(self):
        self.delete("all")
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        if self._active or self._hover:
            theme.rounded_rect(self, 0, 1, width, height - 1, theme.px(self, 8),
                               fill=theme.RAIL_ACTIVE if self._active else theme.RAIL_HOVER, outline="")
        ink = theme.ACCENT_DARK if self._active else theme.INK_SOFT
        size = theme.px(self, 18)
        theme.draw_icon(self, self.key, theme.px(self, 12), (height - size) / 2, size, ink)
        self.create_text(theme.px(self, 40), height / 2, text=self._title, anchor="w", fill=ink,
                         font=(theme.FAMILY["body"], 10, "bold" if self._active else "normal"))
        if self.focus_get() is self:
            self.create_rectangle(2, 3, width - 3, height - 4, outline=theme.ACCENT_DARK, dash=(2, 2))
        if self._badge:
            self.create_text(width - theme.px(self, 12), height / 2, text=self._badge, anchor="e",
                             fill=theme.WARN, font=(theme.FAMILY["mono"], 8))


def build_shell(app):
    """品牌栏 + 内容区。导航只有六个词，品牌只有一个名字。"""
    theme.install(app)
    app.columnconfigure(1, weight=1)
    app.rowconfigure(0, weight=1)
    # 导航宽度随 DPI 折算；分隔线用 1px 网格列，不再用绝对坐标 place(207)，
    # 否则缩放后线会脱离导航边缘。
    rail_width = theme.px(app, 216)
    sidebar = tk.Frame(app, bg=NAV, width=rail_width)
    sidebar.grid(row=0, column=0, sticky="ns")
    sidebar.pack_propagate(False)
    tk.Frame(app, bg=theme.HAIRLINE, width=1).grid(row=0, column=0, sticky="nse")

    brand_row = tk.Frame(sidebar, bg=NAV)
    brand_row.pack(fill="x", padx=20, pady=(24, 20))
    tk.Label(brand_row, image=app.brand_mark, bg=NAV, bd=0).pack(side="left")
    tk.Label(brand_row, text=APP_NAME, bg=NAV, fg=theme.INK,
             font=(theme.FAMILY["latin"], 18, "bold")).pack(side="left", padx=(9, 0))

    app.nav_buttons = {}
    app.nav_rows = {}
    app.active_page = "tb"
    for key, title in PAGES.items():
        if key == "security":
            continue
        item = NavItem(sidebar, key, title, lambda key=key: navigate(app, key))
        item.pack(fill="x", padx=12, pady=1)
        app.nav_buttons[key] = item
        app.nav_rows[key] = item

    footer = tk.Frame(sidebar, bg=NAV)
    footer.pack(side="bottom", fill="x", padx=12, pady=(0, 16))
    settings_item = NavItem(footer, "security", PAGES["security"], lambda: navigate(app, "security"))
    settings_item.pack(fill="x", pady=(0, 6))
    app.nav_buttons["security"] = settings_item
    app.nav_rows["security"] = settings_item
    model = tk.Frame(footer, bg=NAV)
    model.pack(fill="x", padx=12)
    app.model_dot = tk.Label(model, text="●", bg=NAV, fg=theme.MUTED, font=(theme.FAMILY["body"], 7))
    app.model_dot.pack(side="left")
    tk.Label(model, textvariable=app.ai_state, bg=NAV, fg=theme.MUTED,
             font=(theme.FAMILY["body"], 8), anchor="w").pack(side="left", padx=(6, 0))
    app.ai_state.trace_add("write", lambda *_args: _paint_model_dot(app))
    _paint_model_dot(app)
    tk.Label(footer, text=f"v{__version__} · {brand.COPYRIGHT}", bg=NAV, fg=theme.MUTED,
             font=(theme.FAMILY["mono"], 8)).pack(anchor="w", padx=12, pady=(6, 0))

    app.content = tk.Frame(app, bg=BG)
    app.content.grid(row=0, column=1, sticky="nsew")
    app.content.columnconfigure(0, weight=1)
    app.content.rowconfigure(1, weight=1)
    app.page_title = tk.StringVar(value=PAGES["tb"])
    app.page_context = tk.StringVar(value="尚未读取账套")
    app.header_stats = tk.StringVar(value="")
    host = tk.Frame(app.content, bg=BG)
    host.grid(row=1, column=0, sticky="nsew")
    host.columnconfigure(0, weight=1)
    host.rowconfigure(0, weight=1)
    app.pages = {key: tk.Frame(host, bg=BG) for key in PAGES}
    for page in app.pages.values():
        page.grid(row=0, column=0, sticky="nsew")
        page.columnconfigure(0, weight=1)
    app.workspace = app.pages["tb"]
    app.page_scrolls = {}


def _paint_model_dot(app):
    state = app.ai_state.get()
    tone = theme.OK if state.startswith("已启用") else (theme.WARN if state.startswith("已配置") else theme.MUTED)
    try:
        app.model_dot.configure(fg=tone)
    except tk.TclError:
        pass


def build_home(app):
    area = scroll_page(app, "tb")
    shell = tk.Frame(area, bg=BG)
    shell.grid(row=0, column=0, sticky="ew")
    shell.columnconfigure(0, weight=1)

    hero = tk.Frame(shell, bg=BG)
    hero.grid(row=0, column=0, sticky="ew")
    # A small open ledger drawn in the same ink as the navigation icons.
    emblem = tk.Canvas(hero, width=112, height=70, bg=BG, bd=0, highlightthickness=0)
    emblem.pack()
    emblem.create_polygon(6, 25, 38, 10, 56, 16, 86, 5, 108, 14, 108, 46,
                          76, 61, 56, 54, 26, 66, 6, 57, fill="#E8D9C3", outline="")
    emblem.create_polygon(6, 23, 38, 9, 56, 15, 87, 4, 107, 12, 77, 25, 56, 19,
                          26, 32, fill="#FCF8F0", outline=theme.ACCENT_DARK)
    emblem.create_polygon(6, 23, 26, 32, 56, 19, 77, 25, 107, 12, 104, 42,
                          75, 55, 55, 49, 25, 62, 5, 53,
                          fill="#FBF7EF", outline=theme.ACCENT_DARK)
    for x, y, dx, dy in ((26,32,-1,30),(56,19,-1,30),(77,25,-2,30)):
        emblem.create_line(x, y, x+dx, y+dy, fill="#C1A789")
    for offset in (7, 12, 17, 22):
        emblem.create_line(30,32+offset,51,23+offset,fill="#CEBDA6")
        emblem.create_line(80,25+offset,100,16+offset,fill="#CEBDA6")
    emblem.create_polygon(88,20,94,17,93,33,90,32,87,36,fill=theme.ACCENT,outline="")

    app.home_greeting = tk.StringVar(master=app)
    title = tk.Label(hero, textvariable=app.home_greeting, bg=BG, fg=INK,
                     font=theme.font(38, family="display"))
    title.pack(fill="x", pady=(14, 6))
    subtitle = tk.Label(hero, text="从鼎信诺账套开始，在审计对话中填写 TB、制作底稿。",
                        bg=BG, fg=MUTED, justify="center", font=theme.font(11))
    subtitle.pack(fill="x")
    theme.wrap_to_parent(subtitle, padding=8)

    card = theme.RoundCard(shell, pad=20)
    card.grid(row=1, column=0, sticky="ew", pady=(32, 0))
    card.body.columnconfigure(1, weight=1)
    icon = tk.Canvas(card.body, width=46, height=46, bg=theme.SURFACE, bd=0, highlightthickness=0)
    icon.grid(row=0, column=0, rowspan=2, padx=(0, 14))
    theme.rounded_rect(icon, 1, 1, 45, 45, 10, fill=theme.ACCENT_SOFT, outline="")
    theme.draw_icon(icon, "papers", 12, 12, 22, theme.ACCENT_DARK)
    app.overview_entity = tk.StringVar()
    app.overview_meta = tk.StringVar()
    app.home_entity = tk.Label(card.body, bg=theme.SURFACE, fg=INK, anchor="w", width=1,
                               font=theme.font(13, weight="bold"))
    app.home_entity.grid(row=0, column=1, sticky="ew")
    meta = tk.Label(card.body, textvariable=app.overview_meta, bg=theme.SURFACE, fg=MUTED,
                    anchor="w", width=1, font=theme.font(9))
    meta.grid(row=1, column=1, sticky="ew", pady=(3, 0))
    app.home_continue = ttk.Button(card.body, text="进入审计对话", style="Accent.TButton",
                                   command=lambda: navigate(app, "agent"))

    def toggle():
        if app.home_source.winfo_manager():
            app.home_source.grid_remove()
            hero.grid()
        else:
            hero.grid_remove()
            app.home_source.grid()
            app.install_entry.focus_set()
        fit_height()
        app.page_scrolls["tb"].canvas.yview_moveto(0)
        refresh_context(app)

    app.home_select = ttk.Button(card.body, text="选择账套", style="Accent.TButton", command=toggle)
    app.home_select.grid(row=0, column=2, rowspan=2, padx=(10, 0))
    def fit(event):
        margin = max(theme.px(app, 18), (event.width - theme.px(app, 704)) // 2)
        shell.grid_configure(padx=margin)
        title.configure(font=theme.font(30 if event.width < theme.px(app, 650) else 38, family="display"))

    def fit_height(event=None):
        height = app.page_scrolls["tb"].canvas.winfo_height()
        top = max(20, min(theme.px(app, 165), int(height * 0.19))) if hero.winfo_manager() else 0
        shell.grid_configure(pady=(top, 28))

    area.bind("<Configure>", fit, add="+")
    app.page_scrolls["tb"].canvas.bind("<Configure>", fit_height, add="+")
    return shell


def build_pages(app):
    app.review_status = tk.StringVar(value="")
    from . import agent_ui
    agent_ui.build(app)
    _build_papers(app)
    _build_notes(app)
    _build_report(app)
    _build_settings(app)
    refresh_context(app)


def _build_papers(app):
    """展示可制作项目，任务统一从审计对话发送。"""
    page = scroll_page(app, "papers")
    page.columnconfigure(0, weight=1)
    actions = tk.Frame(page, bg=BG)
    actions.grid(row=0, column=0, sticky="ew", padx=32, pady=(8, 0))
    ttk.Button(actions, text="在审计对话中制作", style="Accent.TButton",
               command=lambda: _start_paper_chat(app)).pack(side="left")
    from . import evidence_ui
    ttk.Button(actions, text="项目资料",
               command=lambda: evidence_ui.open_project(app)).pack(side="left", padx=6)
    ttk.Button(actions, text="选择待复核底稿", style="Quiet.TButton",
               command=lambda: app._attach_review_document("底稿")).pack(side="left", padx=(8, 0))
    ttk.Button(actions, text="复核底稿文字", style="Quiet.TButton",
               command=lambda: app._agent_review("底稿")).pack(side="left", padx=(2, 0))
    status = tk.Label(page, textvariable=app.review_status, bg=BG, fg=theme.MUTED,
                      font=(theme.FAMILY["body"], 9), anchor="w", justify="left",
                      wraplength=820)
    status.grid(row=1, column=0, sticky="ew", padx=32, pady=(10, 0))
    theme.wrap_to_parent(status, padding=64)

    card = theme.RoundCard(page, pad=0)
    card.grid(row=2, column=0, sticky="ew", padx=32, pady=(14, 0))
    head = tk.Frame(card.body, bg=theme.SURFACE)
    head.pack(fill="x", padx=16, pady=(14, 6))
    tk.Label(head, text="报表项目", bg=theme.SURFACE, fg=INK,
             font=theme.font(12, weight="bold", family="display")).pack(side="left")
    app.papers_summary = tk.StringVar(value="")
    tk.Label(head, textvariable=app.papers_summary, bg=theme.SURFACE, fg=MUTED,
             font=theme.font(8)).pack(side="right")
    columns = ("index", "name", "accounts", "amount", "basis")
    widths = {"index": 80, "name": 220, "accounts": 70, "amount": 170, "basis": 90}
    app.papers_tree = ttk.Treeview(card.body, columns=columns, show="headings", height=12,
                                   selectmode="browse")
    for key, title, anchor in (("index", "索引", "w"), ("name", "报表项目", "w"),
                               ("accounts", "科目", "e"), ("amount", "账面数", "e"),
                               ("basis", "口径", "w")):
        app.papers_tree.heading(key, text=title, anchor=anchor)
        app.papers_tree.column(key, width=widths[key], anchor=anchor, stretch=True)
    theme.fit_columns(app.papers_tree, widths)
    app.papers_tree.tag_configure("odd", background=theme.SURFACE_ALT)
    app.papers_tree.tag_configure("bad", foreground=theme.BAD)
    app.papers_tree.pack(fill="x", padx=8, pady=(0, 10))
    app.papers_tree.bind("<Double-1>", lambda event: _start_paper_chat(
        app, app.papers_tree.identify_row(event.y)) if app.papers_tree.identify_row(event.y) else None)
    app.papers_tree.bind("<Return>", lambda _event: _start_paper_chat(app))

    app.intake_status = tk.StringVar(master=app, value="")
    intake = ttk.Label(page, textvariable=app.intake_status, wraplength=820)
    intake.grid(row=3, column=0, sticky="nw", padx=32, pady=(10, 0))
    theme.wrap_to_parent(intake, padding=64)
    page.rowconfigure(4, weight=1)


def _start_paper_chat(app, item=None):
    from . import agent_ui

    selected = app.papers_tree.selection()
    item = item or (selected[0] if selected else "")
    values = app.papers_tree.item(item, "values") if item else ()
    if not app.rows or app.loaded_subject is None:
        navigate(app, "tb")
        return
    navigate(app, "agent")
    if values:
        agent_ui.suggest(app, f"制作 {values[0]} {values[1]}底稿")
    elif not app.agent_input.get("1.0", "end-1c").strip():
        agent_ui.suggest(app, "制作底稿")


def _refresh_papers(app):
    tree = getattr(app, "papers_tree", None)
    if tree is None:
        return
    for iid in tree.get_children():
        tree.delete(iid)
    if not app.rows:
        app.papers_summary.set("")
        return
    from . import agent_tools
    try:
        projects, unmapped = agent_tools.report_projects(app.rows, app.paper_library)
    except (ValueError, KeyError, AttributeError) as exc:
        app.papers_summary.set(f"无法归集：{exc}")
        return
    for index, item in enumerate(projects):
        tags = ["odd"] if index % 2 else []
        if item.get("error"):
            tags.append("bad")
            values = (item["index_no"], item["name"], "", "", item["error"][:40])
        else:
            try:
                amount = f"{Decimal(item['total']):,.2f}"
            except (InvalidOperation, ValueError):
                amount = item["total"]
            values = (item["index_no"], item["name"], len(item["accounts"]), amount,
                      "本期发生额" if item["is_profit_loss"] else "期末余额")
        tree.insert("", "end", iid=str(index), values=values, tags=tuple(tags))
    app.papers_summary.set(f"{len(projects)} 个项目" + (f" · 未映射科目 {len(unmapped)}" if unmapped else ""))


def _build_notes(app):
    page = scroll_page(app, "notes")
    actions = tk.Frame(page, bg=BG)
    actions.grid(row=0, column=0, sticky="ew", padx=32, pady=(8, 0))
    ttk.Button(actions, text="选择待复核附注", style="Accent.TButton",
               command=lambda: app._attach_review_document("附注")).pack(side="left")
    ttk.Button(actions, text="按知识依据复核", style="Quiet.TButton",
               command=lambda: app._agent_review("附注")).pack(side="left", padx=(8, 0))
    status = tk.Label(page, textvariable=app.review_status, bg=BG, fg=theme.MUTED,
                      font=(theme.FAMILY["body"], 9), anchor="w", justify="left",
                      wraplength=820)
    status.grid(row=1, column=0, sticky="ew", padx=32, pady=(14, 0))
    theme.wrap_to_parent(status, padding=64)
    page.rowconfigure(2, weight=1)


def _build_report(app):
    page = app.pages["report"]
    card = theme.RoundCard(page, pad=0)
    card.grid(row=0, column=0, sticky="new", padx=32, pady=(8, 0))
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
    actions.grid(row=1, column=0, sticky="ew", padx=32, pady=(14, 0))
    ttk.Button(actions, text="导出准备清单", style="Accent.TButton",
               command=lambda: _export_readiness(app)).pack(side="left")
    ttk.Button(actions, text="按知识依据复核", style="Quiet.TButton",
               command=lambda: app._agent_review("报告")).pack(side="left", padx=(8, 0))
    status = tk.Label(page, textvariable=app.review_status, bg=BG, fg=theme.MUTED,
                      font=(theme.FAMILY["body"], 9), anchor="w", justify="left",
                      wraplength=820)
    status.grid(row=2, column=0, sticky="ew", padx=32, pady=(14, 0))
    theme.wrap_to_parent(status, padding=64)
    page.rowconfigure(3, weight=1)


def _build_settings(app):
    page = scroll_page(app, "security")
    box = tk.Frame(page, bg=BG)
    box.grid(row=0, column=0, sticky="ew", padx=32, pady=(8, 0))
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
    tk.Frame(page, bg=theme.HAIRLINE, height=1).grid(row=1, column=0, sticky="ew", padx=32, pady=(8, 0))
    tk.Label(page, text=brand.FOOTER_TEXT, bg=BG, fg=theme.MUTED,
             font=(theme.FAMILY["mono"], 8)).grid(row=2, column=0, sticky="w", padx=32, pady=(14, 0))
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
    app.page_title.set(brand.AGENT_NAME if key == "agent" else PAGES[key])
    if key == "tb":
        app.page_header.grid_remove()
        if app.home_source.winfo_manager():
            app.home_select.invoke()
        previous = app.home_greeting.get()
        app.home_greeting.set(random.choice([text for text in HOME_GREETINGS if text != previous]))
        app.page_scrolls["tb"].canvas.yview_moveto(0)
    else:
        app.page_header.grid()
    for name, button in app.nav_buttons.items():
        button.set_active(name == key)
    refresh_context(app)
    if key == "report":
        _refresh_report(app)
    if key == "papers":
        _refresh_papers(app)
    if key == "agent":
        app.agent_input.focus_set()
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
    if hasattr(app, "home_continue"):
        loaded = app.loaded_subject if app.rows else None
        app.overview_entity.set(loaded.name if loaded else "尚未读取")
        app.overview_meta.set(context_meta(loaded) if loaded else
                              "待读取科目余额" if app.current_subject else "请选择项目、主体和期间")
        app.home_continue.configure(state="normal" if loaded else "disabled",
                                    style="Accent.TButton" if loaded else "TButton")
        if hasattr(app, "home_select"):
            app.home_entity.configure(text=loaded.name if loaded else "鼎信诺账套")
            expanded = bool(app.home_source.winfo_manager())
            app.home_select.configure(text="收起选择" if expanded else "更换账套" if loaded else "选择账套",
                                      style="TButton" if loaded or expanded else "Accent.TButton")
            if loaded:
                app.home_continue.grid(row=0, column=3, rowspan=2, padx=(8, 0))
            else:
                app.home_continue.grid_remove()
        app.home_read.configure(text="重新读取" if loaded else "读取科目余额",
                                state="disabled" if app.busy or app.current_subject is None else "normal",
                                style="TButton" if loaded else "Accent.TButton")
    document = app.review_document
    app.review_status.set(
        (f"{document['role']}：{document['name']} · 最多发送 12000 字符"
         + ("（内容已截取）" if document["truncated"] else "")) if document else "")
    stats = getattr(app, "header_stats", None)
    if stats is not None:
        accounts = len({row.code for row in app.rows if getattr(row, "code", "")})
        parts = [f"科目 {accounts}"] if accounts else []
        if app.results:
            parts += [f"待确认 {pending}", f"差异 {differences}"]
        stats.set("    ".join(parts))
    tiles = getattr(app, "stat_tiles", None)
    if tiles:
        accounts = len({row.code for row in app.rows if getattr(row, "code", "")})
        tiles["accounts"].update_value(accounts)
        tiles["pending"].update_value(pending, tone=theme.WARN if pending else theme.INK)
        tiles["differences"].update_value(differences, tone=theme.BAD if differences else theme.INK)
    if hasattr(app, "agent_context_var"):
        from . import agent_ui
        agent_ui.refresh(app)
