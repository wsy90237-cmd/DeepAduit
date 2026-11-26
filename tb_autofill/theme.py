"""界面设计令牌与通用部件（Claude 式暖白纸面）。

暖白画布、墨色正文、衬线标题与单一陶土色强调；卡片只用发丝边框，
不用投影与渐变。克制的目的是让金额与结论成为页面上唯一抢眼的东西。
"""

from __future__ import annotations

import os
import weakref
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
_DISPLAY = ("Noto Serif SC", "Source Han Serif SC", "思源宋体", "宋体", "Georgia", "Times New Roman")
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
    # 下拉列表与 Listbox 由 Tk 自己创建，样式只能走 option 数据库；
    # 不设的话弹出层仍是系统灰底黑字，与纸面脱节。
    for option, value in (
        ("*TCombobox*Listbox.background", SURFACE),
        ("*TCombobox*Listbox.foreground", INK),
        ("*TCombobox*Listbox.selectBackground", ACCENT_SOFT),
        ("*TCombobox*Listbox.selectForeground", INK),
        ("*TCombobox*Listbox.borderWidth", 1),
        ("*TCombobox*Listbox.highlightThickness", 0),
        ("*TCombobox*Listbox.font", (FAMILY["body"], 9)),
        ("*Listbox.background", SURFACE),
        ("*Listbox.foreground", INK),
        ("*Listbox.selectBackground", ACCENT_SOFT),
        ("*Listbox.selectForeground", INK),
        ("*Listbox.highlightThickness", 1),
        ("*Listbox.highlightBackground", HAIRLINE),
        ("*Listbox.highlightColor", ACCENT),
        ("*Listbox.borderWidth", 0),
    ):
        try:
            root.option_add(option, value)
        except tk.TclError:
            continue
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
              darkcolor=[("focus", ACCENT)], fieldbackground=[("readonly", SURFACE_ALT),
                                                             ("disabled", PAPER_DEEP)],
              foreground=[("disabled", MUTED)])
    style.configure("TCombobox", fieldbackground=SURFACE, background=SURFACE, bordercolor=HAIRLINE,
                    lightcolor=HAIRLINE, darkcolor=HAIRLINE, arrowcolor=MUTED, padding=5, relief="flat")
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE)],
              bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
              darkcolor=[("focus", ACCENT)], arrowcolor=[("active", ACCENT)],
              fieldforeground=[("readonly", INK)])
    style.configure("TButton", background=SURFACE, foreground=INK, borderwidth=1,
                    bordercolor=HAIRLINE, lightcolor=HAIRLINE, darkcolor=HAIRLINE,
                    focusthickness=1, focuscolor=ACCENT, padding=(12, 6), relief="flat",
                    font=(FAMILY["body"], 9))
    style.map("TButton",
              background=[("pressed", PAPER_DEEP), ("active", SURFACE_ALT)],
              bordercolor=[("active", HAIRLINE_STRONG)],
              foreground=[("disabled", MUTED)])
    style.configure("TMenubutton", background=SURFACE, foreground=INK, borderwidth=1,
                    bordercolor=HAIRLINE, lightcolor=HAIRLINE, darkcolor=HAIRLINE, arrowcolor=MUTED,
                    padding=(10, 6), relief="flat", font=(FAMILY["body"], 9))
    style.map("TMenubutton", background=[("active", SURFACE_ALT)], bordercolor=[("active", HAIRLINE_STRONG)])
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
    # 卡片（白底）里的轻按钮：底色跟卡片一致，不出现纸色色块。
    style.configure("CardQuiet.TButton", background=SURFACE, foreground=MUTED, borderwidth=0,
                    padding=(8, 4), focuscolor=SURFACE, focusthickness=0)
    style.map("CardQuiet.TButton", background=[("active", SURFACE_ALT)], foreground=[("active", INK)])
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=INK,
                    borderwidth=0, rowheight=px(root, ROW_HEIGHT), font=(FAMILY["body"], 9))
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("Treeview.Heading", background=SURFACE, foreground=MUTED, relief="flat",
                    font=(FAMILY["body"], 8, "bold"), padding=(8, 8))
    style.map("Treeview.Heading", background=[("active", SURFACE_ALT)])
    style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", INK)])
    # clam 自带的槽色与箭头会盖掉 troughcolor，这里显式声明，滚动条才和纸面一致。
    for bar in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(bar, background=HAIRLINE, troughcolor=PAPER, bordercolor=HAIRLINE,
                        darkcolor=HAIRLINE, lightcolor=PAPER, arrowcolor=MUTED, gripcount=0,
                        borderwidth=0, relief="flat", arrowsize=px(root, 12), width=px(root, 9))
        style.map(bar, background=[("active", HAIRLINE_STRONG), ("pressed", HAIRLINE_STRONG)],
                  arrowcolor=[("active", ACCENT), ("pressed", ACCENT)],
                  bordercolor=[("active", HAIRLINE_STRONG)])
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


# 最近一次由框架设定的窗口外框位置。Tk 的 geometry("WxH+X+Y") 用的是**外框**原点，
# 而 winfo_rootx/rooty 返回**客户区**原点（实测相差 8/31px）；若拿后者算「保持中心」，
# 每修正一次窗口就会右移 8、下移 31。这里把设定值记下来，测量不到时用它兜底。
_PLACEMENT = weakref.WeakKeyDictionary()


def remember_placement(window, x, y, width, height):
    """记录本模块给窗口设定的外框位置；窗口销毁后自动消失。"""
    try:
        _PLACEMENT[window] = (int(x), int(y), int(width), int(height))
    except (TypeError, ValueError, tk.TclError):
        pass


def last_placement(window):
    """取回上次设定的外框 (x, y, 宽, 高)；没有记录时返回 None。"""
    try:
        return _PLACEMENT.get(window)
    except (TypeError, tk.TclError):
        return None


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
    remember_placement(window, left, top, final_w, final_h)
    try:
        window.after_idle(lambda: bring_to_front(window))
    except tk.TclError:
        pass
    return final_w, final_h


def bring_to_front(window):
    """把弹窗带到最前并取得焦点：Windows 上从后台线程触发的对话框常落在主窗口后面。"""
    try:
        if not window.winfo_exists():
            return
        window.deiconify()
        window.lift()
        window.attributes("-topmost", True)
        window.focus_force()
        window.after(400, lambda: window.winfo_exists() and window.attributes("-topmost", False))
    except tk.TclError:
        pass


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


# ---------- 线性图标与小部件 ----------
# 图标按 24×24 网格写成折线，画在 Canvas 上随 DPI 缩放；不依赖图片文件或 emoji 字体。
ICONS = {
    "tb": [[(3, 10), (12, 3), (21, 10), (21, 20), (15, 20), (15, 14), (9, 14), (9, 20), (3, 20), (3, 10)]],
    "agent": [[(12, 3), (12, 8)], [(12, 16), (12, 21)], [(3, 12), (8, 12)], [(16, 12), (21, 12)],
              [(6.5, 6.5), (9, 9)], [(15, 15), (17.5, 17.5)], [(6.5, 17.5), (9, 15)], [(15, 9), (17.5, 6.5)]],
    "papers": [[(6, 3), (14, 3), (18, 7), (18, 21), (6, 21), (6, 3)], [(14, 3), (14, 7), (18, 7)],
               [(9, 12), (15, 12)], [(9, 16), (15, 16)]],
    "notes": [[(5, 4), (19, 4), (19, 20), (5, 20), (5, 4)], [(9, 4), (9, 20)], [(12, 9), (16, 9)],
              [(12, 13), (16, 13)]],
    "report": [[(4, 20), (20, 20)], [(7, 20), (7, 13)], [(12, 20), (12, 7)], [(17, 20), (17, 11)]],
    "security": [[(12, 2), (12, 5)], [(12, 19), (12, 22)], [(2, 12), (5, 12)], [(19, 12), (22, 12)],
                 [(4.9, 4.9), (7, 7)], [(17, 17), (19.1, 19.1)], [(4.9, 19.1), (7, 17)], [(17, 7), (19.1, 4.9)]],
}
_ICON_CIRCLES = {"security": (12, 12, 3.2)}


def draw_icon(canvas, name, x, y, size, color, *, tag="icon"):
    """在 (x, y) 处画一个 size×size 的线性图标，返回 tag 便于整体换色。"""
    scale = size / 24.0
    width = max(1.0, 1.6 * scale)
    for line in ICONS.get(name, ()):
        points = [coord for px_, py_ in line for coord in (x + px_ * scale, y + py_ * scale)]
        canvas.create_line(*points, fill=color, width=width, capstyle="round",
                           joinstyle="round", tags=(tag,))
    if name in _ICON_CIRCLES:
        cx, cy, r = _ICON_CIRCLES[name]
        canvas.create_oval(x + (cx - r) * scale, y + (cy - r) * scale, x + (cx + r) * scale,
                           y + (cy + r) * scale, outline=color, width=width, tags=(tag,))
    return tag


def rounded_rect(canvas, x1, y1, x2, y2, radius, **options):
    """Canvas 上的圆角矩形（平滑多边形）；用于导航选中态、标签与进度条。"""
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = (x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
              x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
              x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1)
    return canvas.create_polygon(points, smooth=True, splinesteps=12, **options)


class Pill(tk.Label):
    """状态标签：软底色 + 同色系文字，只写事实（“已核 4/4”“待确认”）。"""

    def __init__(self, parent, text="", tone="muted"):
        super().__init__(parent, text=text, padx=8, pady=1, bd=0,
                         font=(FAMILY["body"], 8))
        self.set(text, tone)

    def set(self, text, tone="muted"):
        self.configure(text=text, bg=soft_for(tone), fg=color_for(tone) if tone != "muted" else MUTED)


class Meter(tk.Frame):
    """一行“名称 · 用量”加细进度条；用于 Agent 预算。"""

    def __init__(self, parent, title):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg)
        top = tk.Frame(self, bg=bg)
        top.pack(fill="x")
        tk.Label(top, text=title, bg=bg, fg=MUTED, font=(FAMILY["body"], 8)).pack(side="left")
        self.value = tk.Label(top, text="—", bg=bg, fg=MUTED, font=(FAMILY["mono"], 8))
        self.value.pack(side="right")
        self.bar = tk.Canvas(self, height=6, bg=bg, highlightthickness=0, bd=0)
        self.bar.pack(fill="x", pady=(4, 0))
        self._ratio = 0.0
        self.bar.bind("<Configure>", lambda _event: self._paint())

    def set_usage(self, used, limit, text=None):
        try:
            ratio = 0.0 if not limit else max(0.0, min(1.0, float(used) / float(limit)))
        except (TypeError, ValueError):
            ratio = 0.0
        self._ratio = ratio
        self.value.configure(text=text if text is not None else f"{used} / {limit}")
        self._paint()

    def _paint(self):
        canvas = self.bar
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        rounded_rect(canvas, 0, 0, width, 6, 3, fill=PAPER_DEEP, outline="")
        if self._ratio > 0:
            tone = BAD if self._ratio >= 0.9 else ACCENT
            rounded_rect(canvas, 0, 0, max(6, width * self._ratio), 6, 3, fill=tone, outline="")
