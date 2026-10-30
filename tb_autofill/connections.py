"""Credential forms never display or log saved plaintext secrets."""

from datetime import datetime
import hashlib
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import urlsplit
import webbrowser

from . import ai, settings

BG, FG = "#ffffff", "#18263b"
FONT = ("Microsoft YaHei UI", 10)


class ModelDialog(tk.Toplevel):
    """一步连接模型服务：粘贴 Key → 自动验模型 → 自动验证对话 → 保存。

    地址与模型默认折叠；只要 Key 能用，就不需要用户理解 /models 或 JSON 能力。
    """

    def __init__(self, master, store):
        super().__init__(master)
        self.title("模型服务")
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)
        self.configure(bg=BG)
        self.store, self.queue = store, queue.Queue()
        self.busy, self.closed, self.tested = False, False, None
        self.poll_job, self.advanced_open = None, False
        self.protocol("WM_DELETE_WINDOW", self._close)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", padx=24, pady=20)
        body.columnconfigure(1, weight=1)

        tk.Label(body, text="模型服务", bg=BG, fg=FG, font=(FONT[0], 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w")
        tk.Label(body, text="粘贴 API Key 后点一次「连接并保存」，其余自动完成。", bg=BG,
                 fg="#68778d", font=(FONT[0], 9)).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 16))

        self.key = tk.StringVar()
        self.endpoint = tk.StringVar(value=store.data.get("base_url", settings.DEFAULT_BASE_URL))
        self.model = tk.StringVar(value=store.data.get("model", settings.MODELS[0]))
        self.state = tk.StringVar(
            value="已保存 Key；留空则沿用原 Key 重新连接。" if store.has_key else "尚未配置。")

        tk.Label(body, text="API Key", bg=BG, fg=FG, font=FONT).grid(row=2, column=0, sticky="w", padx=(0, 12))
        self.key_entry = ttk.Entry(body, textvariable=self.key, show="•", width=44)
        self.key_entry.grid(row=2, column=1, sticky="ew")
        self.connect_button = ttk.Button(body, text="连接并保存", style="Accent.TButton", command=self._connect)
        self.connect_button.grid(row=2, column=2, padx=(10, 0))
        tk.Label(body, text="Key 用 Windows DPAPI 加密保存在本机；只发送到下面的服务地址。",
                 bg=BG, fg="#9aa1ac", font=(FONT[0], 8)).grid(row=3, column=1, columnspan=2, sticky="w", pady=(4, 0))

        self.status = tk.Label(body, textvariable=self.state, bg=BG, fg="#2563eb", font=(FONT[0], 9),
                               wraplength=520, justify="left")
        self.status.grid(row=4, column=0, columnspan=3, sticky="w", pady=(14, 6))

        self.advanced_toggle = ttk.Button(body, text="高级设置 ▸", style="Ghost.TButton",
                                          command=self._toggle_advanced)
        self.advanced_toggle.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.advanced = tk.Frame(body, bg=BG)
        self.advanced.columnconfigure(1, weight=1)
        ttk.Label(self.advanced, text="服务地址", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Combobox(self.advanced, textvariable=self.endpoint, values=(settings.DEFAULT_BASE_URL,),
                     width=42).grid(row=0, column=1, sticky="ew")
        ttk.Label(self.advanced, text="模型", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 0), padx=(0, 10))
        self.model_box = ttk.Combobox(self.advanced, textvariable=self.model, values=settings.MODELS, width=26)
        self.model_box.grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Button(self.advanced, text="刷新模型列表", command=lambda: self._connect(refresh_only=True)).grid(
            row=1, column=2, padx=(8, 0), pady=(8, 0))
        tk.Label(self.advanced, text="兼容接口需支持 /models 与 JSON Chat Completions；换地址必须重新输入对应 Key。",
                 bg=BG, fg="#9aa1ac", font=(FONT[0], 8)).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        footer = tk.Frame(body, bg=BG)
        footer.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(18, 0))
        ttk.Button(footer, text="获取 DeepSeek Key", style="Ghost.TButton",
                   command=lambda: webbrowser.open("https://platform.deepseek.com/api_keys")).pack(side="left")
        self.delete_button = ttk.Button(footer, text="删除本机 Key", style="Ghost.TButton", command=self._delete)
        self.delete_button.pack(side="left", padx=8)
        ttk.Button(footer, text="关闭", command=self._close).pack(side="right")
        if not store.has_key:
            self.delete_button.configure(state="disabled")
        self.key_entry.focus_set()
        self.key_entry.bind("<Return>", lambda _event: self._connect())

    # ---- 交互 ----

    def _toggle_advanced(self):
        self.advanced_open = not self.advanced_open
        if self.advanced_open:
            self.advanced.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 0))
            self.advanced_toggle.configure(text="收起高级设置 ▾")
        else:
            self.advanced.grid_remove()
            self.advanced_toggle.configure(text="高级设置 ▸")

    def _profile(self):
        endpoint = settings.normalize_base_url(self.endpoint.get())
        key = self.key.get().strip() or self.store.get_key(endpoint)
        key = settings.validate_key(key)
        model = self.model.get().strip() or settings.MODELS[0]
        signature = (endpoint, model, hashlib.sha256(key.encode()).hexdigest())
        return ai.DeepSeekClient(key, model, base_url=endpoint), signature

    def _connect(self, refresh_only=False):
        """一次点击完成：验模型列表 → 选模型 → 验对话 → 保存。任一步失败都说明原因。"""
        if self.busy:
            return
        try:
            client, signature = self._profile()
        except (settings.SettingsError, ai.AIError) as exc:
            self._say(str(exc), error=True)
            return
        self.busy = True
        self.connect_button.configure(state="disabled")
        self._say("正在连接 %s …" % urlsplit(client.base_url).netloc)

        def work():
            report = {"signature": signature, "models": [], "chat": "未知"}
            try:
                models = client.list_models()
                report["models"] = models
                if self.model.get().strip() not in models and models:
                    report["model"] = models[0]
                try:
                    client.probe_chat()
                    report["chat"] = "通过"
                except ai.AIError as exc:
                    report["chat"] = str(exc)
                report["ok"] = True
            except Exception as exc:          # 网络或鉴权失败
                report["ok"], report["error"] = False, str(exc)
            self.queue.put(report)

        threading.Thread(target=work, daemon=True).start()
        self.after(120, self._poll, refresh_only)

    def _poll(self, refresh_only=False):
        if self.closed:
            return
        try:
            report = self.queue.get_nowait()
        except queue.Empty:
            self.after(120, self._poll, refresh_only)
            return
        self.busy = False
        self.connect_button.configure(state="normal")
        if not report.get("ok"):
            self._say("连接失败：" + str(report.get("error")), error=True)
            return
        models = report.get("models", [])
        if models:
            self.model_box.configure(values=models)
        if report.get("model"):
            self.model.set(report["model"])
        if refresh_only:
            self._say("已获取 %d 个可用模型。" % len(models))
            return
        chat = report.get("chat", "未知")
        try:
            endpoint = settings.normalize_base_url(self.endpoint.get())
            verified = datetime.now().astimezone().isoformat(timespec="seconds") if chat == "通过" else ""
            self.store.save_ai(self.key.get().strip() or None, enabled=True,
                               model=self.model.get().strip() or settings.MODELS[0],
                               base_url=endpoint, verified_at=verified)
        except (settings.SettingsError, ai.AIError) as exc:
            self._say(str(exc), error=True)
            return
        self.key.set("")
        self.delete_button.configure(state="normal")
        if chat == "通过":
            self._say("已连接并保存：%s，对话可用。" % self.model.get(), error=False)
        else:
            self._say("已保存：%s。%s" % (self.model.get(), chat), error=False, warn=True)
        self.tested = (report["signature"], datetime.now().astimezone().isoformat(timespec="seconds"))

    def _say(self, text, *, error=False, warn=False):
        self.state.set(text)
        self.status.configure(fg="#b42318" if error else "#7a4d00" if warn else "#0f766e")

    def _delete(self):
        if self.busy:
            return
        if messagebox.askyesno("删除本机 Key", "删除加密保存的模型 Key 并停用 Agent？", parent=self):
            try:
                self.store.delete_key()
            except settings.SettingsError as exc:
                self._say(str(exc), error=True)
                return
            self._say("已删除本机 Key，Agent 已停用。")
            self.delete_button.configure(state="disabled")

    def _close(self):
        self.closed = True
        if self.poll_job:
            self.after_cancel(self.poll_job)
        self.key.set("")
        self.destroy()


class ImaDialog(tk.Toplevel):
    def __init__(self, master, store):
        super().__init__(master)
        self.title("DeepAudit · ima 知识库连接")
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)
        self.store = store
        body = ttk.Frame(self, padding=22)
        body.pack(fill="both")
        ttk.Label(body, text="ima 只读知识库接入", font=(FONT[0], 15, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        self.client_id, self.key = tk.StringVar(), tk.StringVar()
        for row, title, value in ((1, "Client ID", self.client_id), (2, "API Key", self.key)):
            ttk.Label(body, text=title).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=9)
            ttk.Entry(body, textvariable=value, show="•", width=52).grid(row=row, column=1, sticky="ew")
        ttk.Label(body, text="已有 ima 凭证已加密保存，留空可保留。" if store.has_ima else "尚未配置 ima 凭证。").grid(row=3, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Label(body, text="请在 ima 官方开放接口页自行获取 Client ID 与 API Key。\n"
                  "本版仅搜索账号有权访问的知识库，不导出整库，不使用登录 Cookie。\n"
                  "关注知识库不等于获得接口读取权限；保存后搜索库名即可验证。",
                  wraplength=560, justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=12)
        self.state = tk.StringVar()
        ttk.Label(body, textvariable=self.state, wraplength=560).grid(row=5, column=0, columnspan=2, sticky="w")
        actions = ttk.Frame(body)
        actions.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        ttk.Button(actions, text="打开 ima 官方配置页", command=lambda: webbrowser.open("https://ima.qq.com/agent-interface")).pack(side="left")
        ttk.Button(actions, text="移除连接", command=self._delete).pack(side="left", padx=8)
        ttk.Button(actions, text="加密保存", command=self._save).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _save(self):
        client_id, key = self.client_id.get().strip(), self.key.get().strip()
        if not client_id and not key and self.store.has_ima:
            self._close()
            return
        try:
            self.store.save_ima(client_id, key)
        except settings.SettingsError as exc:
            self.state.set(str(exc))
            return
        self._close()

    def _delete(self):
        if messagebox.askyesno("移除 ima 连接", "仅删除本机加密凭证，不删除 ima 账号或知识库。是否移除？", parent=self):
            try:
                self.store.delete_ima()
            except settings.SettingsError as exc:
                self.state.set(str(exc))
                return
            self._close()

    def _close(self):
        self.client_id.set("")
        self.key.set("")
        self.destroy()
