"""Reference retrieval and explicit source selection for the audit Agent."""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from . import connections, knowledge, settings


class KnowledgePanel(tk.Frame):
    def __init__(self, app, parent):
        super().__init__(parent, bg="#f3f6fa")
        self.app, self.refs, self.libraries = app, [], []
        self.lib_cursor = self.cursor = ""
        self.lib_query = ""
        self.last_query = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.grid(row=0, column=0, sticky="nsew", padx=20, pady=18)
        parent.rowconfigure(0, weight=1)
        top = ttk.Frame(self, padding=16)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="财会知识库", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(top, text="检索规则和实务资料，选用来源后交给审计 Agent；知识材料不等同于项目审计证据。",
                  wraplength=900).pack(anchor="w", pady=9)
        actions = ttk.Frame(top)
        actions.pack(fill="x")
        ttk.Button(actions, text="配置 ima 连接", command=self._configure).pack(side="left")
        ttk.Button(actions, text="导入本地资料", command=self._import).pack(side="left", padx=8)
        ttk.Button(actions, text="ima 接入说明", command=lambda: webbrowser.open("https://ima.qq.com/agent-interface")).pack(side="left")
        form = ttk.Frame(self, padding=14)
        form.grid(row=1, column=0, sticky="ew", pady=10)
        form.columnconfigure(1, weight=1)
        self.provider = tk.StringVar(value="本地资料")
        self.library_query, self.library_id, self.query = tk.StringVar(value="财会"), tk.StringVar(), tk.StringVar()
        ttk.Combobox(form, textvariable=self.provider, state="readonly", values=("本地资料", "ima 知识库"), width=15).grid(row=0, column=0, padx=(0, 10))
        ttk.Entry(form, textvariable=self.query).grid(row=0, column=1, sticky="ew")
        ttk.Button(form, text="检索资料", command=self._search).grid(row=0, column=2, padx=8)
        ttk.Button(form, text="下一页", command=lambda: self._search(next_page=True)).grid(row=0, column=3)
        libraries = ttk.Frame(form)
        libraries.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        ttk.Label(libraries, text="ima 库名").pack(side="left")
        ttk.Entry(libraries, textvariable=self.library_query, width=17).pack(side="left", padx=7)
        ttk.Button(libraries, text="搜索库名", command=self._find_libraries).pack(side="left")
        ttk.Button(libraries, text="更多库", command=lambda: self._find_libraries(True)).pack(side="left", padx=6)
        self.library_box = ttk.Combobox(libraries, state="readonly", width=25)
        self.library_box.pack(side="left", padx=6, fill="x", expand=True)
        ttk.Label(libraries, text="或填库 ID").pack(side="left")
        ttk.Entry(libraries, textvariable=self.library_id, width=17).pack(side="left", padx=(6, 0))
        self.status = tk.StringVar(value="本地支持 TXT / Markdown / DOCX / 文字 PDF；ima 只检索 API 有权读取的库。")
        ttk.Label(self, textvariable=self.status, wraplength=1000).grid(row=2, column=0, sticky="w", pady=(0, 8))
        body = ttk.Frame(self)
        body.grid(row=3, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(body, columns=("source", "title", "excerpt"), show="headings", selectmode="extended", height=7)
        for key, title, width in (("source", "来源", 75), ("title", "资料标题", 220), ("excerpt", "命中片段（可双击阅读）", 550)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=60, stretch=key == "excerpt")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", self._view)
        footer = ttk.Frame(self, padding=(0, 12))
        footer.grid(row=4, column=0, sticky="ew")
        ttk.Button(footer, text="选用为本次依据", command=self._use).pack(side="left")
        self.scope = tk.StringVar(value="TB")
        ttk.Combobox(footer, textvariable=self.scope, values=("TB", "附注", "底稿", "报告"), state="readonly", width=8).pack(side="left", padx=10)
        ttk.Button(footer, text="Agent 检索并复核", style="Accent.TButton",
                   command=lambda: self._search(agent=True)).pack(side="left")
        ttk.Label(footer, text="只读检索；查询词发送到所选来源，账套不会上传至 ima。", wraplength=440).pack(side="right")

    def _configure(self):
        if self.app.busy or self.app.store is None:
            return
        dialog = connections.ImaDialog(self.app, self.app.store)
        self.app.wait_window(dialog)
        self.status.set("ima 凭证已配置，请搜索库名验证权限。" if self.app.store.has_ima else "ima 未配置。")

    def _client(self):
        if self.app.store is None:
            raise settings.SettingsError("本机设置不可用。")
        return knowledge.ImaClient(*self.app.store.get_ima())

    def _import(self):
        if self.app.busy:
            return
        paths = filedialog.askopenfilenames(title="导入有权使用的财会参考资料", filetypes=[("可提取文字的资料", "*.txt *.md *.docx *.pdf")])
        if not paths:
            return
        if len(paths) > 10:
            self.status.set("单次最多导入 10 份资料。")
            return

        def work():
            ok, failures = 0, []
            for path in paths:
                try:
                    _digest, added = self.app.library.import_file(path)
                    ok += int(added)
                except Exception as exc:
                    failures.append(str(exc))
            return ok, failures

        def done(value):
            count = len(self.app.library.list_documents())
            self.status.set(f"新增 {value[0]} 份，库内共 {count} 份。" + (" 导入失败：" + "；".join(value[1]) if value[1] else "重复资料已跳过。"))
        self.app._async("正在读取本地参考资料…", work, done, guard_context=False)

    def _find_libraries(self, next_page=False):
        if self.app.busy:
            return
        query = self.library_query.get().strip()
        cursor = self.lib_cursor if next_page and query == self.lib_query else ""
        if next_page and not cursor:
            self.status.set("没有下一页库列表，或检索条件已变化。")
            return
        try:
            client = self._client()
        except (settings.SettingsError, knowledge.KnowledgeError) as exc:
            self.status.set(str(exc))
            return

        def done(value):
            if self.library_query.get().strip() != query:
                self.status.set("库名已改变，旧结果已丢弃。")
                return
            items, self.lib_cursor = value
            self.lib_query = query
            self.libraries = self.libraries + items if cursor else items
            self.library_box.configure(values=[item["name"] + " [" + item["id"][:10] + "]" for item in self.libraries])
            if self.libraries:
                self.library_box.current(0)
                self.provider.set("ima 知识库")
            self.status.set(f"接口返回 {len(self.libraries)} 个匹配库。" + ("仍有下一页。" if self.lib_cursor else "本次分页已结束。") + "关注库是否可读仍以内容检索权限为准。")
        self.app._async("正在验证 ima 凭证并搜索知识库…", lambda: client.search_libraries(query, cursor), done, guard_context=False)

    def _library(self):
        manual = self.library_id.get().strip()
        index = self.library_box.current()
        return manual or (self.libraries[index]["id"] if 0 <= index < len(self.libraries) else "")

    def _search(self, next_page=False, agent=False):
        if self.app.busy:
            return
        signature = (self.provider.get(), self._library(), self.query.get().strip())
        cursor = self.cursor if next_page and self.last_query == signature else ""
        if next_page and not cursor:
            self.status.set("没有下一页；本地检索最多显示 8 个最相关片段。")
            return
        try:
            if signature[0] == "ima 知识库":
                client = self._client()
                work = lambda: client.search(signature[1], signature[2], cursor)
            else:
                work = lambda: (self.app.library.search(signature[2]), "")
        except (settings.SettingsError, knowledge.KnowledgeError) as exc:
            self.status.set(str(exc))
            return
        scope = self.scope.get()

        def done(value):
            if signature != (self.provider.get(), self._library(), self.query.get().strip()):
                self.status.set("检索条件已变化，旧结果已丢弃。")
                return
            items, self.cursor = value
            self.last_query = signature
            self.refs = self.refs + items if cursor else items
            for iid in self.tree.get_children():
                self.tree.delete(iid)
            for index, item in enumerate(self.refs):
                self.tree.insert("", "end", iid=str(index), values=(item["source"], item["title"], item["excerpt"][:250] or "仅标题，无可引用正文"))
            self.status.set(f"本次显示 {len(self.refs)} 个命中。" + ("仍有下一页。" if self.cursor else "") + "本地为关键词检索；ima 片段不等于完整原文。")
            if agent:
                usable = [item for item in items if item["excerpt"]][:3]
                if not usable:
                    self.status.set("未获得可引用正文，未调用模型。请更换检索词，或导入有权使用的原文。")
                    return
                self.app._set_knowledge_refs(usable)
                self.app._agent_review(scope)
        self.app._async("正在检索参考材料…", work, done, guard_context=agent)

    def _use(self):
        if self.app.busy:
            return
        picked = [self.refs[int(iid)] for iid in self.tree.selection()]
        if not picked or len(picked) > 5:
            self.status.set("请选择 1–5 个含正文的片段。")
            return
        if any(not item["excerpt"] for item in picked):
            self.status.set("选项中存在仅标题的结果，不能作为正文依据。")
            return
        self.app._set_knowledge_refs(picked)
        self.status.set(f"已选用 {len(picked)} 份依据；下一次 Agent 请求会先预览内容。")

    def _view(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        item = self.refs[int(selected[0])]
        messagebox.showinfo(item["title"], item["locator"] + "\n\n" + item["excerpt"], parent=self.app)
