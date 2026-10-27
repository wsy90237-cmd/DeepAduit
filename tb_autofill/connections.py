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
    def __init__(self, master, store):
        super().__init__(master)
        self.title("DeepAudit · 模型与 API 连接")
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)
        self.configure(bg=BG)
        self.store, self.queue = store, queue.Queue()
        self.testing, self.closed, self.tested = False, False, None
        self.poll_job = None
        self.protocol("WM_DELETE_WINDOW", self._close)
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", padx=22, pady=20)
        body.columnconfigure(1, weight=1)
        tk.Label(body, text="连接审计 Agent 的模型服务", bg=BG, fg=FG, font=(FONT[0], 15, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 16))
        self.endpoint = tk.StringVar(value=store.data.get("base_url", settings.DEFAULT_BASE_URL))
        self.key = tk.StringVar()
        self.model = tk.StringVar(value=store.data.get("model", settings.MODELS[0]))
        self.enabled = tk.BooleanVar(value=bool(store.data.get("ai_enabled")))
        self.state = tk.StringVar(value="已加密保存 Key；留空可保留原服务凭证。" if store.has_key else "尚未配置；请在本窗口输入 Key。")
        for row, title in ((1, "API 地址"), (2, "API Key"), (3, "模型 ID")):
            tk.Label(body, text=title, bg=BG, fg=FG, font=FONT).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=8)
        ttk.Combobox(body, textvariable=self.endpoint, values=(settings.DEFAULT_BASE_URL,), width=57).grid(row=1, column=1, sticky="ew")
        ttk.Entry(body, textvariable=self.key, show="•", width=57).grid(row=2, column=1, sticky="ew")
        self.model_box = ttk.Combobox(body, textvariable=self.model, values=settings.MODELS, width=40)
        self.model_box.grid(row=3, column=1, sticky="w")
        ttk.Checkbutton(body, text="启用审计 Agent（不会自动上传账套）", variable=self.enabled).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(12, 7))
        tk.Label(body, text="默认接入 DeepSeek；也可填支持 /models 与 JSON Chat Completions 的 HTTPS 兼容接口。\n"
                 "Key 只发送到上方 API 地址。切换地址必须重新输入对应 Key，原 Key 不会跨服务复用。",
                 bg=BG, fg="#68778d", font=(FONT[0], 9), justify="left", wraplength=590).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=8)
        tk.Label(body, textvariable=self.state, bg=BG, fg="#2563eb", font=(FONT[0], 9),
                 wraplength=590, justify="left").grid(row=6, column=0, columnspan=2, sticky="w", pady=10)
        controls = tk.Frame(body, bg=BG)
        controls.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.auth_button = ttk.Button(controls, text="验证凭证 / 获取模型", command=lambda: self._test("models"))
        self.auth_button.pack(side="left")
        self.chat_button = ttk.Button(controls, text="验证对话能力", command=lambda: self._test("chat"))
        self.chat_button.pack(side="left", padx=8)
        self.save_button = ttk.Button(controls, text="加密保存", style="Accent.TButton", command=self._save)
        self.save_button.pack(side="right")
        footer = tk.Frame(body, bg=BG)
        footer.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(footer, text="获取 DeepSeek Key", command=lambda: webbrowser.open("https://platform.deepseek.com/api_keys")).pack(side="left")
        ttk.Button(footer, text="删除本机模型 Key", command=self._delete).pack(side="left", padx=8)
        ttk.Button(footer, text="关闭", command=self._close).pack(side="right")
        for variable in (self.endpoint, self.key, self.model):
            variable.trace_add("write", self._changed)

    def _changed(self, *_args):
        self.tested = None
        self.state.set("配置已更改，尚未验证。更换地址不会转发原服务 Key。")

    def _profile(self):
        endpoint = settings.normalize_base_url(self.endpoint.get())
        key = self.key.get().strip() or self.store.get_key(endpoint)
        key = settings.validate_key(key)
        model = self.model.get().strip()
        signature = (endpoint, model, hashlib.sha256(key.encode()).hexdigest())
        return ai.DeepSeekClient(key, model, base_url=endpoint), signature

    def _test(self, mode):
        if self.testing:
            return
        try:
            client, signature = self._profile()
        except (settings.SettingsError, ai.AIError) as exc:
            self.state.set(str(exc))
            return
        if mode == "chat" and not messagebox.askyesno("验证对话能力", "将向所选模型发送固定测试问题，不包含账套或资料，但会使用少量 API token。是否测试？", parent=self):
            return
        self.testing = True
        for button in (self.auth_button, self.chat_button, self.save_button):
            button.configure(state="disabled")
        self.state.set(f"正在验证 {urlsplit(client.base_url).netloc} …")

        def work():
            try:
                value = client.list_models() if mode == "models" else client.probe_chat()
                self.queue.put((True, mode, signature, value))
            except Exception as exc:
                self.queue.put((False, mode, signature, str(exc)))
        threading.Thread(target=work, daemon=True).start()
        self.poll_job = self.after(120, self._poll)

    def _poll(self):
        if self.closed:
            return
        try:
            ok, mode, signature, value = self.queue.get_nowait()
        except queue.Empty:
            self.poll_job = self.after(120, self._poll)
            return
        self.testing = False
        for button in (self.auth_button, self.chat_button, self.save_button):
            button.configure(state="normal")
        try:
            current = self._profile()[1]
        except (settings.SettingsError, ai.AIError):
            current = None
        if current != signature:
            self.state.set("测试期间配置发生变化，旧结果已丢弃，请重新测试。")
            return
        if not ok:
            self.state.set("验证失败：" + value)
            return
        if mode == "models":
            self.model_box.configure(values=value)
            if self.model.get() not in value:
                self.model.set(value[0])
            self.state.set(f"鉴权与模型列表通过（{len(value)} 个模型）。对话能力需单独验证。")
        else:
            self.state.set("对话与 JSON 返回能力验证通过；未上传账套。")
        self.tested = (self._profile()[1], datetime.now().astimezone().isoformat(timespec="seconds"))

    def _save(self):
        if self.testing:
            return
        try:
            if not self.key.get().strip() and not self.store.has_key and not self.enabled.get():
                self.store.save_ai(None, enabled=False, model=self.model.get().strip(), base_url=self.endpoint.get())
                self._close()
                return
            _client, signature = self._profile()
            verified = self.tested[1] if self.tested and self.tested[0] == signature else ""
            self.store.save_ai(self.key.get().strip() or None, enabled=self.enabled.get(),
                               model=self.model.get().strip(), base_url=self.endpoint.get(), verified_at=verified)
        except (settings.SettingsError, ai.AIError) as exc:
            self.state.set(str(exc))
            return
        self._close()

    def _delete(self):
        if self.testing:
            return
        if messagebox.askyesno("删除本机 Key", "删除加密保存的模型 Key 并停用 Agent？", parent=self):
            try:
                self.store.delete_key()
            except settings.SettingsError as exc:
                self.state.set(str(exc))
                return
            self._close()

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
