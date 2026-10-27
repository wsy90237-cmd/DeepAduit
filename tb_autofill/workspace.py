"""DeepAudit workspace pages; TB owns amounts, future modules share its context."""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from . import workflow
from .knowledge_ui import KnowledgePanel

BG, CARD, INK, MUTED = "#ffffff", "#ffffff", "#18263b", "#68778d"
NAV, ACCENT, BORDER = "#ffffff", "#2563eb", "#dbe7fb"
FONT = "Microsoft YaHei UI"
PAGES = {"overview": "审计驾驶舱", "tb": "账套与 TB", "risk": "风险与计划", "execution": "程序与证据",
         "notes": "财务报表附注", "papers": "审计底稿", "report": "报告中心", "knowledge": "财会知识库",
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
    label(brand, "年度财报审计引擎", size=9, color=MUTED).pack(anchor="w", pady=(5, 0))
    label(sidebar, "工作空间", size=8, color=MUTED).pack(anchor="w", padx=24, pady=(3, 12))
    app.nav_buttons = {}
    for number, (key, title) in enumerate(PAGES.items(), 1):
        button = tk.Button(sidebar, text=f"{number:02}   {title}", anchor="w", relief="flat", bd=0,
                           bg=NAV, fg="#526580", activebackground="#eff6ff", activeforeground=ACCENT,
                           font=(FONT, 9), padx=15, pady=9, cursor="hand2", takefocus=True,
                           command=lambda key=key: navigate(app, key))
        button.pack(fill="x", padx=10, pady=2)
        app.nav_buttons[key] = button
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
    page = app.pages["overview"]
    hero = panel(page, 0, pady=(22, 8))
    inner = tk.Frame(hero, bg=CARD)
    inner.pack(fill="both", padx=26, pady=25)
    label(inner, "ANNUAL AUDIT ENGINE · 一期", size=9, color=ACCENT, bold=True).pack(anchor="w")
    label(inner, "从账套出发，让审计全程可追溯。", size=23, bold=True).pack(anchor="w", pady=(10, 9))
    label(inner, "账套 → 风险计划 → 程序证据 → TB → 附注底稿 → 报告复核与签发",
          size=10, color=MUTED).pack(anchor="w")
    ttk.Button(inner, text="开始处理 TB  →", style="Accent.TButton",
               command=lambda: navigate(app, "tb")).pack(anchor="w", pady=(20, 0))
    quick = tk.Frame(inner, bg=CARD)
    quick.pack(anchor="w", pady=(8, 0))
    ttk.Button(quick, text="连接模型引擎", command=app._open_ai_settings).pack(side="left")
    ttk.Button(quick, text="配置知识来源", command=lambda: navigate(app, "knowledge")).pack(side="left", padx=8)
    ttk.Button(quick, text="查看报告准备度", command=lambda: navigate(app, "report")).pack(side="left")
    metrics = tk.Frame(page, bg=BG)
    metrics.grid(row=1, column=0, sticky="ew", padx=17, pady=6)
    app.overview_values = []
    for col, title in enumerate(("已读取科目", "已识别字段", "待复核字段", "已选写入")):
        metrics.columnconfigure(col, weight=1, uniform="metrics")
        tile = panel(metrics, 0, column=col, padx=5, pady=0)
        label(tile, title, size=9, color=MUTED).pack(anchor="w", padx=18, pady=(16, 5))
        value = tk.StringVar(value="—")
        tk.Label(tile, textvariable=value, bg=CARD, fg=INK, font=(FONT, 24, "bold")).pack(
            anchor="w", padx=18, pady=(0, 14))
        app.overview_values.append(value)
    stages = tk.Frame(page, bg=BG)
    stages.grid(row=2, column=0, sticky="ew", padx=17, pady=12)
    for col, (key, title, state, description) in enumerate((
            ("tb", "01  TB 填列与复核", "可使用", "导入模板、确认来源、复核差额并另存。"),
            ("notes", "02  财务报表附注", "可辅助复核", "导入附注文字，结合 TB 与知识依据复核。"),
            ("papers", "03  审计底稿", "可辅助复核", "导入底稿文字，核对依据与待补证据。"))):
        stages.columnconfigure(col, weight=1, uniform="stages")
        card = panel(stages, 0, column=col, padx=5, pady=0)
        label(card, state, size=8, color=ACCENT if key == "tb" else MUTED).pack(anchor="w", padx=18, pady=(17, 8))
        label(card, title, size=12, bold=True).pack(anchor="w", padx=18)
        label(card, description, size=9, color=MUTED, wraplength=250, justify="left").pack(
            anchor="w", padx=18, pady=12)
        ttk.Button(card, text="进入工作区", style="Ghost.TButton",
                   command=lambda key=key: navigate(app, key)).pack(anchor="w", padx=12, pady=(0, 13))
    context = panel(page, 3, pady=(0, 18))
    label(context, "当前项目上下文", size=10, bold=True).pack(anchor="w", padx=20, pady=(15, 5))
    tk.Label(context, textvariable=app.page_context, bg=CARD, fg=MUTED,
             font=(FONT, 10), anchor="w").pack(fill="x", padx=20, pady=(0, 16))
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
    _build_engine_pages(app)
    _build_security(app)
    refresh_context(app)


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


def _build_engine_pages(app):
    specs = (
        ("risk", "风险评估与审计计划", "设计阶段 · 不用科目余额替代全部项目风险信息", (
            ("项目承接与范围", "主体、报告期间、适用准则、业务约定和团队职责。"),
            ("风险与重要性", "企业经营背景、重大错报风险、重要性及程序安排需经项目人员确认。"),
            ("Agent 的职责", "结合选定知识整理核查方向，标明出处、缺失信息和适用性，不自动作最终判断。"))),
        ("execution", "审计程序与证据链", "设计阶段 · 当前已接科目余额，其他证据按项目取得", (
            ("账套数据", "科目余额已接入；总账、明细账、序时账和凭证仍待扩展取数。"),
            ("程序与证据", "函证、监盘、截止测试、减值测试等需相应执行记录和证据，不能从余额自动推定完成。"),
            ("复核与留痕", "后续关联程序、底稿版本、证据来源、编制人及复核人，并在来源变化后重新复核。"))))
    for key, title, status, steps in specs:
        card = panel(app.pages[key], 0, pady=(24, 16))
        label(card, title, size=22, bold=True).pack(anchor="w", padx=26, pady=(25, 12))
        label(card, status, color=ACCENT).pack(anchor="w", padx=26, pady=(0, 16))
        for heading, detail in steps:
            label(card, heading, size=12, bold=True).pack(anchor="w", padx=26, pady=(12, 5))
            label(card, detail, color=MUTED, wraplength=850, justify="left").pack(anchor="w", padx=26, pady=(0, 10))
        ttk.Button(card, text="进入知识库与 Agent", command=lambda: navigate(app, "knowledge")).pack(anchor="w", padx=26, pady=20)
    page = app.pages["report"]
    page.rowconfigure(1, weight=1)
    head = panel(page, 0, pady=(22, 10))
    label(head, "报告中心", size=22, bold=True).pack(anchor="w", padx=22, pady=(20, 10))
    label(head, "账套是数据主线，报告需要资料、程序证据与人工复核闭环。当前不具备自动签发能力。",
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
    app.page_title.set(PAGES[key])
    for name, button in app.nav_buttons.items():
        button.configure(bg="#eff6ff" if name == key else NAV,
                         fg=ACCENT if name == key else "#526580")
    refresh_context(app)
    if key == "report":
        _refresh_report(app)


def refresh_context(app):
    if not hasattr(app, "overview_values"):
        return
    pending = sum(result.status not in {"matched", "protected"} for result in app.results)
    values = (len(app.rows), len(app.results), pending, sum(r.selected for r in app.results))
    for variable, count in zip(app.overview_values, values):
        variable.set(str(count))
    subject = app._subject_label()
    app.page_context.set(f"{subject}  ·  {len(app.rows)} 个科目" if subject else "尚未读取账套 · 请先确认主体与期间")
    document = app.review_document
    app.review_status.set(
        f"当前待复核{document['role']}：{document['name']} · 仅文字，最多发送 12000 字符"
        + ("（内容已截取）" if document['truncated'] else "") if document else "尚未选择待复核文档。")
