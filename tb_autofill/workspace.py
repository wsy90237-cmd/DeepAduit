"""DeepAudit workspace pages; TB owns amounts, future modules share its context."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from . import workflow
from .knowledge_ui import KnowledgePanel

BG, CARD, INK, MUTED = "#ffffff", "#ffffff", "#18263b", "#68778d"
NAV, ACCENT, BORDER = "#ffffff", "#2563eb", "#dbe7fb"
NAV_TXT, NAV_HOVER, ACCENT_SOFT = "#526580", "#f5f9ff", "#eff6ff"
LINE = "#eef2f8"
FONT = "Microsoft YaHei UI"
PAGES = {"overview": "工作台", "tb": "账套与 TB",
         "papers": "审计底稿", "notes": "财务报表附注", "report": "报告中心", "knowledge": "财会知识库",
         "security": "模型与安全"}


def label(parent, text, *, size=10, color=INK, bold=False, **kwargs):
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), **kwargs)


def panel(parent, row, *, column=0, padx=22, pady=10):
    frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1, bd=0)
    frame.grid(row=row, column=column, sticky="nsew", padx=padx, pady=pady)
    return frame


def build_shell(app):
    app.columnconfigure(1, weight=1)
    app.rowconfigure(0, weight=1)
    sidebar = tk.Frame(app, bg=NAV, width=208)
    sidebar.grid(row=0, column=0, sticky="ns")
    sidebar.pack_propagate(False)
    tk.Frame(sidebar, bg=BORDER, width=1).pack(side="right", fill="y")
    brand = tk.Frame(sidebar, bg=NAV)
    brand.pack(fill="x", padx=22, pady=(20, 15))
    mark = tk.Frame(brand, bg=NAV)
    mark.pack(anchor="w")
    tk.Label(mark, image=app.whale_mark, bg=NAV, bd=0).pack(side="left")
    label(mark, "DeepAudit", size=14, color=ACCENT, bold=True).pack(side="left", padx=(5, 0))
    label(sidebar, "工作空间", size=8, color=MUTED).pack(anchor="w", padx=24, pady=(3, 12))
    app.nav_buttons = {}
    app.nav_stripes = {}
    app.active_page = "overview"
    for number, (key, title) in enumerate(PAGES.items(), 1):
        row = tk.Frame(sidebar, bg=NAV)
        row.pack(fill="x", padx=10, pady=1)
        stripe = tk.Frame(row, bg=NAV, width=3)
        stripe.pack(side="left", fill="y")
        button = tk.Button(row, text=f"{number:02}   {title}", anchor="w", relief="flat", bd=0,
                           bg=NAV, fg=NAV_TXT, activebackground=ACCENT_SOFT, activeforeground=ACCENT,
                           font=(FONT, 9), padx=13, pady=9, cursor="hand2", takefocus=True,
                           command=lambda key=key: navigate(app, key))
        button.pack(side="left", fill="x", expand=True)
        button.bind("<Enter>", lambda _event, key=key: _hover_nav(app, key, True))
        button.bind("<Leave>", lambda _event, key=key: _hover_nav(app, key, False))
        app.nav_buttons[key] = button
        app.nav_stripes[key] = stripe
    footer = tk.Frame(sidebar, bg=NAV)
    footer.pack(side="bottom", fill="x", padx=23, pady=22)
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
    app.workspace.rowconfigure(3, weight=1)


def build_pages(app):
    app.review_status = tk.StringVar(value="尚未选择待复核文档。")
    app.overview_template = tk.StringVar(value="未选择模板")
    app.overview_notice = tk.StringVar()
    _build_overview(app)
    for key, title, description, steps in (
            ("notes", "让附注与已复核 TB 保持一致", "当前支持文字与知识来源辅助复核；完整生成与模板导出待实现。",
             ("关联已复核 TB", "导入附注模板", "检查勾稽并导出")),
            ("papers", "把科目、程序和审计证据连接起来", "当前支持底稿文字辅助复核；完整程序、证据与复核管理待实现。",
             ("选择科目与底稿索引", "组织程序和审计证据", "编制复核与归档"))):
        future = app.pages[key]
        card = panel(future, 0, pady=(25, 12))
        label(card, "辅助复核已接入 · 生成待接模板", size=9, color=ACCENT, bold=True).pack(anchor="w", padx=28, pady=(28, 15))
        label(card, title, size=22, bold=True).pack(anchor="w", padx=28)
        label(card, description, color=MUTED).pack(anchor="w", padx=28, pady=14)
        for number, step in enumerate(steps, 1):
            label(card, f"{number:02}    {step}", size=12).pack(anchor="w", padx=30, pady=15)
        label(card, "将共用当前主体、期间、TB 版本及复核记录，避免各模块重复取数。",
              color=MUTED, wraplength=750, justify="left").pack(anchor="w", padx=28, pady=18)
        tk.Label(card, textvariable=app.review_status, bg=CARD, fg=ACCENT, font=(FONT, 9),
                 wraplength=820, justify="left").pack(anchor="w", padx=28, pady=(0, 8))
        role = "附注" if key == "notes" else "底稿"
        ttk.Button(card, text=f"选择待复核{role}", command=lambda role=role: app._attach_review_document(role)).pack(anchor="w", padx=28, pady=(4, 8))
        ttk.Button(card, text=f"按知识依据复核{role}", style="Accent.TButton", command=lambda role=role: app._agent_review(role)).pack(anchor="w", padx=28, pady=(0, 12))
        ttk.Button(card, text="返回 TB 复核", command=lambda: navigate(app, "tb")).pack(
            anchor="w", padx=28, pady=(0, 28))
    app.knowledge_panel = KnowledgePanel(app, app.pages["knowledge"])
    _build_report(app)
    _build_security(app)
    refresh_context(app)


def _hover_nav(app, key, active):
    if getattr(app, "active_page", "overview") == key:
        return
    app.nav_buttons[key].configure(bg=NAV_HOVER if active else NAV)


def _build_overview(app):
    """工作台：一屏内回答「现在是什么状态、下一步做什么、还能去哪」。"""
    page = app.pages["overview"]
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

    actions = tk.Frame(hero, bg=CARD, width=170)
    actions.grid(row=0, column=1, sticky="ne", padx=(0, 26), pady=22)
    ttk.Button(actions, text="进入账套与 TB", style="Accent.TButton", width=15,
               command=lambda: navigate(app, "tb")).pack(fill="x")

    modules = tk.Frame(page, bg=BG)
    modules.grid(row=1, column=0, sticky="new", padx=24, pady=(18, 20))
    for col in range(4):
        modules.columnconfigure(col, weight=1, uniform="module")
    for col, (key, number, title, summary, chip) in enumerate((
            ("tb", "01", "TB 填列与复核", "取数、识别、复核后另存。", "可用"),
            ("papers", "02", "审计底稿", "核对底稿文字与依据。", "辅助复核"),
            ("notes", "03", "财务报表附注", "复核附注文字与依据。", "辅助复核"),
            ("report", "04", "报告中心", "按 TB 与附注生成审计报告。", "固定模板"))):
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
    page = app.pages["security"]
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
    label(head, "报告由已复核的 TB 与附注生成，采用固定模板；生成与签发尚未接入，当前可核对准备情况。",
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
