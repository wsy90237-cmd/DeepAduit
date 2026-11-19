"""底稿页的项目资料窗口。所有模型请求均由用户预览后触发。"""
import json
import os
from pathlib import Path
from threading import Event
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import ai, audit_index, evidence_draft as draft
from .evidence_project import ProjectStore
from .evidence_ownership import check_ownership
from .pdf_text import extract_pdf_pages


def activate_project(app):
    if app.busy:
        return
    subject = app.loaded_subject
    if subject is None:
        messagebox.showinfo("项目资料", "请先在账套与 TB 中读取主体和期间。", parent=app)
        return
    root = getattr(app, 'evidence_root', '') or (app.store.data.get('evidence_root') if app.store else '')
    if not root:
        root = filedialog.askdirectory(title="首次设置：选择各公司底稿的共同存放目录", parent=app)
    if not root:
        return
    try:
        store = ProjectStore(root, draft.project_entity(subject), subject.year)
        if app.store and app.store.data.get('evidence_root') != root:
            app.store.save_evidence_root(root)
        from .auto_intake_ui import Intake
        intake = Intake(app, store)
    except Exception as exc:
        messagebox.showerror("无法打开项目资料", str(exc), parent=app)
        return
    app.evidence_store, app.evidence_root = store, root
    app.intake = intake
    intake.start()
    return store


def open_project(app):
    if app.busy:
        return
    intake = getattr(app, 'intake', None)
    store = intake.store if intake and intake.current() else activate_project(app)
    if not store:
        return
    window = EvidenceDialog(app, store)
    window.grab_set()


def current_store(app):
    store = getattr(app, "evidence_store", None)
    subject = app.loaded_subject
    if subject is None:
        return None
    if store is None or (store.entity, str(store.year)) != (draft.project_entity(subject), str(subject.year)):
        settings = getattr(app, "store", None)
        root = getattr(app, "evidence_root", "") or (settings.data.get("evidence_root") if settings else "")
        store = ProjectStore.open_existing(root, draft.project_entity(subject), subject.year) if root else None
    return store


def paper_rows(app, index):
    store = current_store(app)
    return draft.confirmed_rows(store, index, draft.source_key(app)) if store else []


class EvidenceDialog(tk.Toplevel):
    def __init__(self, app, store):
        super().__init__(app)
        self.app, self.store = app, store
        self.subject = app.loaded_subject
        self.title(f"项目资料 · {self.subject.name} · {self.subject.year}")
        self.geometry("920x680")
        self.transient(app)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.protocol("WM_DELETE_WINDOW", self.close)
        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", padx=12, pady=8)
        self.button(bar, "导入文字版 PDF", self.import_pdf)
        self.button(bar, "打开公司资料夹", lambda: os.startfile(store.root))
        intake = getattr(app, 'intake', None)
        if intake and intake.store is store:
            auto_bar = ttk.Frame(self)
            auto_bar.grid(row=6, column=0, sticky='ew', padx=12, pady=6)
            self.button(auto_bar, '自动收件详情', lambda: self.show_text('自动收件', '\n'.join(intake.notes)))
            self.button(auto_bar, '确认自动底稿范围', intake.confirm)
        self.button(bar, "批量读取 PDF", lambda: self.import_files(sorted(store.inbox.glob("*.pdf"))))
        self.button(bar, "打开原页所在 PDF", self.open_pdf)
        self.button(bar, "已核对资料归属", self.review_doc)
        self.button(bar, "已确认记录", self.history)
        self.docs = ttk.Combobox(self, state="readonly", width=80)
        self.docs.grid(row=1, column=0, sticky="ew", padx=12)
        self.docs.bind("<<ComboboxSelected>>", lambda _: self.select_doc())
        opts = ttk.Frame(self)
        opts.grid(row=2, column=0, sticky="ew", padx=12, pady=6)
        ttk.Label(opts, text="证据页").pack(side="left")
        self.pages = ttk.Combobox(opts, state="readonly", width=5)
        self.pages.pack(side="left")
        self.pages.bind("<<ComboboxSelected>>", lambda _: self.select_page())
        ttk.Label(opts, text="  归入底稿").pack(side="left")
        self.papers = audit_index.options(getattr(app, "paper_library", ()))
        self.paper = ttk.Combobox(opts, values=[p.index_no + " " + p.name for p in self.papers],
                                  state="readonly", width=32)
        self.paper.pack(side="left")
        if self.papers:
            self.paper.current(0)
        ttk.Label(opts, text="  可选中一段原文缩小发送范围").pack(side="left")
        self.text = ScrolledText(self, height=10, wrap="word", exportselection=False)
        self.text.grid(row=3, column=0, sticky="nsew", padx=12)
        self.actions = ttk.Frame(self)
        self.actions.grid(row=4, column=0, sticky="ew", padx=12, pady=8)
        self.button(self.actions, "预览并请求 AI 填写建议", self.analyze)
        self.button(self.actions, "手工摘录", self.manual)
        self.status = ttk.Label(self, text="", wraplength=820)
        self.status.grid(row=5, column=0, sticky="ew", padx=12, pady=8)
        self.stop_batch = Event()
        self.cancel_button = ttk.Button(self.actions, text="本份结束后停止", command=self.stop_batch.set)
        self.reload()

    def button(self, bar, label, action):
        def run():
            if self.app.busy:
                return
            try:
                action()
            except Exception as exc:
                messagebox.showerror("操作未完成", str(exc), parent=self)
        ttk.Button(bar, text=label, command=run).pack(side="left", padx=2)

    def close(self):
        if not self.app.busy:
            self.destroy()

    def reload(self, selected=None):
        self.documents = sorted(self.store.list_documents(), key=lambda d: (not d["current"], d["name"]))
        self.docs.configure(values=[f"{d['name']}  v{d['version']}  " +
            ("历史资料" if not d["text_layer"] or not d["current"] else "已核归属" if d["reviewed"] else "待核归属")
            for d in self.documents])
        if self.documents:
            self.docs.current(next((i for i, d in enumerate(self.documents) if d["id"] == selected), 0))
        self.select_doc()

    def select_doc(self):
        if self.docs.current() < 0:
            return
        doc = self.store.get_document(self.documents[self.docs.current()]["id"])
        self.selected_doc = doc
        self.pages.configure(values=[p["page"] for p in doc["pages"]])
        self.pages.current(0)
        self.select_page()

    def current(self):
        if self.docs.current() < 0 or self.pages.current() < 0:
            raise ValueError("请先导入 PDF 并选择页码。")
        doc = self.selected_doc
        return doc, doc["pages"][self.pages.current()]

    def select_page(self):
        doc, page = self.current()
        text = (page["text"] or "此页没有可读取的 PDF 文字层，请查看原件。") if doc["text_layer"] else (
            "旧版提取结果不再用于底稿，可打开原件或重新导入文字版 PDF。")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.configure(state="disabled")
        self.status.configure(text=f"第 {page['page']} 页 · {len(page['text'])} 字 · {doc['name']}")

    def import_pdf(self):
        paths = filedialog.askopenfilenames(title="选择本项目的文字版 PDF（可多选）", parent=self, filetypes=[("PDF", "*.pdf")])
        if paths:
            self.import_files(paths)

    def import_files(self, paths):
        if not paths:
            self.status.configure(text=f"此目录中尚无 PDF：{self.store.inbox}")
            return
        self.stop_batch.clear()
        self.cancel_button.pack(side="left")
        def work():
            completed, errors, selected = 0, [], None
            for i, path in enumerate(paths, 1):
                if self.stop_batch.is_set():
                    break
                label = f"本机读取 {i}/{len(paths)} · {Path(path).name}"
                self.app._post(lambda text=label: self.status.configure(text=text))
                try:
                    doc = self.store.import_pdf(path, lambda src: [s for _, s in extract_pdf_pages(src)])
                except Exception as exc:
                    errors.append(f"{Path(path).name} · {exc}")
                else:
                    completed += 1
                    selected = doc["id"]
            return completed, errors, selected
        def done(result):
            completed, errors, selected = result
            self.cancel_button.pack_forget()
            self.reload(selected)
            self.status.configure(text=f"已处理 {completed} 份，失败 {len(errors)} 份，"
                                       f"剩余 {len(paths) - completed - len(errors)} 份。")
            if errors:
                self.show_text("未能导入的资料", "\n".join(errors))
        self.app._async("正在本机复制 PDF 并读取文字层", work, done)

    def open_pdf(self):
        doc, _ = self.current()
        os.startfile(self.store.verify_document(doc["id"]))

    def review_doc(self):
        doc, _ = self.current()
        if not doc["text_layer"]:
            raise ValueError("请重新导入文字版 PDF，旧版提取结果不再用于复核。")
        self.store.verify_document(doc["id"])
        checks = check_ownership([p["text"] for p in doc["pages"]], self.subject.name,
                                 self.subject.year, self.subject.month)
        issues = [f"第 {i['page']} 页 · {i['original']}\n{i['detail']}" for i in checks["issues"]]
        if checks["truncated"]:
            issues.append("自动检查已达到篇幅上限，请核对剩余页。")
        prompt = f"请根据原件核对《{doc['name']}》与 {self.subject.name} 的 {self.subject.year} 年项目的关系。"
        if self.show_text("确认资料归属", "\n\n".join([prompt, *issues]), confirm="已核对原件与归属"):
            self.store.mark_reviewed(doc["id"])
            self.reload(doc["id"])

    def selection(self):
        doc, page = self.current()
        self.verify_current(doc)
        if self.paper.current() < 0:
            raise ValueError("请先核对资料归属并选择报表项目。")
        region = self.text.tag_ranges("sel")
        excerpt = self.text.get(*region) if region else page["text"]
        if not page["text"].strip() or not excerpt.strip() or len(excerpt) > 5000:
            raise ValueError("请选择本页 1 至 5000 字的有效摘录。")
        paper = self.papers[self.paper.current()]
        return doc, page, paper, excerpt, draft.source_key(self.app)

    def verify_current(self, doc):
        record = self.store.get_document(doc["id"])
        if not record["text_layer"] or not record["current"] or not record["reviewed"]:
            raise ValueError("资料已换版或归属待核，请重新选择并复核。")
        self.store.verify_document(doc["id"])

    def analyze(self):
        doc, page, paper, excerpt, context = self.selection()
        client = ai.DeepSeekClient.from_store(self.app.store)
        payload = {"主体": self.subject.name, "期间": self.app._subject_label(),
                   "底稿": paper.name, "页码": page["page"], "摘录": excerpt}
        display = json.dumps(payload, ensure_ascii=False, indent=2)
        if not self.show_text("发送前预览", f"服务 {client.base_url}\n模型 {client.model}\n目的：建议底稿字段和后续风险程序\n\n" + display, confirm="发送以上资料"):
            return
        if draft.source_key(self.app) != context:
            raise ValueError("项目来源已变化，请重新预览。")
        self.verify_current(doc)
        self.app._async("正在生成证据页填写建议", lambda: draft.extract_fields(client, payload),
                        lambda result: self.edit(doc, page, paper, excerpt, context, result))

    def manual(self):
        doc, page, paper, excerpt, context = self.selection()
        self.edit(doc, page, paper, excerpt, context, {"quote": excerpt[:1000]})

    def edit(self, doc, page, paper, excerpt, context, result):
        form = tk.Toplevel(self)
        form.title(f"确认写入草稿 · {paper.name} · 第 {page['page']} 页")
        form.transient(self)
        form.grab_set()
        widgets = {}
        for i, (key, title) in enumerate((("field", "底稿字段"), ("value", "证据原值"),
                                          ("quote", "连续原文"), ("risk", "待核风险"),
                                          ("next_step", "下一步程序"))):
            ttk.Label(form, text=title).grid(row=i, column=0, sticky="nw", padx=6, pady=4)
            box = tk.Text(form, width=70, height=3, wrap="word")
            box.insert("1.0", result.get(key, ""))
            box.grid(row=i, column=1, sticky="ew", padx=6, pady=4)
            widgets[key] = box
        ttk.Label(form, text="核对人").grid(row=5, column=0)
        reviewer = ttk.Entry(form)
        reviewer.insert(0, self.app.prepared_by)
        reviewer.grid(row=5, column=1, sticky="w")
        comparison = tk.StringVar()
        def compare():
            try:
                from .bank_evidence_ui import compare_dialog
                if paper.index_no != "1-010":
                    raise ValueError("逐户余额核对适用于货币资金。")
                value = widgets["value"].get("1.0", "end-1c").strip()
                compared = compare_dialog(form, self.app, value, excerpt, store=self.store)
                if compared is not None:
                    comparison.set(compared.text)
                    compared_value[0] = value
                    compared_listing[0] = compared.listing_id
            except Exception as exc:
                messagebox.showerror("余额核对", str(exc), parent=form)
        compared_value = [None]
        compared_listing = [""]
        ttk.Button(form, text="核对银行账户余额", command=compare).grid(row=6, column=0)
        ttk.Label(form, textvariable=comparison, wraplength=480).grid(row=6, column=1)
        def analyze_difference():
            if self.app.busy:
                return
            try:
                facts = comparison.get()
                if not facts:
                    raise ValueError("请先完成逐户余额核对。")
                if compared_value[0] != widgets["value"].get("1.0", "end-1c").strip():
                    raise ValueError("证据原值已变化，请重新核对余额。")
                client = ai.DeepSeekClient.from_store(self.app.store)
                payload = {"主体": self.subject.name, "底稿": paper.name, "页码": page["page"],
                           "摘录": excerpt, "本地核对结果": facts,
                           "任务": "保留原文取值，结合本地已计算的差额和缺口提出待核风险及后续程序。"}
                preview = f"服务 {client.base_url}\n模型 {client.model}\n目的：分析本次核对结果\n\n"
                if not self.show_text("风险分析发送预览", preview + json.dumps(payload, ensure_ascii=False,
                                      indent=2), confirm="发送以上资料", owner=form):
                    return
                self.verify_current(doc)
                if compared_listing[0] and not self.store.verify_bank_listing(compared_listing[0]):
                    raise ValueError("逐户余额清单已换版，请重新核对。")
                if draft.source_key(self.app) != context:
                    raise ValueError("项目来源已变化，请重新核对。")
            except Exception as exc:
                messagebox.showerror("无法分析风险", str(exc), parent=form)
                return
            def update_risk(answer):
                if not form.winfo_exists() or comparison.get() != facts:
                    return
                self.verify_current(doc)
                if compared_listing[0] and not self.store.verify_bank_listing(compared_listing[0]):
                    raise ValueError("逐户余额清单已换版，请重新核对。")
                if draft.source_key(self.app) == context:
                    for key in ("risk", "next_step"):
                        widgets[key].delete("1.0", "end")
                        widgets[key].insert("1.0", answer[key])
            self.app._async("正在分析本地核对结果", lambda: draft.extract_fields(client, payload), update_risk)
        ttk.Button(form, text="按差额分析风险", command=analyze_difference).grid(row=7, column=0)
        def save():
            if self.app.busy:
                return
            try:
                row = draft.validate_fields({k: w.get("1.0", "end-1c") for k, w in widgets.items()}, excerpt)
                draft.validate_fields(row, page["text"])
                signer = reviewer.get().strip()
                if not signer:
                    raise ValueError("请填写实际核对人。")
                if draft.source_key(self.app) != context:
                    raise ValueError("账套或 TB 已变化，请重新核对。")
                if comparison.get() and compared_value[0] != row["value"]:
                    raise ValueError("提取值已变化，请重新核对余额。")
                if compared_listing[0] and not self.store.verify_bank_listing(compared_listing[0]):
                    raise ValueError("辅助清单已变更或不可验证，请重新逐户核对。")
                self.store.verify_document(doc["id"])
                row.update(filename=doc["name"], sha256=doc["sha256"], version=doc["version"],
                           page=page["page"], ref=page["evidence_id"], paper=paper.index_no,
                           context=context, reviewer=signer, comparison=comparison.get(),
                           comparison_listing=compared_listing[0])
                sid = self.store.add_suggestion(json.dumps(row, ensure_ascii=False), [row["ref"]])
                self.store.confirm_suggestion(sid)
            except Exception as exc:
                messagebox.showerror("无法确认", str(exc), parent=form)
                return
            self.status.configure(text=f"已保存到 {paper.name}。导出该项目整册底稿时将附入证据核对表。")
            form.destroy()
        ttk.Button(form, text="已核对原件，采纳至底稿", command=save).grid(row=7, column=1, pady=8)
        self.wait_window(form)
        self.grab_set()

    def show_text(self, title, body, confirm=None, owner=None):
        owner = owner or self
        dialog = tk.Toplevel(owner)
        dialog.title(title)
        dialog.transient(owner)
        dialog.grab_set()
        text = ScrolledText(dialog, wrap="word", width=85, height=24)
        text.insert("1.0", body)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)
        accepted = []
        if confirm:
            def accept():
                accepted.append(True)
                dialog.destroy()
            ttk.Button(dialog, text=confirm, command=accept).pack(side="right", padx=8, pady=8)
        ttk.Button(dialog, text="关闭", command=dialog.destroy).pack(side="right")
        self.wait_window(dialog)
        owner.grab_set()
        return bool(accepted)

    def history(self):
        key = draft.source_key(self.app)
        lines = []
        for item in self.store.list_suggestions():
            row = json.loads(item["text"])
            listing = row.get("comparison_listing")
            listing_current = not listing or self.store.verify_bank_listing(listing)
            state = "已确认" if item["confirmed"] and row["context"] == key and listing_current else "待重新复核"
            if not listing_current:
                state += "（辅助清单已变更或不可验证）"
            lines.append(f"{state} · {row['filename']} v{row['version']} 第{row['page']}页 · "
                         f"{row['field']}：{row['value']}\n原文：{row['quote']}\n待核风险：{row['risk']}\n"
                         f"下一步：{row['next_step']}\n证据编号：{row['ref']}\n{row.get('comparison', '')}")
        self.show_text("项目证据记录", "\n\n".join(lines) or "尚无记录。")
