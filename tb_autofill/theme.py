"""界面设计令牌与通用部件（Claude 式暖白纸面）。

暖白画布、墨色正文、衬线标题与单一陶土色强调；卡片只用发丝边框，
不用投影与渐变。克制的目的是让金额与结论成为页面上唯一抢眼的东西。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ---------- 颜色令牌 ----------
PAPER = "#F7F5F0"            # 暖白纸面
PAPER_DEEP = "#EFECE4"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#FBFAF6"
INK = "#1F1E1D"
INK_SOFT = "#3B3A37"
MUTED = "#716F69"
HAIRLINE = "#E4E0D5"
HAIRLINE_STRONG = "#D3CEC0"

ACCENT = "#B14E2C"           # 陶土色：只给主操作与当前项（白字对比度 5.2:1）
ACCENT_DARK = "#98401F"
ACCENT_SOFT = "#F6EAE4"

RAIL = "#FBFAF6"
RAIL_HOVER = "#F1EEE6"
RAIL_ACTIVE = "#F6EAE4"

OK, WARN, BAD, INFO = "#3D7A5A", "#96701B", "#A33A2F", "#3E5F7A"
OK_SOFT, WARN_SOFT, BAD_SOFT, INFO_SOFT = "#EDF4EF", "#F8F1DF", "#F8E9E6", "#EDF2F6"

STATUS = {"ok": OK, "warn": WARN, "bad": BAD, "info": INFO, "muted": MUTED}
STATUS_SOFT = {"ok": OK_SOFT, "warn": WARN_SOFT, "bad": BAD_SOFT, "info": INFO_SOFT, "muted": PAPER_DEEP}

ROW_HEIGHT = 30

_BODY = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI")
_DISPLAY = ("宋体", "Georgia", "Times New Roman")
_MONO = ("Cascadia Mono", "Consolas", "Courier New")

FAMILY = {"body": _BODY[0], "display": _DISPLAY[0], "mono": _MONO[0]}
_REDUCED = None


def _pick(root, candidates):
    try:
        available = set(tkfont.families(root))
    except tk.TclError:
        return candidates[0]
    return next((name for name in candidates if name in available), candidates[0])


def install(root):
    """解析本机字体并安装 ttk 样式；重复调用只更新一次。"""
    FAMILY["body"] = _pick(root, _BODY)
    FAMILY["display"] = _pick(root, _DISPLAY)
    FAMILY["mono"] = _pick(root, _MONO)
    root.configure(bg=PAPER)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=(FAMILY["body"], 9), background=PAPER, foreground=INK)
    style.configure("TFrame", background=PAPER)
    style.configure("TLabel", background=PAPER, foreground=INK)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("Card.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("Card.TRadiobutton", background=SURFACE, foreground=INK, focuscolor=SURFACE,
                    font=(FAMILY["body"], 9))
    style.map("Card.TRadiobutton", background=[("active", SURFACE)])
    style.configure("Card.TCheckbutton", background=SURFACE, foreground=INK, focuscolor=SURFACE,
                    font=(FAMILY["body"], 9))
    style.map("Card.TCheckbutton", background=[("active", SURFACE)])
    style.configure("TEntry", fieldbackground=SURFACE, bordercolor=HAIRLINE, lightcolor=HAIRLINE,
                    darkcolor=HAIRLINE, insertcolor=INK, padding=6, relief="flat")
    style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
              darkcolor=[("focus", ACCENT)])
    style.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE, bordercolor=HAIRLINE,
                    lightcolor=HAIRLINE, darkcolor=HAIRLINE, arrowcolor=MUTED, padding=5, relief="flat")
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE)],
              bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
              darkcolor=[("focus", ACCENT)], fieldforeground=[("readonly", INK)])
    style.configure("TButton", background=SURFACE, foreground=INK, borderwidth=1,
                    bordercolor=HAIRLINE, lightcolor=HAIRLINE, darkcolor=HAIRLINE,
                    focusthickness=1, focuscolor=ACCENT, padding=(12, 6), relief="flat",
                    font=(FAMILY["body"], 9))
    style.map("TButton",
              background=[("pressed", PAPER_DEEP), ("active", SURFACE_ALT)],
              bordercolor=[("active", HAIRLINE_STRONG)],
              foreground=[("disabled", MUTED)])
    style.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF", borderwidth=0,
                    padding=(16, 8), font=(FAMILY["body"], 9))
    style.map("Accent.TButton", background=[("pressed", ACCENT_DARK), ("active", ACCENT_DARK)],
              foreground=[("disabled", "#E7C3B4")])
    style.configure("Ghost.TButton", background=SURFACE, foreground=ACCENT, borderwidth=0,
                    padding=(8, 4))
    style.map("Ghost.TButton", background=[("active", ACCENT_SOFT)], foreground=[("disabled", MUTED)])
    style.configure("Quiet.TButton", background=PAPER, foreground=MUTED, borderwidth=0,
                    padding=(8, 4))
    style.map("Quiet.TButton", background=[("active", PAPER_DEEP)], foreground=[("active", INK)])
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=INK,
                    borderwidth=0, rowheight=ROW_HEIGHT, font=(FAMILY["body"], 9))
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("Treeview.Heading", background=SURFACE, foreground=MUTED, relief="flat",
                    font=(FAMILY["body"], 8), padding=(8, 8))
    style.map("Treeview.Heading", background=[("active", SURFACE_ALT)])
    style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", INK)])
    for bar in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(bar, background=HAIRLINE, troughcolor=PAPER, borderwidth=0,
                        arrowsize=12, width=9)
        style.map(bar, background=[("active", HAIRLINE_STRONG)])
    style.configure("TProgressbar", background=ACCENT, troughcolor=PAPER_DEEP, borderwidth=0,
                    thickness=3)
    return style


def font(size=9, *, weight="normal", family="body"):
    return (FAMILY[family], size, weight)


def mono(size=9, *, weight="normal"):
    return (FAMILY["mono"], size, weight)


def color_for(status):
    return STATUS.get(status, MUTED)


def soft_for(status):
    return STATUS_SOFT.get(status, PAPER_DEEP)


class RoundCard(tk.Frame):
    """纸面卡片：发丝边框 + 内边距，内容用普通 Frame 承载，高度随内容自然展开。

    Tk 没有原生圆角容器，这里选择「直角 + 1px 边框」的克制做法：
    在任何字体与缩放下都不会出现圆角遮不住内容、或高度测量失败的问题。
    """

    def __init__(self, parent, *, pad=18, bg=SURFACE, hover=False):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=1,
                         highlightbackground=HAIRLINE, highlightcolor=HAIRLINE)
        self._border, self._hover = HAIRLINE, hover
        self.body = tk.Frame(self, bg=bg)
        self.body.pack(fill="both", expand=True, padx=pad, pady=pad)
        if hover:
            for widget in (self, self.body):
                widget.bind("<Enter>", lambda _event: self.configure(highlightbackground=ACCENT), add="+")
                widget.bind("<Leave>", lambda _event: self.configure(highlightbackground=HAIRLINE), add="+")


def _mix(start, end, ratio):
    left = [int(start[index:index + 2], 16) for index in (1, 3, 5)]
    right = [int(end[index:index + 2], 16) for index in (1, 3, 5)]
    return "#" + "".join(f"{round(a + (b - a) * ratio):02x}" for a, b in zip(left, right))


# ---------- 动效 ----------
def motion_enabled():
    """尊重系统「减少动画」设置；测试可用环境变量强制关闭。"""
    global _REDUCED
    if _REDUCED is not None:
        return _REDUCED
    if os.environ.get("DEEPAUDIT_REDUCED_MOTION") == "1":
        _REDUCED = False
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop\WindowMetrics") as key:
            _REDUCED = str(winreg.QueryValueEx(key, "MinAnimate")[0]) == "1"
    except (OSError, ImportError):
        _REDUCED = True
    return _REDUCED


def _alive(widget):
    try:
        return bool(widget.winfo_exists())
    except tk.TclError:
        return False


def _later(widget, milliseconds, func, *args):
    """把回调挂到顶层窗口：子控件销毁后不会留下无效的 after 命令。"""
    try:
        host = widget.winfo_toplevel()
    except tk.TclError:
        return
    try:
        host.after(milliseconds, func, *args)
    except tk.TclError:
        pass


def fade(widget, *, start=None, end=None, steps=5, interval=24, attribute="bg", done=None):
    """按帧插值背景色，制造极轻的淡入；终点取组件自身底色。"""
    if end is None:
        try:
            end = widget.cget(attribute)
        except tk.TclError:
            end = SURFACE
    if start is None:
        start = _mix(end, "#000000", 0.04)
    if not motion_enabled():
        try:
            widget.configure(**{attribute: end})
        except tk.TclError:
            pass
        if done:
            done()
        return

    def step(index):
        if not _alive(widget):
            return
        try:
            widget.configure(**{attribute: _mix(start, end, index / steps)})
        except tk.TclError:
            return
        if index >= steps:
            if done:
                done()
            return
        _later(widget, interval, step, index + 1)

    step(0)


def reveal(widgets, *, delay=0, stagger=40, steps=5, interval=24):
    """入场编排：一组卡片错峰淡入，总时长控制在 250ms 内。"""
    for index, widget in enumerate(widgets):
        _later(widget, delay + index * stagger,
               lambda item=widget: fade(item, steps=steps, interval=interval))


def glide(widget, *, axis="y", to=0, steps=6, interval=16):
    """导航指示条滑动：只改 place 坐标，不触发布局重排。"""
    if not motion_enabled():
        widget.place_configure(**{axis: to})
        return
    info = widget.place_info()
    try:
        start = int(float(info.get(axis, 0)))
    except (TypeError, ValueError):
        start = 0
    if start == to:
        return

    def step(index):
        if not _alive(widget):
            return
        value = round(start + (to - start) * (index / steps))
        try:
            widget.place_configure(**{axis: value})
        except tk.TclError:
            return
        if index < steps:
            _later(widget, interval, step, index + 1)

    step(1)


# ---------- 通用部件 ----------
class StatTile(tk.Frame):
    """指标块：大号数字 + 一行标签，用发丝线分隔，不套卡片。"""

    def __init__(self, parent, title, value="—", hint=""):
        super().__init__(parent, bg=parent.cget("bg"))
        tk.Label(self, text=title, bg=self.cget("bg"), fg=MUTED,
                 font=(FAMILY["body"], 8), anchor="w").pack(anchor="w")
        self.value = tk.Label(self, text=value, bg=self.cget("bg"), fg=INK,
                              font=(FAMILY["display"], 30, "bold"), anchor="w")
        self.value.pack(anchor="w", pady=(2, 0))
        self.hint = tk.Label(self, text=hint, bg=self.cget("bg"), fg=MUTED,
                             font=(FAMILY["body"], 8), anchor="w", justify="left",
                             wraplength=230)
        self.hint.pack(anchor="w")

    def update_value(self, value, hint=None, tone=None):
        self.value.configure(text=str(value), fg=tone or INK)
        if hint is not None:
            self.hint.configure(text=str(hint))
