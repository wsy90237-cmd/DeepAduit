"""DeepAudit workspace pages; TB owns amounts, future modules share its context."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from . import workflow

BG, CARD, INK, MUTED = "#ffffff", "#ffffff", "#18263b", "#68778d"
NAV, ACCENT, BORDER = "#ffffff", "#2563eb", "#dbe7fb"
NAV_TXT, NAV_HOVER, ACCENT_SOFT = "#526580", "#f5f9ff", "#eff6ff"
LINE = "#eef2f8"
FONT = "Microsoft YaHei UI"
PAGES = {"overview": "工作台", "tb": "账套与 TB",
         "papers": "审计底稿", "notes": "财务报表附注", "report": "报告中心",
         "security": "模型与安全"}


def label(parent, text, *, size=10, color=INK, bold=False, **kwargs):
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), **kwargs)


def panel(parent, row, *, column=0, padx=22, pady=10):
    frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1, bd=0)
    frame.grid(row=row, column=column, sticky="nsew", padx=padx, pady=pady)
    return frame


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
        """按内容与可用高度分配表单区高度：放得下就完整显示，否则滚动，把余量留给结果表。"""
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
    """静态页面统一放进滚动容器，返回可直接放置卡片的 body。"""
    holder = app.pages[key]
    holder.rowconfigure(0, weight=1)
    holder.columnconfigure(0, weight=1)
    area = ScrollArea(holder, bg=BG, height=520)
    area.grid(row=0, column=0, sticky="nsew")
    app.page_scrolls[key] = area
    return area.body


def build_shell(app):
    app.columnconfigure(1, weight=1)
    app.rowconfigure(0, weight=1)
    sidebar = tk.Frame(app, bg=NAV, width=208)
    sidebar.grid(row=0, column=0, sticky="ns")
    sidebar.pack_propagate(False)
    tk.Frame(sidebar, bg=BORDER, width=1).pack(side="right", fill="y")
    brand = tk.Frame(sidebar, bg=NAV)
    brand.pack(fill="x", padx=16, pady=(18, 12))
    mark = tk.Frame(brand, bg=NAV)
    mark.pack(anchor="w")
    tk.Label(mark, image=app.whale_mark, bg=NAV, bd=0).pack(side="left")
    label(mark, "DeepAudit", size=13, color=ACCENT, bold=True).pack(side="left", padx=(4, 0))
    label(sidebar, "工作空间", size=8, color=MUTED).pack(anchor="w", padx=20, pady=(3, 10))
    app.nav_buttons = {}
    app.nav_stripes = {}
    app.active_page = "overview"
    for key, title in PAGES.items():
        row = tk.Frame(sidebar, bg=NAV)
        row.pack(fill="x", padx=10, pady=1)
        stripe = tk.Frame(row, bg=NAV, width=3)
        stripe.pack(side="left", fill="y")
        button = tk.Button(row, text=title, anchor="w", relief="flat", bd=0,
                           bg=NAV, fg=NAV_TXT, activebackground=ACCENT_SOFT, activeforeground=ACCENT,
                           font=(FONT, 9), padx=13, pady=9, cursor="hand2", takefocus=True,
                           command=lambda key=key: navigate(app, key))
        button.pack(side="left", fill="x", expand=True)
        button.bind("<Enter>", lambda _event, key=key: _hover_nav(app, key, True))
        button.bind("<Leave>", lambda _event, key=key: _hover_nav(app, key, False))
        app.nav_buttons[key] = button
        app.nav_stripes[key] = stripe
    footer = tk.Frame(sidebar, bg=NAV)
    footer.pack(side="bottom", fill="x", padx=18, pady=18)
    label(footer, "●  本机处理", size=9, color=ACCENT).pack(anchor="w")
    label(footer, "金额由本地规则计算", size=8, color=MUTED).pack(anchor="w", pady=6)
    label(footer, f"DeepAudit  /  {__version__}", size=8, color=MUTED).pack(anchor="w", pady=(8, 0))
    app.content = tk.Frame(app, bg=BG)
    app.content.grid(row=0, column=1, sticky="nsew")
    app.content.columnconfigure(0, weight=1)
    app.content.rowconfigure(1, weight=1)
    app.page_title = tk.StringVar(value=PAGES["overview"])
    app.page_context = tk.StringVar(value="尚未读取账套 · 请先确认主体与期间")
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


def build_pages(app):
    app.review_status = tk.StringVar(value="尚未选择待复核文档。")
    app.overview_template = tk.StringVar(value="未选择模板")
    app.overview_notice = tk.StringVar()
    _build_overview(app)
    for key, title, description, steps in (
            ("notes", "让附注与已复核 TB 保持一致", "当前支持文字与知识来源辅助复核；完整生成与模板导出待实现。",
             ("关联已复核 TB", "选择现有附注", "按知识依据辅助复核")),
            ("papers", "按报表项目整册制作底稿", "一册包含该报表项目的全部已确认科目及其明细，例如货币资金含库存现金、银行存款、其他货币资金；可导出 Excel 草稿，审定数与审计调整留待复核。",
             ("读取账套并确认主体、期间", "选择报表项目，核对整册包含的科目、备抵与客商维度", "制作底稿草稿，复核后导出"))):
        future = scroll_page(app, key)
        card = panel(future, 0, pady=(25, 12))
        label(card, "报表项目整册底稿 · 科目与备抵随册列示" if key == "papers" else "文字辅助复核已接入 · 生成与模板导出待实现",
              size=9, color=ACCENT, bold=True).pack(anchor="w", padx=28, pady=(28, 15))
        label(card, title, size=22, bold=True).pack(anchor="w", padx=28)
        label(card, description, color=MUTED).pack(anchor="w", padx=28, pady=14)
        for number, step in enumerate(steps, 1):
            label(card, f"{number:02}    {step}", size=12).pack(anchor="w", padx=30, pady=15)
        label(card, "将共用当前主体、期间、TB 版本及复核记录，避免各模块重复取数。",
              color=MUTED, wraplength=750, justify="left").pack(anchor="w", padx=28, pady=18)
        tk.Label(card, textvariable=app.review_status, bg=CARD, fg=ACCENT, font=(FONT, 9),
                 wraplength=820, justify="left").pack(anchor="w", padx=28, pady=(0, 8))
        role = "附注" if key == "notes" else "底稿"
        if key == "papers":
            ttk.Button(card, text="制作报表项目底稿", style="Accent.TButton",
                       command=app._draft_paper).pack(anchor="w", padx=28, pady=(4, 10))
        ttk.Button(card, text=f"选择待复核{role}", command=lambda role=role: app._attach_review_document(role)).pack(anchor="w", padx=28, pady=(4, 8))
        ttk.Button(card, text=f"按知识依据复核{role}", style="Accent.TButton", command=lambda role=role: app._agent_review(role)).pack(anchor="w", padx=28, pady=(0, 12))
        ttk.Button(card, text="返回 TB 复核", command=lambda: navigate(app, "tb")).pack(
            anchor="w", padx=28, pady=(0, 28))
    _build_report(app)
    _build_security(app)
    refresh_context(app)


def _hover_nav(app, key, active):
    if getattr(app, "active_page", "overview") == key:
        return
    app.nav_buttons[key].configure(bg=NAV_HOVER if active else NAV)


def _build_overview(app):
    """工作台：一屏内回答「现在是什么状态、下一步做什么、还能去哪」。"""
    page = scroll_page(app, "overview")
    page.columnconfigure(0, weight=1)

    hero = tk.Frame(page, bg=CARD, highlightbackground=BORDER, highlightthickness=1, bd=0)
    hero.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 0))
    hero.columnconfigure(0, weight=1)

    left = tk.Frame(hero, bg=CARD)
    left.grid(row=0, column=0, sticky="ew", padx=(26, 12), pady=22)
    label(left, "当前项目", size=8, color=MUTED).pack(anchor="w")
    tk.Label(left, textvariable=app.page_context, bg=CARD, fg=INK, font=(FONT, 17, "bold"),
             anchor="w").pack(anchor="w", pady=(4, 12))
    facts = tk.Frame(left, bg=CARD)
    facts.pack(fill="x")
    facts.columnconfigure(1, weight=1)
    for index, (name, variable, wrap) in enumerate((("TB 模板", app.overview_template, 340),
                                                    ("下一步", app.overview_notice, 520))):
        label(facts, name, size=8, color=MUTED, width=7, anchor="w").grid(
            row=index, column=0, sticky="nw", pady=(0 if index == 0 else 7, 0))
        tk.Label(facts, textvariable=variable, bg=CARD, fg="#33455f", font=(FONT, 9), anchor="w",
                 justify="left", wraplength=wrap).grid(
            row=index, column=1, sticky="ew", pady=(0 if index == 0 else 7, 0))

    actions = tk.Frame(hero, bg=CARD, width=300)
    actions.grid(row=0, column=1, sticky="ne", padx=(0, 26), pady=22)
    ttk.Button(actions, text="进入账套与 TB", style="Accent.TButton", width=15,
               command=lambda: navigate(app, "tb")).pack(fill="x")
    # 签字人：工作台统一维护，导出底稿时自动写入抬头，避免每份底稿重复填写。
    signers = tk.Frame(actions, bg=CARD)
    signers.pack(fill="x", pady=(12, 0))
    for text, variable in (("编制人", app.prepared_by_var), ("复核人", app.reviewed_by_var)):
        group = tk.Frame(signers, bg=CARD)
        group.pack(side="left", padx=(0, 12))
        label(group, text, size=8, color=MUTED).pack(anchor="w")
        entry = ttk.Entry(group, textvariable=variable, width=11, font=(FONT, 9))
        entry.pack(fill="x", pady=(2, 0))
        entry.bind("<FocusOut>", app._save_signers)
        entry.bind("<Return>", app._save_signers)

    modules = tk.Frame(page, bg=BG)
    modules.grid(row=1, column=0, sticky="new", padx=24, pady=(18, 20))
    for col in range(4):
        modules.columnconfigure(col, weight=1, uniform="module")
    for col, (key, number, title, summary, chip) in enumerate((
            ("tb", "01", "TB 填列与复核", "取数、识别、复核后另存。", "可用"),
            ("papers", "02", "审计底稿", "按报表项目整册制作，含备抵与明细。", "底稿草稿"),
            ("notes", "03", "财务报表附注", "复核附注文字与依据。", "辅助复核"),
            ("report", "04", "报告中心", "核对准备情况与现有报告。", "辅助复核"))):
        card = tk.Frame(modules, bg=CARD, highlightbackground=BORDER, highlightthickness=1, bd=0)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 12, 0))
        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=22, pady=(20, 0))
        label(head, number, size=9, color=ACCENT, bold=True).pack(side="left")
        tk.Label(head, text=chip, bg=ACCENT_SOFT, fg=ACCENT, font=(FONT, 8), padx=7, pady=2).pack(side="right")
        label(card, title, size=13, bold=True).pack(anchor="w", padx=22, pady=(10, 0))
        tk.Label(card, text=summary, bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w", justify="left",
                 wraplength=210).pack(anchor="w", padx=22, pady=(8, 14))
        link = tk.Label(card, text="进入  →", bg=CARD, fg=ACCENT, font=(FONT, 9, "bold"), cursor="hand2")
        link.pack(anchor="w", padx=22, pady=(0, 20))
        link.bind("<Button-1>", lambda _event, name=key: navigate(app, name))

    page.rowconfigure(2, weight=1)


def _build_security(app):
    page = scroll_page(app, "security")
    card = panel(page, 0, pady=(25, 12))
    label(card, "数据与访问保护", size=20, bold=True).pack(anchor="w", padx=26, pady=(25, 18))
    for title, text in (
            ("原始账套", "仅复制一致副本取数，原数据库不写入、不修复、不升级。"),
            ("API 密钥", "由当前 Windows 用户的 DPAPI 加密保存，可在 AI 设置中停用或删除。"),
            ("对外请求", "AI 复核先预览上传内容，金额计算始终留在本机。"),
            ("操作留痕", "日志含哈希链与加密末尾检查点；校验异常时停止继续追加。")):
        line = tk.Frame(card, bg=CARD)
        line.pack(fill="x", padx=26, pady=10)
        label(line, title, size=10, bold=True, width=12, anchor="w").pack(side="left")
        label(line, text, size=10, color=MUTED, wraplength=680, justify="left").pack(side="left")
    actions = tk.Frame(card, bg=CARD)
    actions.pack(fill="x", padx=26, pady=(16, 24))
    ttk.Button(actions, text="管理 AI 设置", command=app._open_ai_settings).pack(side="left")
    ttk.Button(actions, text="校验本机日志", command=app._verify_audit).pack(side="left", padx=10)
    release = panel(page, 1, pady=(0, 20))
    label(release, "发布与代码保护", size=14, bold=True).pack(anchor="w", padx=26, pady=(22, 12))
    build = "原生编译版" if globals().get("__compiled__") else "开发版（源码运行）"
    label(release, f"当前运行：{build}", color=ACCENT).pack(anchor="w", padx=26, pady=6)
    label(release, "原生编译可提高代码还原成本，无法阻止拥有软件和运行权限的人进行全部分析。\n"
          "正式分发仅提供编译产物；代码签名须配置有效证书，当前未配置签名。\n"
          "本机日志检查点不能防止同一账户同时回滚日志及检查点；独立存证需后续接入服务器。",
          color=MUTED, wraplength=820, justify="left").pack(anchor="w", padx=26, pady=(8, 24))


def _build_report(app):
    page = app.pages["report"]
    page.rowconfigure(1, weight=1)
    head = panel(page, 0, pady=(22, 10))
    label(head, "报告中心", size=22, bold=True).pack(anchor="w", padx=22, pady=(20, 10))
    label(head, "当前可核对报告准备情况，并辅助复核现有报告；报告生成与签发待实现。",
          color=MUTED, wraplength=900, justify="left").pack(anchor="w", padx=22, pady=(0, 18))
    tk.Label(head, textvariable=app.review_status, bg=CARD, fg=ACCENT, font=(FONT, 9),
             wraplength=850, justify="left").pack(anchor="w", padx=22, pady=(0, 12))
    body = panel(page, 1, pady=(0, 10))
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)
    app.readiness_tree = ttk.Treeview(body, columns=("item", "status", "detail"), show="headings", height=8)
    for key, title, width in (("item", "审计环节", 165), ("status", "当前状态", 190), ("detail", "依据与缺口", 610)):
        app.readiness_tree.heading(key, text=title)
        app.readiness_tree.column(key, width=width, minwidth=80, stretch=key == "detail")
    app.readiness_tree.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
    scroll = ttk.Scrollbar(body, orient="vertical", command=app.readiness_tree.yview)
    scroll.grid(row=0, column=1, sticky="ns")
    app.readiness_tree.configure(yscrollcommand=scroll.set)
    app.readiness_tree.bind("<Double-1>", lambda _e: _show_gap(app))
    actions = tk.Frame(page, bg=BG)
    actions.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 20))
    ttk.Button(actions, text="导出报告准备清单", command=lambda: _export_readiness(app)).pack(side="left")
    ttk.Button(actions, text="按知识依据复核现有报告", command=lambda: app._agent_review("报告")).pack(side="left", padx=10)
    label(actions, "正式报告生成与签发：待接模板及项目复核流程", size=9, color=MUTED).pack(side="right")


def _state(app):
    return workflow.readiness(app.rows, app.results, app.loaded_subject, app.scan, app.knowledge_refs)


def _refresh_report(app):
    for iid in app.readiness_tree.get_children():
        app.readiness_tree.delete(iid)
    for index, item in enumerate(_state(app)["checks"]):
        app.readiness_tree.insert("", "end", iid=str(index), values=(item["item"], item["status"], item["detail"]))


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
        active = name == key
        button.configure(bg=ACCENT_SOFT if active else NAV,
                         fg=ACCENT if active else NAV_TXT,
                         font=(FONT, 9, "bold") if active else (FONT, 9))
        app.nav_stripes[name].configure(bg=ACCENT if active else NAV)
    refresh_context(app)
    if key == "report":
        _refresh_report(app)


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
