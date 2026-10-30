"""桌面界面：鼎信诺科目余额 → 用户模板填列与复核。

界面只负责串联与确认，所有金额仍由 matching.resolve_match 在本地计算。
"""

from __future__ import annotations

import json
from copy import deepcopy
import queue
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__, ai as ai_module
from . import analysis, auditlog, dxn, fields, knowledge, matching, settings, workbook, workspace
from .models import Subject, ledger_digest, money
from .connections import ModelDialog as AISettingsDialog

VERSION = __version__
PAD = 8

FONT = "Microsoft YaHei UI"
BG = "#ffffff"
CARD = "#ffffff"
BORDER = "#dbe7fb"
TX = "#18263b"
SEC = "#68778d"
MUTED = "#9aa1ac"
ACC = "#2563eb"
ACC_D = "#1d4ed8"
ACC_SOFT = "#eff6ff"

STATUS_LABEL = {
    "matched": "已匹配",
    "ambiguous": "待确认",
    "unmatched": "未匹配",
    "protected": "公式保护",
    "duplicate": "重复认领",
    "missing": "缺少依据",
    "basis_mismatch": "口径不符",
    "invalid": "数据异常",
}
STATUS_TAG = {
    "matched": "ok",
    "ambiguous": "warn",
    "duplicate": "warn",
    "unmatched": "muted",
    "missing": "muted",
    "protected": "info",
    "basis_mismatch": "bad",
    "invalid": "bad",
}


def fmt_amount(value):
    try:
        amount = money(value)
        return "" if amount is None else f"{amount:,}"
    except ValueError:
        return str(value)


def has_value(value):
    return value is not None and str(value).strip() != ""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{VERSION}")
        assets = Path(__file__).with_name("assets")
        self.whale_small = tk.PhotoImage(master=self, file=str(assets / "pixel-whale-32.png"))
        self.whale_mark = tk.PhotoImage(master=self, file=str(assets / "pixel-whale-48.png"))
        self.whale_icon = tk.PhotoImage(master=self, file=str(assets / "pixel-whale-256.png"))
        self.iconphoto(True, self.whale_icon, self.whale_small)
        # 适配小屏：按可用桌面留出标题栏与任务栏，底部操作区不再被裁掉。
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(1480, max(1000, screen_w - 90))
        height = min(920, max(620, screen_h - 130))
        self.geometry(f"{width}x{height}")
        self.minsize(1000, 600)
        self.queue = queue.Queue()
        self.busy = False
        self.projects = []
        self.subjects = []
        self.rows = []
        self.results = []
        self.scan = None
        self.current_subject = None
        self.loaded_subject = None
        self._context_revision = 0
        self._ai_subset = []
        self.library = knowledge.LocalLibrary()
        self.knowledge_refs = []
        self.review_document = None
        try:
            self.audit = auditlog.AuditLog()
        except Exception:
            self.audit = None
        self._load_settings()
        self._init_style()
        self._build()
        self._bind_context_changes()
        self.protocol("WM_DELETE_WINDOW", self._request_close)
        self.after(80, self._pump)
        self._sync_ai_state()
        self.after(250, self._autodetect)

    # ---------- 基础设施 ----------

    def _bind_context_changes(self):
        for name in ("install_var", "project_var", "subject_var", "year_var", "month_var",
                     "template_var", "period_var", "unit_var"):
            ledger = name in {"install_var", "project_var", "subject_var", "year_var", "month_var"}
            subject = name in {"install_var", "project_var"}
            getattr(self, name).trace_add("write", lambda *_args, ledger=ledger, subject=subject:
                                       self._invalidate_review(clear_ledger=ledger, clear_subject=subject))

    def _invalidate_review(self, *, clear_ledger=False, clear_subject=False):
        self._context_revision += 1
        self.scan, self.results, self._ai_subset = None, [], []
        self._chat_clear()
        self.report_btn.configure(state="disabled")
        self.write_btn.configure(state="disabled")
        self.scan_state.set("参数已变化，请重新识别")
        if clear_ledger:
            self.rows, self.loaded_subject = [], None
            self.knowledge_refs, self.review_document = [], None
            self.ledger_state.set("请重新读取科目余额")
        if clear_subject:
            self.current_subject, self.subjects = None, []
            self.subject_box.configure(values=())
            self.subject_var.set("")
        self._refresh_tree()

    def _request_close(self):
        if self.busy:
            messagebox.showinfo("任务进行中", "请等待当前任务完成后退出，以确保临时副本和输出文件正确关闭。")
            return
        self.destroy()

    def _load_settings(self):
        try:
            self.store = settings.SettingsStore()
        except settings.SettingsError as exc:
            self.store = None
            self._pending_error = str(exc)
        else:
            self._pending_error = ""

    def _init_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        self.configure(bg=BG)
        style.configure(".", font=(FONT, 9), background=BG, foreground=TX)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TX)
        style.configure("Card.TLabel", background=CARD, foreground=SEC)
        style.configure("Card.TRadiobutton", background=CARD, foreground=TX, focuscolor=CARD)
        style.map("Card.TRadiobutton", background=[("active", CARD)])
        style.configure("Card.TCheckbutton", background=CARD, foreground=TX, focuscolor=CARD)
        style.map("Card.TCheckbutton", background=[("active", CARD)])
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor=BORDER, lightcolor=BORDER,
                        darkcolor=BORDER, insertcolor=TX, padding=5, relief="flat")
        style.map("TEntry", bordercolor=[("focus", ACC)], lightcolor=[("focus", ACC)], darkcolor=[("focus", ACC)])
        style.configure("TCombobox", fieldbackground="#ffffff", background="#ffffff", bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, arrowcolor=SEC, padding=4, relief="flat")
        style.map("TCombobox", fieldbackground=[("readonly", "#ffffff")],
                  bordercolor=[("focus", ACC)], lightcolor=[("focus", ACC)], darkcolor=[("focus", ACC)])
        style.configure("TButton", background="#eff6ff", foreground=TX, borderwidth=0,
                        focusthickness=0, padding=(12, 6), relief="flat")
        style.map("TButton", background=[("pressed", "#dbeafe"), ("active", "#e0edff")],
                  foreground=[("disabled", MUTED)])
        style.configure("Accent.TButton", background=ACC, foreground="#ffffff", borderwidth=0, padding=(14, 6))
        style.map("Accent.TButton", background=[("pressed", ACC_D), ("active", ACC_D)],
                  foreground=[("disabled", "#e8eefb")])
        style.configure("Ghost.TButton", background=CARD, foreground=ACC, borderwidth=0, padding=(8, 4))
        style.map("Ghost.TButton", background=[("active", ACC_SOFT)])
        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TX,
                        borderwidth=0, rowheight=29, font=(FONT, 10))
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Treeview.Heading", background="#eff6ff", foreground=SEC, relief="flat",
                        font=(FONT, 9, "bold"), padding=(6, 6))
        style.map("Treeview.Heading", background=[("active", "#dbeafe")])
        style.map("Treeview", background=[("selected", ACC_SOFT)], foreground=[("selected", TX)])
        for bar in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(bar, background="#e6e9ee", troughcolor=BG, borderwidth=0, arrowsize=12)
        style.configure("TProgressbar", background=ACC, troughcolor="#e6e9ee", borderwidth=0, thickness=5)

    def _build(self):
        self.chat_visible = False
        workspace.build_shell(self)
        self._build_header()
        # 表单区独立滚动：宽屏时 ①② 并排，窄屏时上下堆叠，余量全部留给结果表。
        self.form_scroll = workspace.ScrollArea(self.workspace, bg=BG, height=280)
        self.form_scroll.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        self.form_wide = self.winfo_screenwidth() >= 1280
        # 两列布局需要最小可用宽度，否则卡片内容会被挤到相邻卡片上。
        self.minsize(1180 if self.form_wide else 1000, 620)
        self.form_row = self.form_scroll.body
        if self.form_wide:
            self.form_row.columnconfigure(0, weight=3)
            self.form_row.columnconfigure(1, weight=2)
        self._build_source()
        self._build_template()
        self._build_result()
        self._build_actions()
        self._build_log()
        workspace.build_pages(self)
        self._build_chat()
        workspace.navigate(self, "overview")
        self.workspace.bind("<Configure>", self._on_workspace_resize, add="+")
        if self._pending_error:
            self._log("设置读取失败：" + self._pending_error, error=True)

    def _on_workspace_resize(self, event):
        self.form_scroll.fit(event.height)

    def _card(self, row, title, pady=(10, 0), parent=None, column=0, padx=16):
        """白底卡片：1px 边框 + 标题，替代有年代感的 LabelFrame。"""
        outer = tk.Frame(parent or self.workspace, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1, bd=0)
        outer.grid(row=row, column=column, sticky="nsew", padx=padx, pady=pady)
        head = tk.Frame(outer, bg=CARD)
        head.pack(fill="x", padx=14, pady=(6, 0))
        tk.Label(head, text=title, bg=CARD, fg=SEC, font=(FONT, 9, "bold")).pack(side="left")
        body = tk.Frame(outer, bg=CARD)
        body.pack(fill="both", expand=True, padx=14, pady=(4, 8))
        return outer, body, head

    def _form_card(self, title, index):
        """表单卡片：宽屏并排两列，窄屏堆叠单列，两种布局都保持完整可见。"""
        if self.form_wide:
            padx = (16, 8) if index == 0 else (8, 16)
            return self._card(0, title, parent=self.form_row, column=index, padx=padx)
        return self._card(index, title, parent=self.form_row, column=0)

    def _build_header(self):
        bar = tk.Frame(self.content, bg=CARD)
        bar.grid(row=0, column=0, sticky="ew")
        tk.Frame(bar, bg=BORDER, height=1).pack(side="bottom", fill="x")
        inner = tk.Frame(bar, bg=CARD)
        inner.pack(fill="x", padx=16, pady=(10, 9))
        tk.Label(inner, textvariable=self.page_title, bg=CARD, fg=TX, font=(FONT, 15, "bold")).pack(side="left")
        ttk.Button(inner, text="对话", style="Ghost.TButton", command=self._toggle_chat).pack(side="right")
        ttk.Button(inner, text="AI 设置", command=self._open_ai_settings).pack(side="right", padx=(0, 6))
        self.ai_state = tk.StringVar(value="AI 未配置")
        tk.Label(inner, textvariable=self.ai_state, bg=CARD, fg=SEC, font=(FONT, 9), anchor="e").pack(side="right", padx=6)

    def _build_source(self):
        _outer, body, _head = self._form_card("① 数据源", 0)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="安装目录", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.install_var = tk.StringVar()
        ttk.Entry(body, textvariable=self.install_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(body, text="自动探测", command=self._autodetect).grid(row=0, column=2)
        ttk.Button(body, text="选择", command=self._choose_install).grid(row=0, column=3, padx=(6, 0))

        ttk.Label(body, text="项目", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.project_var = tk.StringVar()
        self.project_box = ttk.Combobox(body, textvariable=self.project_var, state="readonly")
        self.project_box.grid(row=1, column=1, sticky="ew", padx=8, pady=(8, 0))
        self.project_box.bind("<<ComboboxSelected>>", lambda _event: self._load_subjects())
        ttk.Button(body, text="扫描项目", command=self._scan_projects).grid(row=1, column=2, pady=(8, 0))

        line = tk.Frame(body, bg=CARD)
        line.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Label(line, text="主体单位", style="Card.TLabel").pack(side="left")
        self.subject_var = tk.StringVar()
        self.subject_box = ttk.Combobox(line, textvariable=self.subject_var, state="readonly", width=20)
        self.subject_box.pack(side="left", padx=8)
        self.subject_box.bind("<<ComboboxSelected>>", self._on_subject)
        ttk.Label(line, text="年度", style="Card.TLabel").pack(side="left")
        self.year_var = tk.StringVar()
        ttk.Combobox(line, textvariable=self.year_var, width=6,
                     values=[str(year) for year in range(2000, 2101)]).pack(side="left", padx=(8, 12))
        ttk.Label(line, text="截止月", style="Card.TLabel").pack(side="left")
        self.month_var = tk.StringVar()
        ttk.Combobox(line, textvariable=self.month_var, width=4,
                     values=[str(month) for month in range(1, 13)]).pack(side="left", padx=8)
        ttk.Button(line, text="读取科目余额", style="Accent.TButton",
                   command=self._read_ledger).pack(side="left", padx=(12, 0))
        self.ledger_state = tk.StringVar(value="")
        tk.Label(line, textvariable=self.ledger_state, bg=CARD, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=10)

    def _build_template(self):
        _outer, body, _head = self._form_card("② 模板与期间", 1)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="模板文件", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.template_var = tk.StringVar()
        ttk.Entry(body, textvariable=self.template_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(body, text="浏览", command=self._choose_template).grid(row=0, column=2)

        line = tk.Frame(body, bg=CARD)
        line.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(line, text="填列期间", style="Card.TLabel").pack(side="left")
        self.period_var = tk.StringVar(value="both")
        for text, value in (("仅期初", "opening"), ("仅期末", "ending"), ("期初+期末", "both")):
            ttk.Radiobutton(line, text=text, value=value, variable=self.period_var,
                            style="Card.TRadiobutton").pack(side="left", padx=(10, 0))
        self.unit_var = tk.StringVar(value="自动")
        unit_row = tk.Frame(body, bg=CARD) if self.form_wide else line
        if self.form_wide:
            unit_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))
            ttk.Label(unit_row, text="金额单位", style="Card.TLabel").pack(side="left")
        else:
            ttk.Label(line, text="金额单位", style="Card.TLabel").pack(side="left", padx=(18, 0))
        ttk.Combobox(unit_row, textvariable=self.unit_var, state="readonly", width=6,
                     values=("自动", "元", "千元", "万元", "亿元")).pack(side="left", padx=8)
        self.scan_state = tk.StringVar(value="")
        if self.form_wide:
            action = tk.Frame(body, bg=CARD)
            action.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
            ttk.Button(action, text="识别并匹配", style="Accent.TButton",
                       command=self._recognize).pack(side="left")
            tk.Label(action, textvariable=self.scan_state, bg=CARD, fg=MUTED,
                     font=(FONT, 8)).pack(side="left", padx=10)
        else:
            ttk.Button(line, text="识别并匹配", style="Accent.TButton",
                       command=self._recognize).pack(side="left", padx=(14, 0))
            tk.Label(line, textvariable=self.scan_state, bg=CARD, fg=MUTED,
                     font=(FONT, 8)).pack(side="left", padx=10)

    def _build_result(self):
        outer, body, _head = self._card(1, "③ 识别结果")
        outer.grid_configure(sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        bar = tk.Frame(body, bg=CARD)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        controls = tk.Frame(bar, bg=CARD)
        controls.pack(fill="x")
        self.filter_var = tk.StringVar(value="全部")
        chooser = ttk.Combobox(controls, textvariable=self.filter_var, state="readonly", width=9,
                               values=("全部", "可写入", "已匹配", "待确认", "未匹配", "公式保护", "已选中"))
        chooser.pack(side="left")
        chooser.bind("<<ComboboxSelected>>", lambda _event: self._refresh_tree())
        for text, command in (("全选可写入", self._select_all_fillable), ("清空", self._clear_selection),
                              ("允许覆盖", self._allow_overwrite), ("指定来源", self._manual_source),
                              ("变动分析", self._analyze_movements)):
            ttk.Button(controls, text=text, command=command).pack(side="left", padx=(6, 0))
        self.ai_btn = ttk.Button(controls, text="AI 复核", command=self._ai_review)
        self.ai_btn.pack(side="left", padx=(6, 0))
        self.stats = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.stats, bg=CARD, fg=MUTED, font=(FONT, 8), anchor="w").pack(
            fill="x", pady=(7, 0))

        columns = ("sel", "status", "cell", "label", "period", "amount", "actual", "diff", "sources", "reason")
        headings = {"sel": "✓", "status": "状态", "cell": "单元格", "label": "报表项目", "period": "期间",
                    "amount": "应填值", "actual": "现有值", "diff": "差额", "sources": "来源科目",
                    "reason": "方法 / 原因"}
        widths = {"sel": 28, "status": 62, "cell": 108, "label": 150, "period": 44, "amount": 88,
                  "actual": 88, "diff": 76, "sources": 132, "reason": 180}
        anchors = {"sel": "center", "status": "center", "cell": "center", "period": "center",
                   "amount": "e", "actual": "e", "diff": "e"}
        self.tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
        for key in columns:
            self.tree.heading(key, text=headings[key])
            self.tree.column(key, width=widths[key], minwidth=widths[key] - 20,
                             anchor=anchors.get(key, "w"),
                             stretch=key in ("label", "reason", "sources"))
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        hscroll = ttk.Scrollbar(body, orient="horizontal", command=self.tree.xview)
        hscroll.grid(row=2, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=hscroll.set)
        self.tree.tag_configure("ok", background="#f7fcfa", foreground="#285d4c")
        self.tree.tag_configure("warn", background="#fffaeb", foreground="#7a4d00")
        self.tree.tag_configure("bad", background="#fdf1f0", foreground="#7a1c16")
        self.tree.tag_configure("info", background="#f2f6fd", foreground="#1b3a6b")
        self.tree.tag_configure("muted", background="#f7f8fa", foreground="#4b5563")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)
        self.tree.bind("<Double-1>", lambda _event: self._show_detail())

    def _build_actions(self):
        bar = tk.Frame(self.workspace, bg=BG)
        bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 0))
        self.report_btn = ttk.Button(bar, text="导出报告", command=self._export_report, state="disabled")
        self.report_btn.pack(side="left")
        self.write_btn = ttk.Button(bar, text="另存 Excel", style="Accent.TButton",
                                    command=self._write_excel, state="disabled")
        self.write_btn.pack(side="left", padx=8)
        ttk.Button(bar, text="校验日志", command=self._verify_audit).pack(side="left")
        ttk.Button(bar, text="退出", command=self._request_close).pack(side="right")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=120)
        self.status_var = tk.StringVar(value="就绪")

    def _build_log(self):
        outer = tk.Frame(self.workspace, bg=CARD, highlightbackground=BORDER, highlightthickness=1, bd=0)
        outer.grid(row=3, column=0, sticky="ew", padx=16, pady=(8, 12))
        outer.columnconfigure(0, weight=1)
        self.log = tk.Text(outer, height=2, wrap="none", state="disabled", font=("Consolas", 8),
                           background=CARD, foreground=SEC, relief="flat", borderwidth=0, padx=10, pady=8)
        self.log.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(outer, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

    # ---------- 对话面板 ----------

    def _build_chat(self):
        outer = tk.Frame(self, bg=CARD, highlightbackground="#cfd5dd", highlightthickness=1, bd=0)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        self.chat_frame = outer
        self.chat_history = []
        self._chat_proposals = []

        head = tk.Frame(outer, bg=CARD)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 6))
        tk.Label(head, image=self.whale_small, bg=CARD, bd=0).pack(side="left", padx=(0, 7))
        tk.Label(head, text="审计对话", bg=CARD, fg=ACC, font=(FONT, 9, "bold")).pack(side="left")
        ttk.Button(head, text="×", style="Ghost.TButton", width=2,
                   command=self._toggle_chat).pack(side="right")

        self.chat_view = tk.Text(outer, state="disabled", wrap="word", relief="flat", borderwidth=0,
                                 bg=CARD, fg=TX, font=(FONT, 9), padx=14, pady=2, cursor="arrow")
        self.chat_view.grid(row=1, column=0, sticky="nsew")
        self.chat_view.tag_configure("me", foreground=ACC, font=(FONT, 9, "bold"), spacing3=3)
        self.chat_view.tag_configure("ai", foreground=TX, spacing3=3, lmargin2=14)
        self.chat_view.tag_configure("sys", foreground=MUTED, font=(FONT, 8), spacing3=3)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=self.chat_view.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.chat_view.configure(yscrollcommand=scroll.set)

        self.prop_frame = tk.Frame(outer, bg=CARD)
        self.prop_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 0))
        self.prop_frame.columnconfigure(0, weight=1)
        self.prop_tree = ttk.Treeview(self.prop_frame, columns=("sel", "target", "sources"),
                                      show="headings", height=4, selectmode="browse")
        for key, title, width in (("sel", "✓", 30), ("target", "目标", 100), ("sources", "来源", 130)):
            self.prop_tree.heading(key, text=title)
            self.prop_tree.column(key, width=width, anchor="center" if key == "sel" else "w")
        self.prop_tree.grid(row=0, column=0, sticky="ew")
        self.prop_tree.bind("<Button-1>", self._on_prop_click)
        prop_bar = tk.Frame(self.prop_frame, bg=CARD)
        prop_bar.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(prop_bar, text="应用所选", style="Accent.TButton",
                   command=self._apply_proposals).pack(side="left")
        self.prop_frame.grid_remove()

        box = tk.Frame(outer, bg=CARD)
        box.grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(10, 12))
        box.columnconfigure(0, weight=1)
        self.chat_input = tk.Text(box, height=3, wrap="word", relief="flat", borderwidth=0,
                                  bg="#f7f8fa", fg=TX, font=(FONT, 9), padx=10, pady=8,
                                  highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACC)
        self.chat_input.grid(row=0, column=0, sticky="ew")
        self.chat_input.bind("<Return>", self._on_chat_return)
        btns = tk.Frame(box, bg=CARD)
        btns.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(btns, text="发送", style="Accent.TButton", command=self._chat_send).pack(side="right")
        ttk.Button(btns, text="清空", command=self._chat_clear).pack(side="right", padx=8)

    def _toggle_chat(self):
        if self.chat_visible:
            self.chat_frame.place_forget()
            self.chat_visible = False
            return
        height = min(620, max(420, self.winfo_height() - 120))
        self.chat_frame.place(relx=1.0, rely=1.0, anchor="se", x=-16, y=-14, width=340, height=height)
        self.chat_frame.lift()
        self.chat_visible = True
        self.chat_input.focus_set()

    def _on_chat_return(self, event):
        if event.state & 0x0001:
            return None
        self._chat_send()
        return "break"

    def _chat_append(self, role, text):
        prefix = {"me": "我\n", "ai": "AI\n", "sys": "· "}[role]
        self.chat_view.configure(state="normal")
        self.chat_view.insert("end", prefix, role)
        self.chat_view.insert("end", text.strip() + "\n\n", role)
        self.chat_view.configure(state="disabled")
        self.chat_view.see("end")

    def _chat_clear(self):
        self.chat_view.configure(state="normal")
        self.chat_view.delete("1.0", "end")
        self.chat_view.configure(state="disabled")
        self.chat_history = []
        self._chat_proposals = []
        self.prop_frame.grid_remove()

    def _analyze_movements(self):
        """变动分析：本地先出确定性摘要，启用 AI 时再让其解释同一批事实。"""
        if not self.rows:
            self._chat_append("sys", "先读取科目余额")
            if not self.chat_visible:
                self._toggle_chat()
            return
        try:
            facts = analysis.build_facts(self.rows)
        except ValueError as exc:
            if not self.chat_visible:
                self._toggle_chat()
            self._chat_append("sys", str(exc))
            return
        if not self.chat_visible:
            self._toggle_chat()
        period = facts["subject"]
        self._chat_append("sys", f"{period['year']}-{period['month']:02d} · 变动前 {len(facts['movements'])} 项")
        self._chat_append("sys", analysis.summarize(facts))
        if self.store is None or not self.store.has_key or not self.store.data.get("ai_enabled"):
            return
        self._chat_send("请依据给定账套事实，说明主要变动的原因方向与需要追加核查的事项。"
                        "只能引用事实中的科目与金额，不确定就说明缺少哪些资料。", extra={"facts": facts})

    def _chat_send(self, question=None, extra=None):
        if self.busy:
            return
        text = (self.chat_input.get("1.0", "end") if question is None else question).strip()
        if not text:
            return
        if self.store is None or not self.store.has_key:
            self._chat_append("sys", "未配置 Key")
            return
        if not self.store.data.get("ai_enabled"):
            self._chat_append("sys", "AI 未启用")
            return
        if ai_module.audit_scope(text) == "offtopic":
            self._chat_append("sys", "本通道用于审计任务，未发送离题问题。")
            return
        try:
            candidates = list(self.rows[:300])
            context = ai_module.assistance_payload(
                [result for result in self.results if result.status != "protected"][:30], candidates)
            context["scope"] = {"total_targets": len(self.results), "total_accounts": len(self.rows),
                                "target_limit": 30, "candidate_limit": 300,
                                "note": "限本次发送范围，不代表完整账套、全表或全部审计程序已完成。"}
            if self.rows:
                context["facts"] = analysis.build_facts(self.rows)
            context["knowledge_refs"] = deepcopy(self.knowledge_refs)
            if extra:
                context = {**context, **extra}
            client = ai_module.DeepSeekClient.from_store(self.store)
        except (ai_module.AIError, settings.SettingsError, ValueError) as exc:
            self._chat_append("sys", str(exc))
            return
        history = list(self.chat_history[-8:])
        dialog = PayloadDialog(self, {**context, "question": text, "history": history}, client.model, client.base_url)
        self.wait_window(dialog)
        if not dialog.confirmed:
            return
        if question is None:
            self.chat_input.delete("1.0", "end")
        self._chat_append("me", text)
        detail = f"附 {len(context['targets'])} 个字段 / {len(context['candidates'])} 个科目"
        if extra and extra.get("facts"):
            detail += f" / {len(extra['facts']['movements'])} 项变动"
        self._chat_append("sys", detail)
        self.chat_history.append({"role": "user", "content": text})

        def work():
            try:
                # 先检索内置的会计与 IPO 知识库，命中条目以 K 编号注入后再回答。
                return client.converse_with_skills(text, history, context)
            except ai_module.AIError as exc:
                return {"intent": "error", "answer": str(exc), "proposals": []}

        self._async("对话请求中…", work, self._on_chat_answer)

    def _on_chat_answer(self, answer):
        if answer.get("intent") == "error":
            self._chat_append("sys", answer["answer"])
            self.chat_history.pop()
            return
        self._chat_append("ai", answer["answer"])
        self.chat_history.append({"role": "assistant", "content": answer["answer"]})
        hits = answer.get("knowledge_refs") or []
        if hits:
            self._chat_append("sys", "依据内置知识库：" + " · ".join(
                f"[{item['citation_id']}] {item['title'][:26]}" for item in hits[:5]))
        for value in (answer.get("warnings") or [])[:5]:
            self._chat_append("sys", f"⚠ 事实中无此金额：{value}")
        for code in (answer.get("unknown_codes") or [])[:5]:
            self._chat_append("sys", f"⚠ 账套中无此编码：{code}")
        for warning in answer.get("citation_warnings", []):
            self._chat_append("sys", "⚠ " + warning)
        if answer.get("intent") == "offtopic":
            return
        self._set_chat_proposals(answer.get("proposals", []))

    def _set_knowledge_refs(self, refs):
        if not 1 <= len(refs) <= 5 or any(not item.get("excerpt") for item in refs):
            raise ValueError("请选择 1–5 份含正文的知识片段。")
        self.knowledge_refs = [{"citation_id": f"K{index + 1}", "title": str(item["title"])[:200],
                                "source": str(item["source"])[:30], "locator": str(item["locator"])[:700],
                                "excerpt": str(item["excerpt"])[:1500]} for index, item in enumerate(refs)]
        self._context_revision += 1
        self._chat_clear()
        workspace.refresh_context(self)

    def _attach_review_document(self, role, after=None):
        if self.busy:
            return
        path = filedialog.askopenfilename(title=f"选择待复核{role}，不会加入参考知识库", filetypes=[
            ("待复核资料", "*.txt *.md *.docx *.pdf")])
        if not path:
            return

        def done(value):
            text, digest = value
            self.review_document = {"role": role, "name": Path(path).name, "sha256": digest,
                                    "text": text[:12000], "truncated": len(text) > 12000,
                                    "scope": "仅提取文字；不包含图片、扫描内容、版式或未发送部分。"}
            self._context_revision += 1
            self._chat_clear()
            workspace.refresh_context(self)
            if after:
                after()
        self._async(f"正在读取待复核{role}（仅本机）…", lambda: knowledge.read_document(path), done)

    def _agent_review(self, role="TB"):
        if self.busy:
            return
        # 知识库已内置并在对话中自动检索，无需先手工选用条目。
        if role == "TB" and not self.results:
            workspace.navigate(self, "tb")
            messagebox.showinfo("缺少 TB", "请先读取账套并识别模板，再进行 TB 知识库复核。")
            return
        if role in {"附注", "底稿", "报告"} and (not self.review_document or self.review_document["role"] != role):
            self._attach_review_document(role, after=lambda: self._agent_review(role))
            return
        if not self.chat_visible:
            self._toggle_chat()
        extra = {"review_role": role}
        if role in {"附注", "底稿", "报告"}:
            extra["review_document"] = deepcopy(self.review_document)
        self._chat_send(f"请辅助复核本次提供的{role}。逐项说明问题、涉及字段或文字、引用的知识编号[K1]及来源适用性、"
                        "需要补充的证据和建议。金额只引用本地事实，不自行改写；没有资料不能判定通过。"
                        "若仅发送片段或缺少TB，明确限制，不声称完成全表勾稽、全部审计程序或正式报告签发。", extra=extra)

    def _set_chat_proposals(self, proposals):
        self._chat_proposals = list(proposals)
        for item in self.prop_tree.get_children():
            self.prop_tree.delete(item)
        if not proposals:
            self.prop_frame.grid_remove()
            return
        for index, proposal in enumerate(proposals):
            self.prop_tree.insert("", "end", iid=str(index),
                                  values=("☑", proposal["target_id"], "; ".join(proposal["source_codes"])))
        self.prop_frame.grid()

    def _on_prop_click(self, event):
        if self.prop_tree.identify_region(event.x, event.y) != "cell":
            return None
        if self.prop_tree.identify_column(event.x) != "#1":
            return None
        iid = self.prop_tree.identify_row(event.y)
        if iid:
            current = self.prop_tree.set(iid, "sel")
            self.prop_tree.set(iid, "sel", "☐" if current == "☑" else "☑")
        return "break"

    def _apply_proposals(self):
        if self.busy or self.scan is None:
            return
        picked = [self._chat_proposals[int(iid)] for iid in self.prop_tree.get_children()
                  if self.prop_tree.set(iid, "sel") == "☑"]
        if not picked:
            self._chat_append("sys", "未勾选提案")
            return
        targets = {result.target.id: result for result in self.results}
        subset = [targets[item["target_id"]] for item in picked if item["target_id"] in targets]
        if not subset:
            self._chat_append("sys", "提案目标已失效")
            return
        applied, notes = matching.apply_ai_suggestions(subset, picked, self.rows)
        matching.validate_claims(self.results)
        self._refresh_tree()
        self._chat_append("sys", f"已应用 {applied} 条，待确认 {len(notes)} 条")
        for note in notes[:3]:
            self._chat_append("sys", note)
        self._audit("ai_proposal_applied", count=applied,
                    items=[{"cell": item["target_id"], "sources": item["source_codes"]} for item in picked])
        self.prop_frame.grid_remove()
        self._chat_proposals = []

    # ---------- 线程与日志 ----------

    def _pump(self):
        try:
            while True:
                self.queue.get_nowait()()
        except queue.Empty:
            pass
        except Exception as exc:
            self._set_busy(False)
            self._log(f"界面回调失败：{exc}", error=True)
        finally:
            self.after(80, self._pump)

    def _post(self, action):
        self.queue.put(action)

    def _log(self, message, error=False):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{stamp}] {'!! ' if error else ''}{message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.status_var.set(message if len(message) < 60 else message[:57] + "…")

    def _thread_log(self, message):
        self._post(lambda: self._log(message))

    def _set_busy(self, busy, text=""):
        self.busy = busy
        if busy:
            self.progress.pack(side="right", padx=(0, 10))
            self.progress.start(12)
            self.status_var.set(text)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def _async(self, text, work, done=None, *, guard_context=True):
        if self.busy:
            messagebox.showinfo("请稍候", "当前任务尚未完成。")
            return
        self._set_busy(True, text)
        self._log(text)
        revision = self._context_revision if guard_context else None

        def runner():
            try:
                value = work()
            except Exception as exc:
                detail = traceback.format_exc(limit=3)
                self._post(lambda exc=exc, detail=detail: self._finish_error(text, exc, detail))
            else:
                self._post(lambda: self._finish_ok(text, value, done, revision))

        threading.Thread(target=runner, daemon=True).start()

    def _finish_error(self, text, exc, detail):
        self._set_busy(False)
        self._log(f"{text}失败：{exc}", error=True)
        for line in detail.strip().splitlines()[-2:]:
            self._log("  " + line.strip(), error=True)
        messagebox.showerror("操作未完成", str(exc))

    def _finish_ok(self, text, value, done, revision=None):
        self._set_busy(False)
        if revision is not None and revision != self._context_revision:
            self._log("操作期间参数已变化，已丢弃旧任务结果，请重新操作。")
            return
        try:
            if done:
                done(value)
        except Exception as exc:
            self._log(f"{text}后处理失败：{exc}", error=True)
            messagebox.showerror("操作未完成", str(exc))

    # ---------- 数据源 ----------

    def _choose_install(self):
        if self.busy:
            return
        path = filedialog.askdirectory(title="选择鼎信诺安装或服务目录（含 bin\\mysqld.exe）")
        if path:
            self.install_var.set(path)
            self._scan_projects()

    def _autodetect(self):
        self._async("正在探测鼎信诺安装目录…", dxn.discover_installations, self._on_installations)

    def _on_installations(self, found):
        if not found:
            self._log("未发现鼎信诺安装目录，请用“选择…”指定 bin 的上级目录。")
            return
        self.install_var.set(str(found[0]))
        self._log(f"已定位鼎信诺服务目录：{found[0]}")
        self._scan_projects()

    def _scan_projects(self):
        path = self.install_var.get().strip()
        self._async("正在扫描鼎信诺项目…", lambda: dxn.scan_projects(path), self._on_projects)

    def _on_projects(self, projects):
        self.projects = list(projects)
        self.project_box.configure(values=[str(item) for item in self.projects])
        if not self.projects:
            self._log("未找到含科目余额数据库的项目目录。")
            return
        self._log(f"发现 {len(self.projects)} 个项目。")
        self.project_box.current(0)
        self._load_subjects()

    def _load_subjects(self):
        index = self.project_box.current()
        if index < 0 or index >= len(self.projects):
            return
        project = self.projects[index]
        self._async("正在读取项目底稿信息…", lambda: dxn.list_subjects(str(project)), self._on_subjects)

    def _on_subjects(self, subjects):
        self.subjects = list(subjects)
        labels = [item.label for item in self.subjects]
        self.subject_box.configure(values=labels)
        if not labels:
            self._log("该项目未识别到可用主体。")
            return
        self._log(f"识别到 {len(labels)} 个主体 / 期间。")
        self.subject_box.current(0)
        self._on_subject()

    def _on_subject(self, _event=None):
        index = self.subject_box.current()
        if index < 0 or index >= len(self.subjects):
            return
        self.current_subject = self.subjects[index]
        self.year_var.set(str(self.current_subject.year))
        self.month_var.set(str(self.current_subject.month))

    def _read_ledger(self):
        if self.busy:
            return
        if self.current_subject is None:
            messagebox.showwarning("缺少主体", "请先选择主体单位。")
            return
        try:
            year = int(self.year_var.get())
            month = int(self.month_var.get())
        except ValueError:
            messagebox.showwarning("期间不完整", "请选择有效的年度与截止月份。")
            return
        if not (1900 <= year <= 2200 and 1 <= month <= 12):
            messagebox.showwarning("期间不合法", "年度需在 1900–2200，月份需在 1–12。")
            return
        subject = Subject(self.current_subject.id, self.current_subject.name, year, month,
                          self.current_subject.project_path, self.current_subject.source_path,
                          self.current_subject.ledger_id)
        install = self.install_var.get().strip()
        self._invalidate_review(clear_ledger=True)
        self._async("正在校验并复制科目余额（原库只读）…",
                    lambda: dxn.read_ledger(subject, install, self._thread_log),
                    lambda rows: self._on_ledger(rows, subject))

    def _on_ledger(self, rows, subject):
        self._invalidate_review()
        self.rows = list(rows)
        self.loaded_subject = subject
        if not self.rows:
            self.ledger_state.set("未读到科目")
            self._log("未读到任何科目记录。")
            return
        self.ledger_state.set(f"已读取 {len(self.rows)} 个科目")
        self._log(f"科目余额读取完成：{len(self.rows)} 个科目，"
                  f"编码 {self.rows[0].code}–{self.rows[-1].code}。")
        workspace.refresh_context(self)

    # ---------- 模板识别 ----------

    def _choose_template(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title="选择用户模板", filetypes=[
            ("Excel 模板", "*.xlsx *.xlsm"), ("所有文件", "*.*")])
        if not path:
            return
        self.template_var.set(path)
        self.scan = None
        self.scan_state.set("模板已更换，请重新识别")
        # 科目余额已就绪时换模板直接重算，省去再点一次“识别并匹配”。
        if self.rows and not self.busy:
            self._recognize()

    def _periods(self):
        value = self.period_var.get()
        return ("opening", "ending") if value == "both" else (value,)

    def _recognize(self):
        if self.busy:
            return
        path = self.template_var.get().strip()
        if not path:
            messagebox.showwarning("缺少模板", "请先选择模板文件。")
            return
        if not self.rows:
            messagebox.showwarning("缺少科目余额", "请先读取鼎信诺科目余额。")
            return
        unit = self.unit_var.get()
        periods = self._periods()
        rows = tuple(self.rows)
        self._invalidate_review()

        def work():
            scan = fields.scan_template(path, periods, unit)
            workbook.validate_source_context(scan, [], rows)
            scan.source_digest = ledger_digest(rows)
            return scan, matching.match_fields(scan.fields, list(rows))

        self._recognize_started = time.perf_counter()
        self._async("正在识别模板字段并匹配…", work, self._on_scanned)

    def _on_scanned(self, value):
        scan, results = value
        self.scan = scan
        self.results = results
        elapsed = time.perf_counter() - getattr(self, "_recognize_started", time.perf_counter())
        self.scan_state.set(f"{len(self.results)} 个字段，单位：{scan.unit}，{elapsed:.2f} 秒")
        self._log(f"模板识别完成：{len(self.results)} 个字段，金额单位 {scan.unit}，"
                  f"匹配 {len(self.rows)} 个科目，耗时 {elapsed:.2f} 秒。")
        for warning in scan.warnings:
            self._log("提示：" + warning)
        self._refresh_tree()
        self.report_btn.configure(state="normal")
        self.write_btn.configure(state="normal")

    # ---------- 结果表 ----------

    def _visible_results(self):
        mode = self.filter_var.get()
        if mode == "全部":
            return list(enumerate(self.results))
        if mode == "可写入":
            return [(i, r) for i, r in enumerate(self.results)
                    if r.amount is not None and r.status not in {"protected"}]
        if mode == "已选中":
            return [(i, r) for i, r in enumerate(self.results) if r.selected]
        wanted = {key for key, label in STATUS_LABEL.items() if label == mode}
        return [(i, r) for i, r in enumerate(self.results) if r.status in wanted]

    def _row_values(self, result):
        target = result.target
        return (
            "☑" if result.selected else "☐",
            STATUS_LABEL.get(result.status, result.status),
            target.id,
            target.label,
            "期初" if target.period == "opening" else "期末",
            fmt_amount(result.amount),
            fmt_amount(target.value) if not target.is_formula else "公式",
            fmt_amount(result.difference),
            "; ".join(f"{row.code} {row.name}" for row in result.sources),
            (result.method + " · " if result.method else "") + (result.reason or ""),
        )

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for index, result in self._visible_results():
            self.tree.insert("", "end", iid=str(index), values=self._row_values(result),
                             tags=(STATUS_TAG.get(result.status, "muted"),))
        self._refresh_stats()

    def _refresh_row(self, iid):
        try:
            result = self.results[int(iid)]
        except (ValueError, IndexError):
            return
        if self.tree.exists(iid):
            self.tree.item(iid, values=self._row_values(result),
                           tags=(STATUS_TAG.get(result.status, "muted"),))

    def _refresh_stats(self):
        workspace.refresh_context(self)
        if not self.results:
            self.stats.set("")
            return
        total = len(self.results)
        counts = {key: 0 for key in STATUS_LABEL}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        selected = sum(1 for result in self.results if result.selected)
        overwrite = sum(1 for result in self.results if result.selected and result.overwrite)
        self.stats.set(f"共 {total} 项｜已匹配 {counts['matched']}｜待确认 {counts['ambiguous']}｜"
                       f"未匹配 {counts['unmatched']}｜缺少依据 {counts['missing']}｜公式 {counts['protected']}｜"
                       f"已勾选 {selected}（其中覆盖 {overwrite}）")

    def _current_result(self):
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return self.results[int(selection[0])]
        except (ValueError, IndexError):
            return None

    def _on_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        if self.tree.identify_column(event.x) != "#1":
            return None
        iid = self.tree.identify_row(event.y)
        if iid:
            self._toggle(iid)
        return "break"

    def _on_space(self, _event):
        result = self._current_result()
        if result is not None:
            self._toggle(str(self.results.index(result)))
        return "break"

    def _toggle(self, iid):
        if self.busy:
            return
        try:
            result = self.results[int(iid)]
        except (ValueError, IndexError):
            return
        if result.status == "protected" or result.amount is None:
            self._log(f"{result.target.id} 无可用金额，不能勾选（{result.reason or '公式或缺少依据'}）。")
            return
        if result.selected:
            result.selected = False
            result.overwrite = False
        else:
            if has_value(result.target.value):
                if not messagebox.askyesno(
                        "覆盖确认",
                        f"{result.target.id} 已有值 {result.target.value}，应填 {fmt_amount(result.amount)}。\n"
                        "确定允许覆盖该已有值？"):
                    return
                result.overwrite = True
            result.selected = True
        self._refresh_row(iid)
        self._refresh_stats()

    def _select_all_fillable(self):
        if self.busy:
            return
        added = 0
        for result in self.results:
            if result.selected or result.amount is None or result.status == "protected":
                continue
            if has_value(result.target.value):
                continue
            result.selected = True
            added += 1
        self._refresh_tree()
        self._log(f"已勾选 {added}")

    def _clear_selection(self):
        if self.busy:
            return
        for result in self.results:
            result.selected = False
            result.overwrite = False
        self._refresh_tree()
        self._log("已清空")

    def _allow_overwrite(self):
        if self.busy:
            return
        selection = self.tree.selection()
        result = self._current_result()
        if result is None or not result.selected:
            messagebox.showinfo("请先选择", "请在表格中选中一行已勾选的结果。")
            return
        if not has_value(result.target.value):
            messagebox.showinfo("无需覆盖", "该单元格当前为空。")
            return
        result.overwrite = True
        self._refresh_row(selection[0])
        self._refresh_stats()
        self._log(f"{result.target.id} 已标记允许覆盖。")

    def _show_detail(self):
        result = self._current_result()
        if result is None:
            return
        lines = [f"目标：{result.target.id}（{result.target.label}）",
                 f"期间：{'期初' if result.target.period == 'opening' else '期末'}　"
                 f"口径：{'审定' if result.target.basis == 'audited' else '账面'}　"
                 f"口径类型：{result.target.measure}",
                 f"状态：{STATUS_LABEL.get(result.status, result.status)}　方法：{result.method or '-'}",
                 f"应填值：{fmt_amount(result.amount)}　现有值：{fmt_amount(result.target.value)}　"
                 f"差额：{fmt_amount(result.difference)}",
                 "", "来源科目："]
        for row in result.sources:
            lines.append(f"  {row.code} {row.name}（{row.path or row.name}）期初 {fmt_amount(row.opening)} "
                         f"期末 {fmt_amount(row.ending)} 借 {fmt_amount(row.debit)} 贷 {fmt_amount(row.credit)}")
        lines += ["", "原因：" + (result.reason or "-")]
        messagebox.showinfo(f"明细 · {result.target.id}", "\n".join(lines))

    # ---------- 人工指定来源 ----------

    def _manual_source(self):
        if self.busy or self.scan is None:
            return
        result = self._current_result()
        if result is None:
            messagebox.showinfo("请先选择", "请在表格中选中一个目标字段。")
            return
        if result.status == "protected":
            messagebox.showinfo("公式受保护", "该单元格为公式，不允许写入。")
            return
        dialog = SourceDialog(self, result, self.rows)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        check = matching.resolve_match(result.target, dialog.result, method="manual", rows=self.rows)
        result.sources, result.amount = check.sources, check.amount
        result.status, result.method, result.reason = check.status, check.method, check.reason
        result.selected, result.overwrite = check.selected, False
        matching.validate_claims(self.results)
        self._refresh_tree()
        self._log(f"{result.target.id} 已人工指定来源：{', '.join(check.source_ids) or '无'}（{check.status}）")

    # ---------- AI ----------

    def _sync_ai_state(self):
        if self.store is None:
            self.ai_state.set("AI：设置不可用")
            self.ai_btn.configure(state="disabled")
            return
        has_key = self.store.has_key
        enabled = bool(self.store.data.get("ai_enabled"))
        model = self.store.data.get("model", "—")
        model = model if len(model) <= 18 else model[:17] + "…"
        if has_key and enabled:
            self.ai_state.set(f"AI 已启用 · {model}")
            self.ai_btn.configure(state="normal")
        elif has_key:
            self.ai_state.set("AI 已配置 · 未启用")
            self.ai_btn.configure(state="disabled")
        else:
            self.ai_state.set("AI：未配置（可离线使用）")
            self.ai_btn.configure(state="disabled")

    def _open_ai_settings(self):
        if self.busy:
            return
        if self.store is None:
            messagebox.showerror("设置不可用", self._pending_error or "本机设置文件不可读。")
            return
        dialog = AISettingsDialog(self, self.store)
        self.wait_window(dialog)
        self._sync_ai_state()

    def _ai_review(self):
        if self.busy:
            return
        if self.store is None or not self.store.has_key:
            messagebox.showinfo("未配置 Key", "请先在“AI 设置”中填入自己的 DeepSeek API Key。")
            return
        if not self.store.data.get("ai_enabled"):
            messagebox.showinfo("AI 未启用", "请先在 AI 设置中启用。")
            return
        if not self.results:
            messagebox.showinfo("无结果", "请先识别模板。")
            return
        subset = [result for result in self.results
                  if result.status in {"unmatched", "ambiguous", "duplicate", "missing"}][:30]
        if not subset:
            messagebox.showinfo("无需复核", "当前没有待确认或未匹配的字段。")
            return
        try:
            payload = ai_module.assistance_payload(subset, self.rows)
            client = ai_module.DeepSeekClient.from_store(self.store)
        except (ai_module.AIError, settings.SettingsError) as exc:
            messagebox.showwarning("范围过大", str(exc))
            return
        dialog = PayloadDialog(self, payload, client.model, client.base_url)
        self.wait_window(dialog)
        if not dialog.confirmed:
            return
        self._ai_subset = subset
        self._async(f"正在请求模型复核 {len(subset)} 个字段…",
                    lambda: client.assist(payload),
                    self._on_ai_answer)

    def _on_ai_answer(self, answer):
        applied, notes = matching.apply_ai_suggestions(self._ai_subset, answer.get("suggestions", []), self.rows)
        matching.validate_claims(self.results)
        self._refresh_tree()
        self._log(f"AI 复核：{answer.get('summary', '').strip()}")
        self._log(f"AI 采纳 {applied} 条建议（金额仍由本地规则计算）。未采纳 {len(notes)} 条。")
        for note in notes[:5]:
            self._log("  未采纳：" + note)
        messagebox.showinfo("AI 复核完成", f"{answer.get('summary', '').strip()}\n\n"
                                          f"采纳建议：{applied} 条\n未采纳：{len(notes)} 条")

    # ---------- 输出 ----------

    def _export_report(self):
        if self.busy or not self.results or self.scan is None:
            return
        try:
            workbook.validate_source_context(self.scan, self.results, self.rows)
        except ValueError as exc:
            messagebox.showerror("结果已失效", str(exc))
            return
        path = filedialog.asksaveasfilename(title="导出复核报告", defaultextension=".csv",
                                            filetypes=[("CSV 报告", "*.csv")],
                                            initialfile="TB复核报告.csv")
        if not path:
            return
        subject = self._subject_label()
        unit = self.scan.unit if self.scan else "元"
        try:
            target = workbook.export_report(Path(path), self.results, subject=subject, unit=unit,
                                            warnings=self.scan.warnings if self.scan else ())
        except FileExistsError:
            messagebox.showerror("文件已存在", "目标文件已存在，请更换文件名。")
            return
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self._log(f"复核报告已导出：{target}")
        self._audit("export_report", path=str(target), subject=subject, unit=unit,
                    fields=len(self.results), selected=sum(1 for r in self.results if r.selected))
        messagebox.showinfo("已导出", str(target))

    def _subject_label(self):
        if self.loaded_subject is None:
            return ""
        return f"{self.loaded_subject.name} / {self.loaded_subject.year}-{self.loaded_subject.month:02d}"

    def _write_excel(self):
        if self.busy or not self.results or self.scan is None:
            return
        selected = [result for result in self.results if result.selected]
        if not selected:
            messagebox.showwarning("尚未勾选", "请先勾选需要写入的字段。")
            return
        suffix = self.scan.path.suffix
        path = filedialog.asksaveasfilename(title="另存为新的 Excel 文件", defaultextension=suffix,
                                            filetypes=[("Excel 工作簿", f"*{suffix}")],
                                            initialfile=f"{self.scan.path.stem}-已填列{suffix}")
        if not path:
            return
        scan, results, rows = deepcopy(self.scan), deepcopy(self.results), tuple(self.rows)
        subject = self._subject_label()
        self._async(f"正在写入 {len(selected)} 个字段（调用 Excel 重算并逐格校验）…",
                    lambda: workbook.write_results(scan, results, path, rows=rows),
                    lambda output: self._on_written(output, scan, results, subject), guard_context=False)

    def _on_written(self, output, scan, results, subject):
        self._log(f"已另存：{output}")
        self._audit("write_excel", output=str(output), template=str(scan.path),
                    template_digest=scan.digest, subject=subject, unit=scan.unit,
                    cells=[{"cell": result.target.id,
                            "before": "公式" if result.target.is_formula else fmt_amount(result.target.value),
                            "after": str(result.amount), "overwrite": result.overwrite,
                            "sources": result.source_ids}
                           for result in results if result.selected])
        messagebox.showinfo("写入完成", str(output))

    def _audit(self, event, **fields):
        if self.audit is None:
            self._log("审计日志不可用，本次操作未留痕。", error=True)
            return
        try:
            record = self.audit.append(event, **fields)
        except Exception as exc:
            self._log(f"审计日志写入失败：{exc}", error=True)
            messagebox.showerror("操作未完整留痕", "业务操作可能已完成，但审计日志写入失败。请保留输出和现场，核对日志后再继续。")
            return
        ok, seq, reason = self.audit.verify()
        self._log(f"审计日志已记录（序号 {record['seq']}）：{self.audit.path}")
        if not ok:
            self._log(f"警告：日志链在序号 {seq} 处校验失败（{reason}），请立即核对", error=True)

    def _verify_audit(self):
        if self.audit is None:
            messagebox.showerror("不可用", "审计日志未初始化。")
            return
        ok, seq, reason = self.audit.verify()
        total = len(self.audit.records())
        if ok:
            status = reason or "哈希链与本机末尾检查点均通过"
            messagebox.showinfo("审计日志校验", f"共 {total} 条记录：{status}。\n\n{self.audit.path}")
        else:
            messagebox.showerror("审计日志校验", f"第 {seq} 条记录异常：{reason}\n\n{self.audit.path}\n"
                                             "请保留现场并人工核对，不要覆盖原文件。")


class PayloadDialog(tk.Toplevel):
    """上传 AI 的内容必须可预览、可取消。"""

    def __init__(self, master, payload, model, destination=""):
        super().__init__(master)
        self.title("AI 复核 · 上传内容预览")
        self.transient(master)
        self.grab_set()
        self.resizable(True, True)
        self.confirmed = False
        self.geometry("760x560")
        self.configure(bg=BG)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        tk.Label(self, text=f"{len(payload['targets'])} 个目标 · {len(payload['candidates'])} 个候选 · {model}\n发送至：{destination or '当前配置的模型服务'} · 仅预览中所示范围",
                 bg=BG, fg=SEC, font=(FONT, 9)).grid(row=0, column=0, sticky="w", padx=PAD, pady=(PAD, 6))

        frame = ttk.Frame(self, padding=(PAD, 0, PAD, 0))
        frame.grid(row=1, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text = tk.Text(frame, wrap="none", font=("Consolas", 9), background=CARD, relief="flat",
                       highlightthickness=1, highlightbackground=BORDER, borderwidth=0, padx=8, pady=6)
        text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))
        text.configure(state="disabled")

        bar = ttk.Frame(self, padding=PAD)
        bar.grid(row=2, column=0, sticky="ew")
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="确认发送", style="Accent.TButton",
                   command=self._confirm).pack(side="right", padx=8)

    def _confirm(self):
        self.confirmed = True
        self.destroy()


class SourceDialog(tk.Toplevel):
    """人工指定来源：只能从已读取的科目中选择，金额由本地重新计算。"""

    def __init__(self, master, result, rows):
        super().__init__(master)
        self.title(f"人工指定来源 · {result.target.id}")
        self.transient(master)
        self.grab_set()
        self.geometry("820x480")
        self.result = None
        self.rows = list(rows)
        self.filtered = self.rows
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        tk.Label(self, text=f"{result.target.label} · {result.target.id} · "
                            f"{'期初' if result.target.period == 'opening' else '期末'}",
                 bg=BG, fg=SEC, font=(FONT, 9)).grid(row=0, column=0, sticky="w", padx=PAD, pady=(PAD, 0))
        bar = ttk.Frame(self, padding=(PAD, 6, PAD, 0))
        bar.grid(row=1, column=0, sticky="ew")
        ttk.Label(bar, text="筛选").pack(side="left")
        self.keyword = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.keyword, width=32)
        entry.pack(side="left", padx=6)
        entry.bind("<KeyRelease>", lambda _event: self._filter())

        frame = ttk.Frame(self, padding=(PAD, 6, PAD, 0))
        frame.grid(row=2, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(frame, selectmode="extended", font=(FONT, 9), activestyle="none",
                                  relief="flat", highlightthickness=1, highlightbackground=BORDER,
                                  selectbackground=ACC_SOFT, selectforeground=TX, borderwidth=0)
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)

        actions = ttk.Frame(self, padding=PAD)
        actions.grid(row=3, column=0, sticky="ew")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="确定使用所选来源", command=self._ok).pack(side="right", padx=8)
        self._filter()

    def _filter(self):
        keyword = self.keyword.get().strip()
        if keyword:
            self.filtered = [row for row in self.rows
                             if keyword in row.code or keyword in row.name or keyword in (row.path or "")]
        else:
            self.filtered = self.rows
        self.listbox.delete(0, "end")
        for row in self.filtered:
            self.listbox.insert("end", f"{row.code}　{row.name}　[{row.path or row.name}]　"
                                       f"{row.direction}　期末 {fmt_amount(row.ending)}")

    def _ok(self):
        picked = [self.filtered[index] for index in self.listbox.curselection()]
        if not picked:
            messagebox.showinfo("未选择", "请至少选择一个来源科目。", parent=self)
            return
        self.result = picked
        self.destroy()



def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App().mainloop()


if __name__ == "__main__":
    main()
