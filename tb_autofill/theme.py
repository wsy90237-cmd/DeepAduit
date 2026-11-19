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
PAPER = "#FEFAEF"            # 取自品牌图的暖米色
PAPER_DEEP = "#F2EBDD"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#FCFAF4"
INK = "#291C13"              # 品牌图近黑，不用纯黑
INK_SOFT = "#3F332A"
MUTED = "#746859"
HAIRLINE = "#E7DCC8"
HAIRLINE_STRONG = "#D6C7AC"

ACCENT = "#B5520D"           # 橙色操作色：白字 5.0:1、米色底 4.7:1
ACCENT_DARK = "#97420A"
ACCENT_SOFT = "#FBE7D3"

RAIL = "#FDFBF3"
RAIL_HOVER = "#F5EEE1"
RAIL_ACTIVE = "#FBE7D3"

OK, WARN, BAD, INFO = "#2F7552", "#8D6414", "#A33A2F", "#3E5F7A"
OK_SOFT, WARN_SOFT, BAD_SOFT, INFO_SOFT = "#EAF4EE", "#FBF1DD", "#FAE9E5", "#EBF1F7"

STATUS = {"ok": OK, "warn": WARN, "bad": BAD, "info": INFO, "muted": MUTED}
STATUS_SOFT = {"ok": OK_SOFT, "warn": WARN_SOFT, "bad": BAD_SOFT, "info": INFO_SOFT, "muted": PAPER_DEEP}

ROW_HEIGHT = 30

_BODY = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI")
_DISPLAY = ("宋体", "Georgia", "Times New Roman")
_LATIN = ("Georgia", "Times New Roman", "宋体")
_MONO = ("Cascadia Mono", "Consolas", "Courier New")

FAMILY = {"body": _BODY[0], "display": _DISPLAY[0], "latin": _LATIN[0], "mono": _MONO[0]}
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
    FAMILY["latin"] = _pick(root, _LATIN)
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
                    borderwidth=0, rowheight=px(root, ROW_HEIGHT), font=(FAMILY["body"], 9))
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("Treeview.Heading", background=SURFACE, foreground=MUTED, relief="flat",
                    font=(FAMILY["body"], 8), padding=(8, 8))
    style.map("Treeview.Heading", background=[("active", SURFACE_ALT)])
    style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", INK)])
    for bar in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(bar, background=HAIRLINE, troughcolor=PAPER, borderwidth=0,
                        arrowsize=px(root, 12), width=px(root, 9))
        style.map(bar, background=[("active", HAIRLINE_STRONG)])
    style.configure("TProgressbar", background=ACCENT, troughcolor=PAPER_DEEP, borderwidth=0,
                    thickness=3)
    return style


def font(size=9, *, weight="normal", family="body"):
    return (FAMILY[family], size, weight)


# ---------- 分辨率 / 缩放适配 ----------
# Tk 的几何参数是物理像素，字号按 DPI 放大。设计稿以 96 DPI 为准，
# 因此所有写死的像素常量都要经过 px() 换算，否则 125% / 150% 缩放下必然裁切。
BASE_DPI = 96.0


def ui_scale(root):
    """当前屏幕的界面缩放比（96 DPI = 1.0），限制在 0.8–2.0 之间。"""
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except (tk.TclError, ValueError):
        dpi = BASE_DPI
    if dpi <= 0:
        dpi = BASE_DPI
    return min(2.0, max(0.8, dpi / BASE_DPI))


def px(root, value):
    """设计像素 → 当前缩放下的屏幕像素。"""
    return max(1, int(round(value * ui_scale(root))))


def work_area(root):
    """主显示器可用工作区（已扣任务栏），取不到时退回整屏尺寸。"""
    try:
        from ctypes import byref, windll
        from ctypes.wintypes import RECT

        rect = RECT()
        if windll.user32.SystemParametersInfoW(0x0030, 0, byref(rect), 0):
            width, height = rect.right - rect.left, rect.bottom - rect.top
            if width > 320 and height > 240:
                return width, height
    except (AttributeError, OSError, ValueError):
        pass
    return root.winfo_screenwidth(), root.winfo_screenheight()


def fit_window(window, width, height, *, min_width=0, min_height=0, margin=12, fit_content=False):
    """把窗口限制在可用工作区内并居中，返回最终 (宽, 高)。

    小屏（1366×768）或 150% 缩放时，固定尺寸对话框会超出屏幕导致按钮点不到；
    这里以工作区为上界，同时把 minsize 一并收紧。fit_content=True 时按内容收紧高度，
    避免表单类对话框下方留一大片空白。
    """
    area_w, area_h = work_area(window)
    limit_w, limit_h = max(320, area_w - margin * 2), max(240, area_h - margin * 2)
    final_w = min(max(px(window, width), 320), limit_w)
    final_h = min(max(px(window, height), 240), limit_h)
    if fit_content:
        try:
            window.update_idletasks()
            final_h = min(final_h, max(240, window.winfo_reqheight() + px(window, 8)))
        except tk.TclError:
            pass
    if min_width or min_height:
        window.minsize(min(px(window, min_width) if min_width else 0, final_w),
                       min(px(window, min_height) if min_height else 0, final_h))
    left = max(0, (area_w - final_w) // 2)
    top = max(0, (area_h - final_h) // 2)
    window.geometry(f"{final_w}x{final_h}+{left}+{top}")
    return final_w, final_h


def wrap_to_parent(label, *, ratio=1.0, minimum=160, padding=0):
    """让换行宽度跟随父容器，避免固定 wraplength 在大/小屏上过宽或过窄。"""

    def resize(event):
        width = int((event.width - padding) * ratio)
        if width >= minimum:
            try:
                label.configure(wraplength=width)
            except tk.TclError:
                pass

    target = label.master
    target.bind("<Configure>", resize, add="+")
    return label


def fit_columns(tree, spec, *, minimum=56):
    """按控件实际宽度等比分配 Treeview 列宽。

    固定像素列宽在 125%/150% 缩放或窄窗口下会把「差额」「来源科目」挤没，
    这里保留设计稿的比例，宽度随窗口变化重算。
    """
    total = float(sum(spec.values())) or 1.0

    def resize(event):
        width = event.width
        if width < minimum * 2:
            return
        for key, weight in spec.items():
            try:
                tree.column(key, width=max(minimum, int(width * weight / total)))
            except tk.TclError:
                return

    tree.bind("<Configure>", resize, add="+")
    return tree


def style_window_caption(window):
    if window.tk.call("tk", "windowingsystem") != "win32":
        return
    from ctypes import byref, sizeof, windll
    from ctypes.wintypes import DWORD, HWND, UINT

    try:
        ancestor = windll.user32.GetAncestor
        ancestor.argtypes = (HWND, UINT)
        ancestor.restype = HWND
        handle = ancestor(window.winfo_id(), 2)
        if not handle:
            return
        border_color, caption_color, text_color = 34, 35, 36
        for attribute, value in ((border_color, HAIRLINE), (caption_color, PAPER), (text_color, INK)):
            color = DWORD(int.from_bytes(bytes.fromhex(value[1:]), "little"))
            windll.dwmapi.DwmSetWindowAttribute(HWND(handle), attribute, byref(color), sizeof(color))
    except (AttributeError, OSError):
        return


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
    """指标块：一个数字 + 一个短标签；说明文字只在调用方明确需要时才出现。"""

    def __init__(self, parent, title, value="—", hint=""):
        super().__init__(parent, bg=parent.cget("bg"))
        tk.Label(self, text=title, bg=self.cget("bg"), fg=MUTED,
                 font=(FAMILY["body"], 8), anchor="w").pack(anchor="w")
        self.value = tk.Label(self, text=value, bg=self.cget("bg"), fg=INK,
                              font=(FAMILY["display"], 30, "bold"), anchor="w")
        self.value.pack(anchor="w", pady=(2, 0))
        self.hint = None
        if hint:
            self._hint_label(hint)

    def _hint_label(self, hint):
        if self.hint is None:
            self.hint = tk.Label(self, text=hint, bg=self.cget("bg"), fg=MUTED,
                                 font=(FAMILY["body"], 8), anchor="w", justify="left",
                                 wraplength=230)
            self.hint.pack(anchor="w")
        else:
            self.hint.configure(text=hint)

    def update_value(self, value, hint=None, tone=None):
        self.value.configure(text=str(value), fg=tone or INK)
        if hint:
            self._hint_label(hint)
