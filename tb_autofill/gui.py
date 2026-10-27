"""桌面界面：鼎信诺科目余额 → 用户模板填列与复核。

界面只负责串联与确认，所有金额仍由 matching.resolve_match 在本地计算。
"""

from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, ai as ai_module
from . import auditlog, dxn, fields, matching, settings, workbook
from .models import Subject, money

APP_NAME = "ZSZH TB 自动填写系统"
VERSION = __version__
PAD = 8

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
    if value is None or value == "":
        return ""
    try:
        return f"{money(value):,}"
    except ValueError:
        return str(value)


def has_value(value):
    return value is not None and str(value).strip() != ""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{VERSION}")
        # 适配小屏：窗口不超出可用桌面，避免底部操作区被裁掉。
        width = min(1280, max(1000, self.winfo_screenwidth() - 60))
        height = min(830, max(640, self.winfo_screenheight() - 110))
        self.geometry(f"{width}x{height}")
        self.minsize(1000, 620)
        self.queue = queue.Queue()
        self.busy = False
        self.projects = []
        self.subjects = []
        self.rows = []
        self.results = []
        self.scan = None
        self.current_subject = None
        self._ai_subset = []
        try:
            self.audit = auditlog.AuditLog()
        except Exception:
            self.audit = None
        self._load_settings()
        self._init_style()
        self._build()
        self.after(80, self._pump)
        self._sync_ai_state()
        self.after(250, self._autodetect)

    # ---------- 基础设施 ----------

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
        if "vista" in style.theme_names():
            style.theme_use("vista")
        base = ("Microsoft YaHei UI", 9)
        style.configure(".", font=base)
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Treeview", font=("Microsoft YaHei UI", 9), rowheight=24)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Hint.TLabel", foreground="#6b7280")

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self._build_header()
        self._build_source()
        self._build_template()
        self._build_result()
        self._build_actions()
        self._build_log()
        if self._pending_error:
            self._log("设置读取失败：" + self._pending_error, error=True)

    def _build_header(self):
        header = ttk.Frame(self, padding=(PAD, PAD, PAD, 0))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text=APP_NAME, font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        ttk.Label(header, text="鼎信诺科目余额 → 模板填列 / 复核", style="Hint.TLabel").pack(
            side="left", padx=(12, 0), pady=(6, 0))
        ttk.Button(header, text="AI 设置…", command=self._open_ai_settings).pack(side="right")
        self.ai_state = tk.StringVar(value="AI：未配置")
        ttk.Label(header, textvariable=self.ai_state).pack(side="right", padx=(0, 10))

    def _build_source(self):
        box = ttk.LabelFrame(self, text=" ① 数据源：鼎信诺科目余额（原库只读，仅读临时副本） ", padding=PAD)
        box.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="安装目录").grid(row=0, column=0, sticky="w")
        self.install_var = tk.StringVar()
        ttk.Entry(box, textvariable=self.install_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(box, text="自动探测", width=10, command=self._autodetect).grid(row=0, column=2)
        ttk.Button(box, text="选择…", width=8, command=self._choose_install).grid(row=0, column=3, padx=(6, 0))

        ttk.Label(box, text="项目").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.project_var = tk.StringVar()
        self.project_box = ttk.Combobox(box, textvariable=self.project_var, state="readonly")
        self.project_box.grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        self.project_box.bind("<<ComboboxSelected>>", lambda _event: self._load_subjects())
        ttk.Button(box, text="扫描项目", width=10, command=self._scan_projects).grid(row=1, column=2, pady=(6, 0))
        self.ledger_state = tk.StringVar(value="尚未读取科目余额")
        ttk.Label(box, textvariable=self.ledger_state, style="Hint.TLabel").grid(
            row=1, column=3, sticky="w", padx=(6, 0), pady=(6, 0))

        line = ttk.Frame(box)
        line.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Label(line, text="主体单位").pack(side="left")
        self.subject_var = tk.StringVar()
        self.subject_box = ttk.Combobox(line, textvariable=self.subject_var, state="readonly", width=44)
        self.subject_box.pack(side="left", padx=6)
        self.subject_box.bind("<<ComboboxSelected>>", self._on_subject)
        ttk.Label(line, text="年度").pack(side="left", padx=(10, 0))
        self.year_var = tk.StringVar()
        ttk.Combobox(line, textvariable=self.year_var, width=7,
                     values=[str(year) for year in range(2000, 2101)]).pack(side="left", padx=6)
        ttk.Label(line, text="截止月").pack(side="left")
        self.month_var = tk.StringVar()
        ttk.Combobox(line, textvariable=self.month_var, width=4,
                     values=[str(month) for month in range(1, 13)]).pack(side="left", padx=6)
        ttk.Button(line, text="读取科目余额", command=self._read_ledger).pack(side="left", padx=(10, 0))

    def _build_template(self):
        box = ttk.LabelFrame(self, text=" ② 模板与期间（.xlsx / .xlsm，原文件不改动，只另存） ", padding=PAD)
        box.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="模板文件").grid(row=0, column=0, sticky="w")
        self.template_var = tk.StringVar()
        ttk.Entry(box, textvariable=self.template_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(box, text="浏览…", width=8, command=self._choose_template).grid(row=0, column=2)

        line = ttk.Frame(box)
        line.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Label(line, text="填列期间").pack(side="left")
        self.period_var = tk.StringVar(value="both")
        for text, value in (("仅期初", "opening"), ("仅期末", "ending"), ("期初 + 期末", "both")):
            ttk.Radiobutton(line, text=text, value=value, variable=self.period_var).pack(side="left", padx=(8, 0))
        ttk.Label(line, text="金额单位").pack(side="left", padx=(18, 0))
        self.unit_var = tk.StringVar(value="自动")
        ttk.Combobox(line, textvariable=self.unit_var, state="readonly", width=7,
                     values=("自动", "元", "千元", "万元", "亿元")).pack(side="left", padx=6)
        ttk.Button(line, text="识别并匹配", command=self._recognize).pack(side="left", padx=(16, 0))
        self.scan_state = tk.StringVar(value="尚未识别模板")
        ttk.Label(line, textvariable=self.scan_state, style="Hint.TLabel").pack(side="left", padx=12)

    def _build_result(self):
        frame = ttk.LabelFrame(self, text=" ③ 识别结果与预览（勾选后才写入；已有值默认保留） ", padding=(PAD, PAD, PAD, 4))
        frame.grid(row=3, column=0, sticky="nsew", padx=PAD, pady=(PAD, 0))
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        bar = ttk.Frame(frame)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(bar, text="显示").pack(side="left")
        self.filter_var = tk.StringVar(value="全部")
        chooser = ttk.Combobox(bar, textvariable=self.filter_var, state="readonly", width=10,
                               values=("全部", "可写入", "已匹配", "待确认", "未匹配", "公式保护", "已选中"))
        chooser.pack(side="left", padx=6)
        chooser.bind("<<ComboboxSelected>>", lambda _event: self._refresh_tree())
        ttk.Button(bar, text="全选可写入", command=self._select_all_fillable).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="清空勾选", command=self._clear_selection).pack(side="left", padx=6)
        ttk.Button(bar, text="允许覆盖", command=self._allow_overwrite).pack(side="left", padx=6)
        ttk.Button(bar, text="指定来源…", command=self._manual_source).pack(side="left", padx=6)
        self.ai_btn = ttk.Button(bar, text="AI 复核", command=self._ai_review)
        self.ai_btn.pack(side="left", padx=6)
        self.stats = tk.StringVar(value="尚未识别")
        ttk.Label(bar, textvariable=self.stats, style="Hint.TLabel").pack(side="right")

        columns = ("sel", "status", "cell", "label", "period", "amount", "actual", "diff", "sources", "reason")
        headings = {"sel": "✓", "status": "状态", "cell": "单元格", "label": "报表项目", "period": "期间",
                    "amount": "应填值", "actual": "现有值", "diff": "差额", "sources": "来源科目",
                    "reason": "方法 / 原因"}
        widths = {"sel": 34, "status": 76, "cell": 96, "label": 176, "period": 50, "amount": 104,
                  "actual": 104, "diff": 96, "sources": 166, "reason": 280}
        anchors = {"sel": "center", "status": "center", "cell": "center", "period": "center",
                   "amount": "e", "actual": "e", "diff": "e"}
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        for key in columns:
            self.tree.heading(key, text=headings[key])
            self.tree.column(key, width=widths[key], minwidth=widths[key] - 20,
                             anchor=anchors.get(key, "w"),
                             stretch=key in ("label", "reason", "sources"))
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        hscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        hscroll.grid(row=2, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=hscroll.set)
        self.tree.tag_configure("ok", background="#eaf7ee", foreground="#14532d")
        self.tree.tag_configure("warn", background="#fdf6e3", foreground="#78350f")
        self.tree.tag_configure("bad", background="#fdeaea", foreground="#7f1d1d")
        self.tree.tag_configure("info", background="#eaf1fb", foreground="#1e3a5f")
        self.tree.tag_configure("muted", background="#f4f4f5", foreground="#3f3f46")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)
        self.tree.bind("<Double-1>", lambda _event: self._show_detail())

    def _build_actions(self):
        bar = ttk.Frame(self, padding=(PAD, 6, PAD, 0))
        bar.grid(row=4, column=0, sticky="ew")
        self.report_btn = ttk.Button(bar, text="导出复核报告（CSV）", command=self._export_report, state="disabled")
        self.report_btn.pack(side="left")
        self.write_btn = ttk.Button(bar, text="另存为 Excel…", command=self._write_excel, state="disabled")
        self.write_btn.pack(side="left", padx=8)
        ttk.Button(bar, text="校验审计日志", command=self._verify_audit).pack(side="left")
        ttk.Button(bar, text="退出", command=self.destroy).pack(side="right")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=180)
        self.progress.pack(side="right", padx=10)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.status_var, style="Hint.TLabel").pack(side="right", padx=10)

    def _build_log(self):
        frame = ttk.LabelFrame(self, text=" 运行日志 ", padding=(PAD, 4, PAD, 4))
        frame.grid(row=5, column=0, sticky="ew", padx=PAD, pady=PAD)
        frame.columnconfigure(0, weight=1)
        self.log = tk.Text(frame, height=5, wrap="none", state="disabled",
                           font=("Consolas", 9), background="#fbfbfc", relief="flat")
        self.log.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

    # ---------- 线程与日志 ----------

    def _pump(self):
        try:
            while True:
                self.queue.get_nowait()()
        except queue.Empty:
            pass
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
            self.progress.start(12)
            self.status_var.set(text)
        else:
            self.progress.stop()

    def _async(self, text, work, done=None):
        if self.busy:
            messagebox.showinfo("请稍候", "当前任务尚未完成。")
            return
        self._set_busy(True, text)
        self._log(text)

        def runner():
            try:
                value = work()
            except Exception as exc:
                detail = traceback.format_exc(limit=3)
                self._post(lambda: self._finish_error(text, exc, detail))
            else:
                self._post(lambda: self._finish_ok(text, value, done))

        threading.Thread(target=runner, daemon=True).start()

    def _finish_error(self, text, exc, detail):
        self._set_busy(False)
        self._log(f"{text}失败：{exc}", error=True)
        for line in detail.strip().splitlines()[-2:]:
            self._log("  " + line.strip(), error=True)
        messagebox.showerror("操作未完成", str(exc))

    def _finish_ok(self, text, value, done):
        self._set_busy(False)
        try:
            if done:
                done(value)
        except Exception as exc:
            self._log(f"{text}后处理失败：{exc}", error=True)
            messagebox.showerror("操作未完成", str(exc))

    # ---------- 数据源 ----------

    def _choose_install(self):
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
                          self.current_subject.project_path, self.current_subject.source_path)
        install = self.install_var.get().strip()
        self._async("正在校验并复制科目余额（原库只读）…",
                    lambda: dxn.read_ledger(subject, install, self._thread_log), self._on_ledger)

    def _on_ledger(self, rows):
        self.rows = list(rows)
        if not self.rows:
            self.ledger_state.set("未读到科目")
            self._log("未读到任何科目记录。")
            return
        self.ledger_state.set(f"已读取 {len(self.rows)} 个科目")
        self._log(f"科目余额读取完成：{len(self.rows)} 个科目，"
                  f"编码 {self.rows[0].code}–{self.rows[-1].code}。")
        self._recompute_if_ready()

    # ---------- 模板识别 ----------

    def _choose_template(self):
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
        path = self.template_var.get().strip()
        if not path:
            messagebox.showwarning("缺少模板", "请先选择模板文件。")
            return
        if not self.rows:
            messagebox.showwarning("缺少科目余额", "请先读取鼎信诺科目余额。")
            return
        unit = self.unit_var.get()
        periods = self._periods()

        def work():
            scan = fields.scan_template(path, periods, unit)
            return scan, matching.match_fields(scan.fields, self.rows)

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

    def _recompute_if_ready(self):
        if self.scan is not None and self.rows:
            self._log("科目余额已更新，请点击“识别并匹配”重新计算。")

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
        if not self.results:
            self.stats.set("尚未识别")
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
        added = 0
        for result in self.results:
            if result.selected or result.amount is None or result.status == "protected":
                continue
            if has_value(result.target.value):
                continue
            result.selected = True
            added += 1
        self._refresh_tree()
        self._log(f"已勾选 {added} 个空白字段；已有值的 {sum(1 for r in self.results if has_value(r.target.value))} "
                  "个字段需逐个确认覆盖。")

    def _clear_selection(self):
        for result in self.results:
            result.selected = False
            result.overwrite = False
        self._refresh_tree()
        self._log("已清空全部勾选。")

    def _allow_overwrite(self):
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
        check = matching.resolve_match(result.target, dialog.result, method="manual")
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
        if has_key and enabled:
            self.ai_state.set(f"AI：已启用（{model}）")
            self.ai_btn.configure(state="normal")
        elif has_key:
            self.ai_state.set("AI：已配置但未启用")
            self.ai_btn.configure(state="disabled")
        else:
            self.ai_state.set("AI：未配置（可离线使用）")
            self.ai_btn.configure(state="disabled")

    def _open_ai_settings(self):
        if self.store is None:
            messagebox.showerror("设置不可用", self._pending_error or "本机设置文件不可读。")
            return
        AISettingsDialog(self, self.store)
        self._sync_ai_state()

    def _ai_review(self):
        if self.store is None or not self.store.has_key:
            messagebox.showinfo("未配置 Key", "请先在“AI 设置”中填入自己的 DeepSeek API Key。")
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
        except ai_module.AIError as exc:
            messagebox.showwarning("范围过大", str(exc))
            return
        dialog = PayloadDialog(self, payload, self.store.data.get("model", ""))
        self.wait_window(dialog)
        if not dialog.confirmed:
            return
        self._ai_subset = subset
        try:
            key = self.store.get_key()
        except settings.SettingsError as exc:
            messagebox.showerror("无法读取 Key", str(exc))
            return
        model = self.store.data.get("model")
        self._async(f"正在请求 DeepSeek 复核 {len(subset)} 个字段…",
                    lambda: ai_module.DeepSeekClient(key, model).assist(payload),
                    self._on_ai_answer)

    def _on_ai_answer(self, answer):
        applied, notes = matching.apply_ai_suggestions(self._ai_subset, answer.get("suggestions", []), self.rows)
        self._refresh_tree()
        self._log(f"AI 复核：{answer.get('summary', '').strip()}")
        self._log(f"AI 采纳 {applied} 条建议（金额仍由本地规则计算）。未采纳 {len(notes)} 条。")
        for note in notes[:5]:
            self._log("  未采纳：" + note)
        messagebox.showinfo("AI 复核完成", f"{answer.get('summary', '').strip()}\n\n"
                                          f"采纳建议：{applied} 条\n未采纳：{len(notes)} 条")

    # ---------- 输出 ----------

    def _export_report(self):
        if not self.results:
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
        if self.current_subject is None:
            return ""
        try:
            return f"{self.current_subject.name} / {int(self.year_var.get())}-{int(self.month_var.get()):02d}"
        except ValueError:
            return self.current_subject.name

    def _write_excel(self):
        if not self.results or self.scan is None:
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
        self._async(f"正在写入 {len(selected)} 个字段（调用 Excel 重算并逐格校验）…",
                    lambda: workbook.write_results(self.scan, self.results, path), self._on_written)

    def _on_written(self, output):
        self._log(f"已另存：{output}")
        self._audit("write_excel", output=str(output), template=str(self.scan.path),
                    template_digest=self.scan.digest, subject=self._subject_label(), unit=self.scan.unit,
                    cells=[{"cell": result.target.id,
                            "before": "公式" if result.target.is_formula else fmt_amount(result.target.value),
                            "after": str(result.amount), "overwrite": result.overwrite,
                            "sources": result.source_ids}
                           for result in self.results if result.selected])
        messagebox.showinfo("写入完成", f"已生成并校验：\n{output}\n\n原始模板未改动，改动已写入本机审计日志。")

    def _audit(self, event, **fields):
        if self.audit is None:
            self._log("审计日志不可用，本次操作未留痕。", error=True)
            return
        try:
            record = self.audit.append(event, **fields)
        except Exception as exc:
            self._log(f"审计日志写入失败：{exc}", error=True)
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
            messagebox.showinfo("审计日志校验", f"完整：共 {total} 条记录，哈希链连续。\n\n{self.audit.path}")
        else:
            messagebox.showerror("审计日志校验", f"第 {seq} 条记录异常：{reason}\n\n{self.audit.path}\n"
                                             "请保留现场并人工核对，不要覆盖原文件。")


class PayloadDialog(tk.Toplevel):
    """上传 AI 的内容必须可预览、可取消。"""

    def __init__(self, master, payload, model):
        super().__init__(master)
        self.title("AI 复核 · 上传内容预览")
        self.transient(master)
        self.grab_set()
        self.resizable(True, True)
        self.confirmed = False
        self.geometry("760x560")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        ttk.Label(self, text=f"以下 {len(payload['targets'])} 个目标与 {len(payload['candidates'])} 个候选科目"
                             f"将发送至 DeepSeek（{model}）。", padding=(PAD, PAD, PAD, 0)).grid(
            row=0, column=0, sticky="w")
        ttk.Label(self, text="内容包含报表项目名称与科目名称，不含金额合计或其他客户资料；AI 只能返回来源科目建议。",
                  style="Hint.TLabel", padding=(PAD, 2, PAD, 6)).grid(row=1, column=0, sticky="w")

        frame = ttk.Frame(self, padding=(PAD, 0, PAD, 0))
        frame.grid(row=2, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text = tk.Text(frame, wrap="none", font=("Consolas", 9), background="#fbfbfc", relief="flat")
        text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", json.dumps(payload, ensure_ascii=False, indent=2))
        text.configure(state="disabled")

        bar = ttk.Frame(self, padding=PAD)
        bar.grid(row=3, column=0, sticky="ew")
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="确认发送", command=self._confirm).pack(side="right", padx=8)

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

        ttk.Label(self, text=f"目标：{result.target.label}（{result.target.id}，"
                             f"{'期初' if result.target.period == 'opening' else '期末'}）",
                  padding=(PAD, PAD, PAD, 0)).grid(row=0, column=0, sticky="w")
        bar = ttk.Frame(self, padding=(PAD, 6, PAD, 0))
        bar.grid(row=1, column=0, sticky="ew")
        ttk.Label(bar, text="筛选").pack(side="left")
        self.keyword = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.keyword, width=32)
        entry.pack(side="left", padx=6)
        entry.bind("<KeyRelease>", lambda _event: self._filter())
        ttk.Label(bar, text="按 Ctrl/Shift 可多选，多选将求和", style="Hint.TLabel").pack(side="left", padx=8)

        frame = ttk.Frame(self, padding=(PAD, 6, PAD, 0))
        frame.grid(row=2, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.listbox = tk.Listbox(frame, selectmode="extended", font=("Microsoft YaHei UI", 9), activestyle="none")
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


class AISettingsDialog(tk.Toplevel):
    def __init__(self, master, store):
        super().__init__(master)
        self.title("AI 设置 · DeepSeek")
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)
        self.store = store
        self.queue = queue.Queue()
        self.models = list(settings.MODELS)

        body = ttk.Frame(self, padding=PAD)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)

        state = "已保存 Key（本机 Windows DPAPI 加密）" if store.has_key else "尚未保存 Key"
        ttk.Label(body, text="当前状态").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text=state, style="Hint.TLabel").grid(row=0, column=1, sticky="w")

        ttk.Label(body, text="API Key").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.key_var = tk.StringVar()
        ttk.Entry(body, textvariable=self.key_var, show="•", width=48).grid(
            row=1, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(body, text="留空表示沿用已保存的 Key；Key 不写入项目文件，也不随代码提交。",
                  style="Hint.TLabel").grid(row=2, column=1, sticky="w", pady=(2, 0))

        ttk.Label(body, text="模型").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.model_var = tk.StringVar(value=store.data.get("model", self.models[0]))
        self.model_box = ttk.Combobox(body, textvariable=self.model_var, state="readonly", width=30,
                                      values=self.models)
        self.model_box.grid(row=3, column=1, sticky="w", pady=(8, 0))

        self.enable_var = tk.BooleanVar(value=bool(store.data.get("ai_enabled")))
        ttk.Checkbutton(body, text="启用 AI 复核（未启用时全部功能仍可离线使用）",
                        variable=self.enable_var).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.test_state = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.test_state, style="Hint.TLabel").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

        actions = ttk.Frame(self, padding=(PAD, 0, PAD, PAD))
        actions.grid(row=1, column=0, sticky="ew")
        ttk.Button(actions, text="关闭", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="保存", command=self._save).pack(side="right", padx=8)
        ttk.Button(actions, text="删除 Key", command=self._delete).pack(side="right", padx=(0, 8))
        self.test_btn = ttk.Button(actions, text="测试连接", command=self._test)
        self.test_btn.pack(side="left")

    def _test(self):
        key = self.key_var.get().strip()
        if not key:
            try:
                key = self.store.get_key()
            except settings.SettingsError as exc:
                self.test_state.set(str(exc))
                return
        if not key:
            self.test_state.set("请先输入 API Key。")
            return
        model = self.model_var.get()
        self.test_btn.configure(state="disabled")
        self.test_state.set("正在连接 api.deepseek.com …")

        def work():
            try:
                ids = ai_module.DeepSeekClient(key, model).list_models()
            except Exception as exc:
                self.queue.put(("err", str(exc)))
            else:
                self.queue.put(("ok", ids))

        threading.Thread(target=work, daemon=True).start()
        self.after(120, self._poll)

    def _poll(self):
        try:
            kind, value = self.queue.get_nowait()
        except queue.Empty:
            self.after(120, self._poll)
            return
        self.test_btn.configure(state="normal")
        if kind == "err":
            self.test_state.set("连接失败：" + value)
            return
        self.models = value
        self.model_box.configure(values=value)
        if self.model_var.get() not in value:
            self.model_var.set(value[0])
        self.test_state.set("连接成功，可用模型：" + "、".join(value))

    def _save(self):
        key = self.key_var.get().strip()
        model = self.model_var.get()
        enable = self.enable_var.get()
        try:
            if key:
                self.store.save_ai(key, enabled=enable, model=model)
            elif enable and not self.store.has_key:
                raise settings.SettingsError("启用 AI 前请先输入 API Key。")
            else:
                self.store.save_model(model)
                self.store.save_ai(None, enabled=enable, model=model)
        except settings.SettingsError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.key_var.set("")
        messagebox.showinfo("已保存", "设置已保存到本机。", parent=self)
        self.destroy()

    def _delete(self):
        if not messagebox.askyesno("删除 Key", "将删除本机保存的 API Key 并停用 AI，是否继续？", parent=self):
            return
        try:
            self.store.delete_key()
        except settings.SettingsError as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)
            return
        self.enable_var.set(False)
        self.test_state.set("Key 已删除，AI 已停用。")
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
