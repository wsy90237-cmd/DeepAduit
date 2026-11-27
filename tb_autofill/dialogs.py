"""主题化消息框：替代原生 `tkinter.messagebox`。

原生消息框是系统灰底、System 字体、系统图标，跟暖白纸面完全脱节。这里用同一套
令牌重画：PAPER 画布、SURFACE 卡片、1px 发丝边框、衬线标题，主操作橙色置右；
`Esc` 取消、`Enter` 确认。函数签名与返回值跟 `messagebox` 保持一致，调用点
只需把导入换成 `from . import dialogs as messagebox`。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import layout, theme

# 待按类型的着色徽记：不用 emoji，全部用 Canvas 画。
_TONES = {
    "info": (theme.INFO, theme.INFO_SOFT),
    "ok": (theme.OK, theme.OK_SOFT),
    "warn": (theme.WARN, theme.WARN_SOFT),
    "bad": (theme.BAD, theme.BAD_SOFT),
}

# 测试注入点：返回按钮 id 即可免去人工点击；正常运行保持 None。
_RESPONDER = None


def _alive(widget):
    try:
        return bool(widget.winfo_exists())
    except tk.TclError:
        return False


def _badge(parent, kind):
    """按类型画一枚圆形徽记（对勾 / 感叹号 / 叉 / i）。"""
    color, soft = _TONES.get(kind, _TONES["info"])
    size = theme.px(parent, 20)
    canvas = tk.Canvas(parent, width=size, height=size, bg=theme.SURFACE,
                       highlightthickness=0, bd=0, takefocus=0)
    inset = max(1, size // 16)
    stroke = max(1, size // 12)
    canvas.create_oval(inset, inset, size - inset, size - inset,
                       fill=soft, outline=color, width=stroke)
    mid = size / 2.0
    if kind == "ok":
        canvas.create_line(size * 0.28, size * 0.52, size * 0.44, size * 0.68, size * 0.72, size * 0.34,
                           fill=color, width=stroke, capstyle="round", joinstyle="round")
    elif kind == "bad":
        canvas.create_line(size * 0.34, size * 0.34, size * 0.66, size * 0.66,
                           fill=color, width=stroke, capstyle="round")
        canvas.create_line(size * 0.66, size * 0.34, size * 0.34, size * 0.66,
                           fill=color, width=stroke, capstyle="round")
    elif kind == "warn":
        canvas.create_line(mid, size * 0.28, mid, size * 0.58,
                           fill=color, width=stroke, capstyle="round")
        canvas.create_oval(mid - stroke / 2, size * 0.68 - stroke / 2,
                           mid + stroke / 2, size * 0.68 + stroke / 2, fill=color, outline=color)
    else:
        canvas.create_line(mid, size * 0.30, mid, size * 0.56,
                           fill=color, width=stroke, capstyle="round")
        canvas.create_oval(mid - stroke / 2, size * 0.70 - stroke / 2,
                           mid + stroke / 2, size * 0.70 + stroke / 2, fill=color, outline=color)
    return canvas


def _default_parent(parent):
    """`parent=None` 时退回当前焦点所在顶层窗口，其次退回根窗口。"""
    if parent is not None and _alive(parent):
        return parent
    root = getattr(tk, "_default_root", None)
    if root is None:
        return None
    try:
        focused = root.focus_get()
    except (tk.TclError, KeyError):
        focused = None
    if focused is not None and _alive(focused):
        try:
            return focused.winfo_toplevel()
        except tk.TclError:
            pass
    return root


def _center_on_parent(window, parent):
    """相对父窗口居中，并收进工作区。"""
    try:
        parent.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        window.update_idletasks()
        width, height = window.winfo_width(), window.winfo_height()
    except tk.TclError:
        return
    if pw <= 1 or ph <= 1:
        return
    area_w, area_h = theme.work_area(window)
    left = px + max(0, (pw - width) // 2)
    top = py + max(0, (ph - height) // 3)
    left = min(max(0, left), max(0, area_w - width))
    top = min(max(0, top), max(0, area_h - height))
    try:
        window.geometry(f"+{left}+{top}")
    except tk.TclError:
        pass


class _MessageBox(tk.Toplevel):
    """一个纸面消息框：图标、正文、按钮行，全部走 theme 令牌。"""

    def __init__(self, parent, title, message, *, kind, buttons, results, default, cancel):
        super().__init__(parent)
        self.withdraw()
        self.title(title or "")
        self.resizable(False, False)
        self.result = results.get(cancel)
        self._results, self._cancel = results, cancel
        if parent is not None:
            try:
                self.transient(parent)
            except tk.TclError:
                pass

        # 白底一张纸：标题只出现一次（与图标同行），正文在下，按钮靠右；不再套边框卡片。
        surface = theme.SURFACE
        self.configure(bg=surface)
        outer = tk.Frame(self, bg=surface)
        outer.pack(fill="both", expand=True, padx=24, pady=(20, 18))
        head = tk.Frame(outer, bg=surface)
        head.pack(fill="x")
        self.badge = _badge(head, kind)
        self.badge.pack(side="left", padx=(0, 10))
        tk.Label(head, text=title or "", bg=surface, fg=theme.INK,
                 font=(theme.FAMILY["body"], 11, "bold"), anchor="w").pack(side="left")
        self.text = tk.Label(outer, text=message, bg=surface, fg=theme.INK_SOFT,
                             font=(theme.FAMILY["body"], 10), justify="left", anchor="w",
                             wraplength=theme.px(self, 420))
        self.text.pack(fill="both", expand=True, pady=(10, 0))

        actions = tk.Frame(outer, bg=surface)
        actions.pack(fill="x", pady=(18, 0))
        self.buttons = {}
        styles = {"primary": "Accent.TButton", "secondary": "CardQuiet.TButton",
                  "ghost": "CardQuiet.TButton"}
        for name, label, role in buttons:
            button = ttk.Button(actions, text=label, style=styles.get(role, "CardQuiet.TButton"),
                                command=lambda value=name: self.choose(value))
            # 先 pack 的在最右：主操作固定靠右，次要操作依次向左。
            button.pack(side="right", padx=(8, 0) if self.buttons else 0)
            self.buttons[name] = button

        self._default = default
        self.bind("<Escape>", lambda _event: self.choose(cancel))
        self.bind("<Return>", self._on_return)
        self.protocol("WM_DELETE_WINDOW", lambda: self.choose(cancel))
        focus_target = self.buttons.get(default) or self.buttons.get(cancel)
        if focus_target is not None:
            try:
                focus_target.focus_set()
            except tk.TclError:
                pass

        layout.autofit(self, min_width=360, min_height=170, max_width=760)
        _center_on_parent(self, parent)
        self.deiconify()
        self.lift()

    def _on_return(self, _event=None):
        """回车执行当前获得焦点的按钮；焦点不在按钮上时才走默认动作。"""
        focus = self.focus_get()
        for name, button in self.buttons.items():
            if button is focus:
                self.choose(name)
                return "break"
        self.choose(self._default)
        return "break"

    def choose(self, name):
        """按按钮 id 收尾；未知 id 只关窗，不改变语义。"""
        if not _alive(self):
            return
        if name in self._results:
            self.result = self._results[name]
        try:
            self.grab_release()
            self.destroy()
        except tk.TclError:
            pass

    def run(self):
        if _alive(self):
            try:
                self.grab_set()
                self.wait_window(self)
            except tk.TclError:
                pass
        return self.result


def _open(kind, title, message, buttons, results, *, parent=None, default=None, cancel=None):
    """建框、跑模态、返回结果。"""
    parent = _default_parent(parent)
    default = default or buttons[0][0]
    cancel = cancel or buttons[-1][0]
    # 消息框会抢走 grab，关掉后要把模态还给原来的窗口，否则父弹窗会「掉模态」。
    keep_grab = False
    if parent is not None:
        try:
            keep_grab = parent.grab_current() is parent
        except tk.TclError:
            keep_grab = False
    box = None
    try:
        box = _MessageBox(parent, title, message, kind=kind, buttons=buttons,
                          results=results, default=default, cancel=cancel)
    except tk.TclError:
        return results.get(default)
    responder = _RESPONDER
    if responder is not None:
        def answer():
            try:
                box.choose(responder(box))
            except Exception:
                box.choose(cancel)
        try:
            box.after(1, answer)
        except tk.TclError:
            pass
    result = box.run()
    if keep_grab and _alive(parent):
        try:
            parent.grab_set()
        except tk.TclError:
            pass
    return result


# ---------- 与 messagebox 同签名的入口 ----------

def showinfo(title, message="", *, parent=None, **_options):
    """提示框；返回 "ok"，与原生一致。"""
    return _open("info", title, message,
                 [("ok", "确定", "primary")], {"ok": "ok"}, parent=parent)


def showwarning(title, message="", *, parent=None, **_options):
    """警告框；返回 "ok"。"""
    return _open("warn", title, message,
                 [("ok", "确定", "primary")], {"ok": "ok"}, parent=parent)


def showerror(title, message="", *, parent=None, **_options):
    """错误框；返回 "ok"。"""
    return _open("bad", title, message,
                 [("ok", "确定", "primary")], {"ok": "ok"}, parent=parent)


def askyesno(title, message="", *, parent=None, **_options):
    """是/否；返回 True / False。"""
    return _open("ask", title, message,
                 [("yes", "是", "primary"), ("no", "否", "secondary")],
                 {"yes": True, "no": False}, parent=parent, default="yes", cancel="no")


def askokcancel(title, message="", *, parent=None, **_options):
    """确定/取消；返回 True / False。"""
    return _open("ask", title, message,
                 [("ok", "确定", "primary"), ("cancel", "取消", "secondary")],
                 {"ok": True, "cancel": False}, parent=parent, default="ok", cancel="cancel")


def askyesnocancel(title, message="", *, parent=None, **_options):
    """是/否/取消；返回 True / False / None。"""
    return _open("ask", title, message,
                 [("yes", "是", "primary"), ("no", "否", "secondary"), ("cancel", "取消", "ghost")],
                 {"yes": True, "no": False, "cancel": None},
                 parent=parent, default="yes", cancel="cancel")
