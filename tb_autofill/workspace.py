"""DeepAudit workspace pages; TB owns amounts, future modules share its context."""

import tkinter as tk
from tkinter import ttk

from . import __version__

BG, CARD, INK, MUTED = "#f3f6fa", "#ffffff", "#18263b", "#68778d"
NAV, ACCENT, BORDER = "#13243a", "#087f8c", "#e0e7ef"
FONT = "Microsoft YaHei UI"
PAGES = {"overview": "项目总览", "tb": "TB 填列与复核", "notes": "财务报表附注",
         "papers": "审计底稿", "security": "安全与设置"}


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
    brand = tk.Frame(sidebar, bg=NAV)
    brand.pack(fill="x", padx=22, pady=(30, 25))
    label(brand, "DA", size=12, color="#77ddd6", bold=True).pack(anchor="w")
    label(brand, "DeepAudit", size=16, color="white", bold=True).pack(anchor="w", pady=(5, 2))
    label(brand, "审计工作台", size=9, color="#97abc4").pack(anchor="w")
    label(sidebar, "工作空间", size=8, color="#8196b3").pack(anchor="w", padx=24, pady=(3, 12))
    app.nav_buttons = {}
    for number, (key, title) in enumerate(PAGES.items(), 1):
        button = tk.Button(sidebar, text=f"{number:02}   {title}", anchor="w", relief="flat", bd=0,
                           bg=NAV, fg="#c3d0e2", activebackground="#213b54", activeforeground="white",
                           font=(FONT, 10), padx=18, pady=13, cursor="hand2", takefocus=True,
                           command=lambda key=key: navigate(app, key))
        button.pack(fill="x", padx=10, pady=3)
        app.nav_buttons[key] = button
    footer = tk.Frame(sidebar, bg=NAV)
    footer.pack(side="bottom", fill="x", padx=23, pady=22)
    label(footer, "●  本机处理", size=9, color="#79d7bd").pack(anchor="w")
    label(footer, "金额由本地规则计算", size=8, color="#97abc4").pack(anchor="w", pady=6)
    label(footer, f"DeepAudit  /  {__version__}", size=8, color="#7187a5").pack(anchor="w", pady=(8, 0))
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
    page = app.pages["overview"]
    hero = panel(page, 0, pady=(22, 8))
    inner = tk.Frame(hero, bg=CARD)
    inner.pack(fill="both", padx=26, pady=25)
    label(inner, "AUDIT WORKSPACE", size=9, color=ACCENT, bold=True).pack(anchor="w")
    label(inner, "每个数字，都有可复核的来源。", size=23, bold=True).pack(anchor="w", pady=(10, 9))
    label(inner, "确认项目与期间，完成 TB 复核，再衔接附注编制和底稿归档。",
          size=10, color=MUTED).pack(anchor="w")
    ttk.Button(inner, text="开始处理 TB  →", style="Accent.TButton",
               command=lambda: navigate(app, "tb")).pack(anchor="w", pady=(20, 0))
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
            ("notes", "02  财务报表附注", "规划中", "后续关联已复核 TB，填列附注并检查勾稽。"),
            ("papers", "03  审计底稿", "规划中", "后续按科目组织底稿、证据与编制复核记录。"))):
        stages.columnconfigure(col, weight=1, uniform="stages")
        card = panel(stages, 0, column=col, padx=5, pady=0)
        label(card, state, size=8, color=ACCENT if key == "tb" else MUTED).pack(anchor="w", padx=18, pady=(17, 8))
        label(card, title, size=12, bold=True).pack(anchor="w", padx=18)
        label(card, description, size=9, color=MUTED, wraplength=250, justify="left").pack(
            anchor="w", padx=18, pady=12)
        ttk.Button(card, text="进入工作区" if key == "tb" else "查看规划", style="Ghost.TButton",
                   command=lambda key=key: navigate(app, key)).pack(anchor="w", padx=12, pady=(0, 13))
    context = panel(page, 3, pady=(0, 18))
    label(context, "当前项目上下文", size=10, bold=True).pack(anchor="w", padx=20, pady=(15, 5))
    tk.Label(context, textvariable=app.page_context, bg=CARD, fg=MUTED,
             font=(FONT, 10), anchor="w").pack(fill="x", padx=20, pady=(0, 16))
    for key, title, description, steps in (
            ("notes", "让附注与已复核 TB 保持一致", "附注模块尚未实现，本页用于明确后续工作流。",
             ("关联已复核 TB", "导入附注模板", "检查勾稽并导出")),
            ("papers", "把科目、程序和审计证据连接起来", "底稿生成模块尚未实现，本页用于明确后续工作流。",
             ("选择科目与底稿索引", "组织程序和审计证据", "编制复核与归档"))):
        future = app.pages[key]
        card = panel(future, 0, pady=(25, 12))
        label(card, "规划中", size=9, color=ACCENT, bold=True).pack(anchor="w", padx=28, pady=(28, 15))
        label(card, title, size=22, bold=True).pack(anchor="w", padx=28)
        label(card, description, color=MUTED).pack(anchor="w", padx=28, pady=14)
        for number, step in enumerate(steps, 1):
            label(card, f"{number:02}    {step}", size=12).pack(anchor="w", padx=30, pady=15)
        label(card, "将共用当前主体、期间、TB 版本及复核记录，避免各模块重复取数。",
              color=MUTED, wraplength=750, justify="left").pack(anchor="w", padx=28, pady=18)
        ttk.Button(card, text="返回 TB 复核", command=lambda: navigate(app, "tb")).pack(
            anchor="w", padx=28, pady=(0, 28))
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


def navigate(app, key):
    app.pages[key].tkraise()
    app.page_title.set(PAGES[key])
    for name, button in app.nav_buttons.items():
        button.configure(bg="#24445a" if name == key else NAV,
                         fg="#87e2d8" if name == key else "#c3d0e2")
    refresh_context(app)


def refresh_context(app):
    if not hasattr(app, "overview_values"):
        return
    pending = sum(result.status not in {"matched", "protected"} for result in app.results)
    values = (len(app.rows), len(app.results), pending, sum(r.selected for r in app.results))
    for variable, count in zip(app.overview_values, values):
        variable.set(str(count))
    subject = app._subject_label()
    app.page_context.set(f"{subject}  ·  {len(app.rows)} 个科目" if subject else "尚未读取账套 · 请先确认主体与期间")
