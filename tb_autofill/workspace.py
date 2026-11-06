"""DeepAudit workspace pages; TB owns amounts, future modules share its context.

版式：暖白纸面、衬线标题、发丝分隔线。页面只保留必要说明，
金额与结论是唯一抢眼的内容；金额仍由本地规则计算。
"""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from . import theme, workflow

BG, CARD, INK, MUTED = theme.PAPER, theme.SURFACE, theme.INK, theme.MUTED
NAV, ACCENT, BORDER = theme.RAIL, theme.ACCENT, theme.HAIRLINE
NAV_TXT, NAV_HOVER, ACCENT_SOFT = theme.INK_SOFT, theme.RAIL_HOVER, theme.ACCENT_SOFT
LINE = theme.HAIRLINE
FONT = theme.FAMILY["body"]
PAGES = {"overview": "工作台", "tb": "账套与 TB",
         "papers": "审计底稿", "notes": "财务报表附注", "report": "报告中心",
         "security": "模型与安全"}


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

    def fit(self, available, *, ratio=0.46, low=176, high=326):
        """按内容与可用高度分配表单区高度：放得下就完整显示，否则滚动。"""
        needed = self.body.winfo_reqheight()
        self.set_height(min(high, max(low, min(needed, available * ratio))))

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
    """导航项：纯文字 + 左侧细线，选中时用陶土色标记，不做图标。"""

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
            widget.configure(cursor="hand2")

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
    """左侧索引栏 + 右侧内容区；导航只保留文字，选中项用细线标记。"""
    theme.install(app)
    app.columnconfigure(1, weight=1)
    app.rowconfigure(0, weight=1)
    sidebar = tk.Frame(app, bg=NAV, width=216)
    sidebar.grid(row=0, column=0, sticky="ns")
    sidebar.pack_propagate(False)
    tk.Frame(app, bg=theme.HAIRLINE, width=1).place(x=215, y=0, relheight=1)

    brand = tk.Frame(sidebar, bg=NAV)
    brand.pack(fill="x", padx=20, pady=(24, 2))
    tk.Label(brand, image=app.whale_mark, bg=NAV, bd=0).pack(side="left")
    tk.Label(brand, text="DeepAudit", bg=NAV, fg=theme.INK,
             font=(theme.FAMILY["display"], 17, "bold")).pack(side="left", padx=(8, 0))
    tk.Label(sidebar, text="审计底稿工作台", bg=NAV, fg=theme.MUTED,
             font=(theme.FAMILY["body"], 8)).pack(anchor="w", padx=22, pady=(2, 20))

    app.nav_buttons = {}
    app.nav_rows = {}
    app.nav_stripes = {}
    app.active_page = "overview"
    for key, title in PAGES.items():
        item = NavItem(sidebar, title, lambda key=key: navigate(app, key))
        item.pack(fill="x", padx=10, pady=1)
        app.nav_buttons[key] = item
        app.nav_rows[key] = item

    footer = tk.Frame(sidebar, bg=NAV)
    footer.pack(side="bottom", fill="x", padx=22, pady=18)
    tk.Frame(footer, bg=theme.HAIRLINE, height=1).pack(fill="x", pady=(0, 12))
    tk.Label(footer, text="本机处理 · 金额本地计算", bg=NAV, fg=theme.MUTED,
             font=(theme.FAMILY["body"], 8)).pack(anchor="w")
    tk.Label(footer, text=f"v{__version__} · 未编译开发版", bg=NAV, fg="#A8A59C",
             font=(theme.FAMILY["mono"], 8)).pack(anchor="w", pady=(4, 0))

    app.content = tk.Frame(app, bg=BG)
    app.content.grid(row=0, column=1, sticky="nsew")
    app.content.columnconfigure(0, weight=1)
    app.content.rowconfigure(1, weight=1)
    app.page_title = tk.StringVar(value=PAGES["overview"])
    app.page_context = tk.StringVar(value="尚未读取账套 · 请先确认主体与期间")
    app.page_eyebrow = tk.StringVar(value="")
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
    # 结果表格占剩余高度并保留最小可读高度，表单区自身滚动，底部操作区不被挤出。
    app.workspace.rowconfigure(1, weight=1, minsize=210)
    app.page_scrolls = {}


def _page_header(body, *, title="", intro="", row=0):
    """页内标题：与顶部页头重复时留空，只保留一行说明。"""
    head = tk.Frame(body, bg=BG)
    head.grid(row=row, column=0, sticky="ew", padx=30, pady=(26, 4))
    if title:
        tk.Label(head, text=title, bg=BG, fg=theme.INK,
                 font=(theme.FAMILY["display"], 24, "bold")).pack(anchor="w")
    if intro:
        tk.Label(head, text=intro, bg=BG, fg=theme.MUTED, font=(theme.FAMILY["body"], 9),
                 wraplength=760, justify="left").pack(anchor="w", pady=(6, 0))
    return head


def build_pages(app):
    app.review_status = tk.StringVar(value="尚未选择待复核文档。")
    app.overview_template = tk.StringVar(value="未选择模板")
    app.overview_notice = tk.StringVar()
    _build_overview(app)
    _build_papers(app)
    _build_notes(app)
    _build_report(app)
    _build_security(app)
    refresh_context(app)
    _bind_responsive(app)


def _bind_responsive(app):
    """窗口变窄时把统计与模块改为两列，避免内容被裁切。"""
    state = {"wide": None}

    def on_configure(event):
        wide = event.width >= 1040
        if state["wide"] == wide:
            return
        state["wide"] = wide
        _reflow(app, wide)

    try:
        app.content.bind("<Configure>", on_configure, add="+")
    except tk.TclError:
        return


def _reflow(app, wide):
    modules = getattr(app, "overview_modules", None)
    cards = getattr(app, "module_rows", None)
    if modules is not None and cards:
        columns = 2 if wide else 1
        for col in range(2):
            modules.columnconfigure(col, weight=0, uniform="")
        for col in range(columns):
            modules.columnconfigure(col, weight=1, uniform="module")
        for index, card in enumerate(cards):
            card.grid_configure(row=index // columns, column=index % columns,
                                padx=(0 if index % columns == 0 else 16, 0),
                                pady=(0 if index < columns else 10, 0))


def _build_overview(app):
    """工作台：项目状态、下一步、三个数字、四个入口，一屏读完。"""
    page = scroll_page(app, "overview")
    page.columnconfigure(0, weight=1)

    head = tk.Frame(page, bg=BG)
    head.grid(row=0, column=0, sticky="ew", padx=30, pady=(30, 0))
    tk.Label(head, text="当前项目", bg=BG, fg=theme.MUTED,
             font=(theme.FAMILY["body"], 9)).pack(anchor="w")
    tk.Label(head, textvariable=app.page_context, bg=BG, fg=theme.INK,
             font=(theme.FAMILY["display"], 28, "bold"), anchor="w", justify="left",
             wraplength=900).pack(anchor="w", pady=(4, 10))
    app.overview_chips = tk.Frame(head, bg=BG)
    app.overview_chips.pack(anchor="w")
    _chip(app.overview_chips, "期间：待确认").pack(side="left", padx=(0, 8))
    _chip(app.overview_chips, "账套：待确认").pack(side="left", padx=(0, 8))
    _chip(app.overview_chips, "模板：未选择").pack(side="left")

    next_row = tk.Frame(head, bg=BG)
    next_row.pack(fill="x", pady=(18, 0))
    tk.Label(next_row, text="下一步", bg=BG, fg=theme.MUTED,
             font=(theme.FAMILY["body"], 8), width=6, anchor="w").pack(side="left", anchor="n")
    tk.Label(next_row, textvariable=app.overview_notice, bg=BG, fg=theme.INK_SOFT,
             font=(theme.FAMILY["body"], 10), anchor="w", justify="left",
             wraplength=560).pack(side="left", anchor="n")
    ttk.Button(next_row, text="进入账套与 TB", style="Accent.TButton",
               command=lambda: navigate(app, "tb")).pack(side="left", padx=(16, 0))

    stats = tk.Frame(page, bg=BG)
    stats.grid(row=1, column=0, sticky="ew", padx=30, pady=(26, 0))
    for col in range(3):
        stats.columnconfigure(col, weight=1, uniform="stat")
    app.stat_tiles = {}
    for col, (key, title) in enumerate((("accounts", "已载入科目"), ("pending", "待确认事项"),
                                        ("differences", "现有值差异"))):
        holder = tk.Frame(stats, bg=BG)
        holder.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 22, 0))
        if col:
            tk.Frame(holder, bg=theme.HAIRLINE, width=1).place(x=-11, y=4, relheight=0.9)
        tile = theme.StatTile(holder, title, "—", "读取账套后更新")
        tile.pack(fill="x")
        app.stat_tiles[key] = tile

    tk.Frame(page, bg=theme.HAIRLINE, height=1).grid(row=2, column=0, sticky="ew", padx=30, pady=(22, 0))

    section = tk.Frame(page, bg=BG)
    section.grid(row=3, column=0, sticky="new", padx=30, pady=(18, 26))
    tk.Label(section, text="模块", bg=BG, fg=theme.MUTED,
             font=(theme.FAMILY["body"], 8)).pack(anchor="w", pady=(0, 8))
    modules = tk.Frame(section, bg=BG)
    modules.pack(fill="x")
    app.overview_modules = modules
    app.module_rows = []
    for col in range(2):
        modules.columnconfigure(col, weight=1, uniform="module")
    for index, (key, title, summary) in enumerate((
            ("tb", "账套与 TB", "取数、识别、复核后另存"),
            ("papers", "审计底稿", "按报表项目整册导出"),
            ("notes", "财务报表附注", "复核附注文字与依据"),
            ("report", "报告中心", "核对准备情况与现有报告"))):
        row = tk.Frame(modules, bg=BG, cursor="hand2")
        row.grid(row=index // 2, column=index % 2, sticky="nsew",
                 padx=(0 if index % 2 == 0 else 16, 0), pady=(0 if index < 2 else 10, 0))
        top = tk.Frame(row, bg=BG)
        top.pack(fill="x", pady=(8, 0))
        tk.Label(top, text=title, bg=BG, fg=theme.INK,
                 font=(theme.FAMILY["body"], 11), anchor="w").pack(side="left")
        tk.Label(top, text="→", bg=BG, fg=theme.ACCENT,
                 font=(theme.FAMILY["body"], 10)).pack(side="right")
        tk.Label(row, text=summary, bg=BG, fg=theme.MUTED, justify="left",
                 font=(theme.FAMILY["body"], 9), anchor="w", wraplength=320).pack(anchor="w", pady=(2, 8))
        tk.Frame(row, bg=theme.HAIRLINE, height=1).pack(fill="x")
        for widget in (row, top, row.winfo_children()[-1]):
            widget.bind("<Button-1>", lambda _event, name=key: navigate(app, name), add="+")
        app.module_rows.append(row)

    page.rowconfigure(4, weight=1)
    theme.reveal([head, stats, section], delay=30, stagger=40)


def _chip(parent, text, *, tone="muted"):
    color = theme.color_for(tone)
    back = theme.soft_for(tone) if tone != "muted" else theme.PAPER_DEEP
    return tk.Label(parent, text=text, bg=back, fg=color, font=(theme.FAMILY["body"], 8),
                    padx=9, pady=3)


def _build_papers(app):
    """底稿：一句说明 + 主操作 + 三步，右侧只放整册口径示例。"""
    page = scroll_page(app, "papers")
    _page_header(page, title="按报表项目整册出底稿",
                 intro="一册包含该项目归集的全部科目与明细，备抵按真实方向参与净额。")

    actions = tk.Frame(page, bg=BG)
    actions.grid(row=1, column=0, sticky="ew", padx=30, pady=(18, 0))
    ttk.Button(actions, text="制作报表项目底稿", style="Accent.TButton",
               command=app._draft_paper).pack(side="left")
    ttk.Button(actions, text="选择待复核底稿", style="Quiet.TButton",
               command=lambda: app._attach_review_document("底稿")).pack(side="left", padx=(6, 0))
    tk.Label(actions, textvariable=app.review_status, bg=BG, fg=theme.MUTED,
             font=(theme.FAMILY["body"], 8), anchor="w").pack(side="left", padx=12)

    grid = tk.Frame(page, bg=BG)
    grid.grid(row=2, column=0, sticky="nsew", padx=30, pady=(20, 8))
    grid.columnconfigure(0, weight=1, uniform="paper")
    grid.columnconfigure(1, weight=1, uniform="paper")
    app.papers_grid = grid

    flow = theme.RoundCard(grid, pad=0)
    flow.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
    app.papers_flow = flow
    inner = tk.Frame(flow.body, bg=theme.SURFACE)
    inner.pack(fill="both", expand=True, padx=22, pady=20)
    tk.Label(inner, text="出册流程", bg=theme.SURFACE, fg=theme.INK,
             font=(theme.FAMILY["display"], 14, "bold"), anchor="w").pack(anchor="w")
    for number, title in (("1", "读取账套"), ("2", "选择报表项目"), ("3", "制作并导出")):
        row = tk.Frame(inner, bg=theme.SURFACE)
        row.pack(fill="x", pady=(10, 0))
        tk.Label(row, text=number, bg=theme.SURFACE, fg=theme.ACCENT,
                 font=(theme.FAMILY["mono"], 9), width=2, anchor="w").pack(side="left")
        tk.Label(row, text=title, bg=theme.SURFACE, fg=theme.INK_SOFT,
                 font=(theme.FAMILY["body"], 10), anchor="w").pack(side="left")

    sample = theme.RoundCard(grid, pad=0, bg=theme.SURFACE_ALT)
    sample.grid(row=0, column=1, sticky="nsew")
    app.papers_sample = sample
    body = tk.Frame(sample.body, bg=theme.SURFACE_ALT)
    body.pack(fill="both", expand=True, padx=22, pady=20)
    tk.Label(body, text="整册口径", bg=theme.SURFACE_ALT, fg=theme.INK,
             font=(theme.FAMILY["display"], 14, "bold"), anchor="w").pack(anchor="w")
    for title, parts in (("货币资金", "库存现金 · 银行存款 · 其他货币资金"),
                         ("固定资产", "原值 · 累计折旧 · 减值准备"),
                         ("无形资产", "原价 · 累计摊销 · 减值准备")):
        row = tk.Frame(body, bg=theme.SURFACE_ALT)
        row.pack(fill="x", pady=(10, 0))
        row.columnconfigure(0, minsize=76)
        row.columnconfigure(1, weight=1)
        tk.Label(row, text=title, bg=theme.SURFACE_ALT, fg=theme.INK_SOFT,
                 font=(theme.FAMILY["body"], 9, "bold"), anchor="nw").grid(row=0, column=0, sticky="nw")
        tk.Label(row, text=parts, bg=theme.SURFACE_ALT, fg=theme.MUTED,
                 font=(theme.FAMILY["body"], 9), anchor="nw", justify="left",
                 wraplength=260).grid(row=0, column=1, sticky="nw")

    tk.Label(page, text="附注与报告生成仍未实现；界面不提供生成按钮。", bg=BG,
             fg=theme.MUTED, font=(theme.FAMILY["body"], 8)).grid(
        row=3, column=0, sticky="w", padx=30, pady=(4, 24))
    theme.reveal([flow, sample], delay=30, stagger=40)


def _build_notes(app):
    page = scroll_page(app, "notes")
    _page_header(page, intro="导入现有附注，按已确认主体、期间与知识依据做文字复核。")
    actions = tk.Frame(page, bg=BG)
    actions.grid(row=1, column=0, sticky="ew", padx=30, pady=(18, 0))
    ttk.Button(actions, text="选择待复核附注", style="Accent.TButton",
               command=lambda: app._attach_review_document("附注")).pack(side="left")
    ttk.Button(actions, text="按知识依据复核", style="Quiet.TButton",
               command=lambda: app._agent_review("附注")).pack(side="left", padx=(6, 0))
    tk.Label(page, textvariable=app.review_status, bg=BG, fg=theme.MUTED,
             font=(theme.FAMILY["body"], 8)).grid(row=2, column=0, sticky="w", padx=30, pady=(12, 24))


def _build_report(app):
    page = app.pages["report"]
    _page_header(page, intro="核对准备情况，辅助复核现有报告；正式生成与签发待实现。")
    head = tk.Frame(page, bg=BG)
    head.grid(row=1, column=0, sticky="ew", padx=30, pady=(0, 10))
    tk.Label(head, textvariable=app.review_status, bg=BG, fg=theme.MUTED,
             font=(theme.FAMILY["body"], 9), anchor="w", justify="left",
             wraplength=860).pack(anchor="w")

    card = theme.RoundCard(page, pad=0)
    card.grid(row=2, column=0, sticky="new", padx=30, pady=(0, 10))
    body = tk.Frame(card.body, bg=theme.SURFACE)
    body.pack(fill="both", expand=True, padx=12, pady=12)
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)
    app.readiness_tree = ttk.Treeview(body, columns=("item", "status", "detail"), show="headings", height=9)
    for key, title, width in (("item", "审计环节", 175), ("status", "当前状态", 190), ("detail", "依据与缺口", 600)):
        app.readiness_tree.heading(key, text=title)
        app.readiness_tree.column(key, width=width, minwidth=80, stretch=key == "detail")
    app.readiness_tree.grid(row=0, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(body, orient="vertical", command=app.readiness_tree.yview)
    scroll.grid(row=0, column=1, sticky="ns", padx=(6, 0))
    app.readiness_tree.configure(yscrollcommand=scroll.set)
    app.readiness_tree.tag_configure("odd", background=theme.SURFACE_ALT)
    app.readiness_tree.bind("<Double-1>", lambda _e: _show_gap(app))
    actions = tk.Frame(page, bg=BG)
    actions.grid(row=3, column=0, sticky="ew", padx=30, pady=(0, 20))
    ttk.Button(actions, text="导出准备清单", style="Accent.TButton",
               command=lambda: _export_readiness(app)).pack(side="left")
    ttk.Button(actions, text="按知识依据复核", style="Quiet.TButton",
               command=lambda: app._agent_review("报告")).pack(side="left", padx=(6, 0))
    tk.Label(actions, text="正式报告生成与签发待实现", bg=BG, fg=theme.MUTED,
             font=(theme.FAMILY["body"], 8)).pack(side="right")


def _build_security(app):
    page = scroll_page(app, "security")
    _page_header(page, intro="原始账套只读、凭据本机加密、对外请求先预览。")
    body = tk.Frame(page, bg=BG)
    body.grid(row=1, column=0, sticky="new", padx=30, pady=(16, 0))
    for title, text in (("原始账套", "仅复制一致副本取数，原库不写入、不修复、不升级。"),
                        ("API 密钥", "DPAPI 加密保存在当前 Windows 用户下，可随时停用。"),
                        ("对外请求", "AI 复核先预览内容与目的服务，金额计算始终留在本机。"),
                        ("操作留痕", "日志含哈希链与检查点；校验异常时停止继续追加。")):
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(0, 12))
        tk.Label(row, text=title, bg=BG, fg=theme.INK, width=9, anchor="w",
                 font=(theme.FAMILY["body"], 10, "bold")).pack(side="left", anchor="n")
        tk.Label(row, text=text, bg=BG, fg=theme.MUTED, font=(theme.FAMILY["body"], 9),
                 anchor="w", justify="left", wraplength=620).pack(side="left", anchor="n")
        tk.Frame(body, bg=theme.HAIRLINE, height=1).pack(fill="x", pady=(0, 12))
    actions = tk.Frame(page, bg=BG)
    actions.grid(row=2, column=0, sticky="ew", padx=30, pady=(4, 24))
    ttk.Button(actions, text="管理 AI 设置", style="Accent.TButton",
               command=app._open_ai_settings).pack(side="left")
    ttk.Button(actions, text="校验本机日志", style="Quiet.TButton",
               command=app._verify_audit).pack(side="left", padx=(6, 0))


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
    path = filedialog.asksaveasfilename(title="导出准备清单，不是正式审计报告", defaultextension=".html",
                                      filetypes=[("可阅读的准备清单", "*.html")], initialfile="DeepAudit-报告准备清单.html")
    if not path:
        return
    state = _state(app)
    try:
        output = workflow.export_readiness(path, state)
    except (OSError, ValueError) as exc:
        messagebox.showerror("无法导出", str(exc), parent=app)
        return
    app._audit("export_readiness", output=str(output), source_digest=state["source_digest"], ready_to_issue=False)
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
    page = app.pages[key]
    children = [child for child in page.winfo_children() if isinstance(child, tk.Frame)]
    if children:
        theme.reveal(children[:3], delay=0, stagger=30)


def refresh_context(app):
    if not hasattr(app, "overview_notice"):
        return
    pending = sum(result.status not in {"matched", "protected"} for result in app.results)
    subject = app._subject_label()
    app.page_context.set(subject if subject else "尚未读取账套")
    template = app.template_var.get().strip()
    if app.scan:
        app.overview_template.set(f"已识别：{app.scan.path.name}  ·  单位：{app.scan.unit}")
    elif template:
        app.overview_template.set(f"已选择：{Path(template).name}  ·  待识别")
    else:
        app.overview_template.set("未选择模板")
    differences = sum(result.difference not in (None, 0) for result in app.results)
    if not app.rows:
        notice = "选择项目和主体，读取科目余额。"
    elif not app.scan:
        notice = "选择 Excel 模板并识别字段。"
    elif not app.results:
        notice = "未识别到字段，请检查模板表头。"
    elif pending or differences:
        notice = f"待确认 {pending} 项，现有值差异 {differences} 项；请进入 TB 复核。"
    else:
        notice = "核对匹配结果，确认需要写入的字段。"
    app.overview_notice.set(notice)
    document = app.review_document
    app.review_status.set(
        f"当前待复核{document['role']}：{document['name']} · 仅文字，最多发送 12000 字符"
        + ("（内容已截取）" if document['truncated'] else "") if document else "尚未选择待复核文档。")
    tiles = getattr(app, "stat_tiles", None)
    if tiles:
        accounts = len({row.code for row in app.rows if getattr(row, "code", "")})
        tiles["accounts"].update_value(accounts, "科目余额表去重科目数" if accounts else "读取账套后更新")
        tiles["pending"].update_value(pending, "需人工确认的匹配项", tone=theme.WARN if pending else theme.INK)
        tiles["differences"].update_value(differences, "模板现有值与账套不一致",
                                          tone=theme.BAD if differences else theme.INK)
    chips = getattr(app, "overview_chips", None)
    if chips is not None and chips.winfo_exists():
        for child in chips.winfo_children():
            child.destroy()
        loaded = getattr(app, "loaded_subject", None)
        period = getattr(loaded, "month", None)
        ledger = getattr(loaded, "ledger_id", "")
        _chip(chips, f"期间：{getattr(loaded, 'year', '—')}-{period:02d}" if period else "期间：待确认").pack(
            side="left", padx=(0, 8))
        _chip(chips, f"账套：{ledger or '待确认'}").pack(side="left", padx=(0, 8))
        _chip(chips, f"模板：{Path(template).name if template else '未选择'}",
              tone="info" if template else "muted").pack(side="left")
