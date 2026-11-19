"""布局自检与自修正：让控件自己发现尺寸问题并修正。

`theme.fit_window()` 只在构造时量一次；字体回退、DPI 变化，或「高级设置」这类
延迟展开的内容，都会让窗口停在折叠态尺寸，底部按钮被裁掉。这里把「测量—修正」
做成可复用的能力：

- `diagnose()` 给出结构化问题清单（kind / widget / detail），可测、可打印；
- `autofit()` 按内容实际需求量重算几何，工作区是硬上界；
- `LayoutGuard` 常驻 `<Configure>`，去抖后最多自修 3 轮；到上限就改滚动，
  不静默截断；
- `scroll_host()` 提供弹窗内的滚动区，滚轮只在指针进入时接管。

不变量：auto 修正必须是幂等的——同样的内容量两次，得到同样的几何。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme

MIN_WIDTH, MIN_HEIGHT = 320, 240
MAX_ROUNDS = 3          # 单轮自修正的上限，防止「改尺寸 → 又触发测量」死循环
MARGIN = 12             # 设计像素，窗口与工作区边缘的留白
DEBOUNCE_MS = 120       # <Configure> 去抖窗口
UNDERFLOW_ROWS = 3      # 折叠后允许残留的空白行数


def _alive(widget):
    try:
        return bool(widget.winfo_exists())
    except tk.TclError:
        return False


def _mapped(widget):
    try:
        return bool(widget.winfo_ismapped())
    except tk.TclError:
        return False


def _label_of(widget):
    """给问题清单一个能看懂的名字：优先取控件文字，其次取类名。"""
    text = ""
    try:
        text = str(widget.cget("text") or "").strip()
    except (tk.TclError, AttributeError):
        text = ""
    text = " ".join(text.split())
    if len(text) > 14:
        text = text[:14] + "…"
    return text or widget.winfo_class()


def _box(widget, origin_x, origin_y):
    """控件在窗口客户区内的相对矩形 (x, y, 宽, 高)。"""
    try:
        x = widget.winfo_rootx() - origin_x
        y = widget.winfo_rooty() - origin_y
        return x, y, widget.winfo_width(), widget.winfo_height()
    except tk.TclError:
        return 0, 0, 0, 0


def _overflow(widget, box, width, height, tolerance):
    """单个控件的越界类型；没有越界返回 None。"""
    x, y, widget_w, widget_h = box
    if widget_h <= 0 or widget_w <= 0:
        return None
    over_bottom = (y + widget_h) - height
    over_right = (x + widget_w) - width
    if over_bottom > tolerance:
        return "clipped_bottom", f"「{_label_of(widget)}」底部超出窗口 {over_bottom}px，内容被裁切"
    if over_right > tolerance:
        return "clipped_right", f"「{_label_of(widget)}」右侧超出窗口 {over_right}px，内容被裁切"
    return None


def _wrap_too_wide(widget, box, width, tolerance):
    """tk.Label 的换行宽度不能大于它可用的横向空间。"""
    if not isinstance(widget, tk.Label):
        return None
    try:
        wraplength = int(widget.cget("wraplength"))
    except (tk.TclError, ValueError):
        return None
    if wraplength <= 0:
        return None
    x, _y, _w, _h = box
    available = width - max(0, x) - tolerance
    if available > 0 and wraplength > available:
        return ("wrap_too_wide",
                f"「{_label_of(widget)}」换行宽度 {wraplength}px 大于可用 {available}px，文字会被顶出")
    return None


def diagnose(window, *, fit_content: bool = False):
    """遍历控件树，返回结构化问题列表。

    每项含 `kind`、`widget`、`detail`；父控件已越界时不再重复报它的子控件，
    避免同一处裁切刷出一屏。`fit_content=True` 时才检查「折叠后残留空白」，
    因为只有按内容收紧的窗口才应该对多余高度敏感。
    """
    problems = []
    if not _alive(window):
        return problems
    try:
        window.update_idletasks()
        width, height = window.winfo_width(), window.winfo_height()
        origin_x, origin_y = window.winfo_rootx(), window.winfo_rooty()
    except tk.TclError:
        return problems
    if width <= 1 or height <= 1:
        return problems                      # 尚未映射，测不出结论就不要误报

    tolerance = theme.px(window, 2)
    queue = [window]
    hosts = []
    while queue:
        parent = queue.pop(0)
        try:
            children = parent.winfo_children()
        except tk.TclError:
            continue
        for child in children:
            if not _mapped(child):
                continue
            box = _box(child, origin_x, origin_y)
            found = _overflow(child, box, width, height, tolerance)
            if found:
                kind, detail = found
                problems.append({"kind": kind, "widget": child, "detail": detail})
                continue                     # 已定位到外层，子控件不再细报
            found = _wrap_too_wide(child, box, width, tolerance)
            if found:
                kind, detail = found
                problems.append({"kind": kind, "widget": child, "detail": detail})
            if isinstance(child, ScrollHost):
                hosts.append(child)          # 滚动区内部超出可视范围是预期行为，不下钻
                continue
            queue.append(child)

    try:
        need_w, need_h = window.winfo_reqwidth(), window.winfo_reqheight()
    except tk.TclError:
        return problems
    scrollable = [host for host in hosts if host.scrollable()]
    if need_h - height > tolerance:
        if scrollable:
            # 内容比窗口高，但滚动区已承接：这是「可用」状态，不是裁切。
            problems.append({
                "kind": "scrollable", "widget": scrollable[0],
                "detail": f"内容需要 {need_h}px 高，可视 {height}px，已由滚动区承接",
            })
        else:
            problems.append({
                "kind": "content_overflow", "widget": window,
                "detail": f"内容需要 {need_h}px 高，当前只有 {height}px，纵向被压缩",
            })
    elif fit_content and height - need_h > theme.px(window, UNDERFLOW_ROWS * theme.ROW_HEIGHT):
        problems.append({
            "kind": "content_underflow", "widget": window,
            "detail": f"内容只有 {need_h}px 高，窗口却占 {height}px，下方残留空白",
        })
    if need_w - width > tolerance:
        problems.append({
            "kind": "content_overflow", "widget": window,
            "detail": f"内容需要 {need_w}px 宽，当前只有 {width}px，横向被压缩",
        })
    return problems


def _limits(window, max_width=None):
    area_w, area_h = theme.work_area(window)
    margin = theme.px(window, MARGIN)
    limit_w = max(MIN_WIDTH, area_w - margin * 2)
    limit_h = max(MIN_HEIGHT, area_h - margin * 2)
    if max_width is not None:
        limit_w = min(limit_w, max(MIN_WIDTH, theme.px(window, max_width)))
    return limit_w, limit_h, area_w, area_h


def autofit(window, *, min_width=0, min_height=0, max_width=None):
    """按内容实际需求量重算窗口几何，返回最终 (宽, 高)；无法测量时返回 None。

    以工作区为上界，**钉住窗口左上角**：内容变多就向下长，标题栏不跳；只有越出
    工作区才整体回收。位置基准必须用 winfo_x / winfo_y（外框坐标），不能用
    winfo_rootx / winfo_rooty（客户区坐标，差一个边框与标题栏）。`minsize` 同步
    收紧到最终尺寸，避免用户把窗口拖到内容装不下的程度。
    """
    if not _alive(window):
        return None
    try:
        window.update_idletasks()
    except tk.TclError:
        return None
    limit_w, limit_h, area_w, area_h = _limits(window, max_width)
    try:
        need_w, need_h = window.winfo_reqwidth(), window.winfo_reqheight()
        current_w, current_h = window.winfo_width(), window.winfo_height()
        mapped = bool(window.winfo_ismapped())
        frame_x, frame_y = window.winfo_x(), window.winfo_y()
    except tk.TclError:
        return None

    final_w = min(limit_w, max(MIN_WIDTH, theme.px(window, min_width), need_w))
    final_h = min(limit_h, max(MIN_HEIGHT, theme.px(window, min_height), need_h))
    # 位置策略：geometry 的 +X+Y 是**外框**原点，因此用 winfo_x/y（同为外框坐标），
    # 不能用 winfo_rootx/rooty（客户区坐标，差一个标题栏与边框），否则每修正一次都会漂移。
    # 默认钉住左上角：内容增多时向下长，标题栏不跳；只有越出工作区才整体回收。
    base = (frame_x, frame_y, current_w, current_h) if mapped and current_w > 1 else theme.last_placement(window)
    remembered = theme.last_placement(window)
    if base and base[2] > 1 and base[3] > 1:
        if (base[0], base[1]) == (0, 0) and (remembered is None or remembered[:2] != (0, 0)):
            # Toplevel 映射瞬间，WM 还没落位，winfo_x/y 会先报 0,0；「钉住左上角」
            # 会把这个默认角落当成用户的选择。没记录过位置就居中一次；记录过就沿用。
            base = ((area_w - final_w) // 2, (area_h - final_h) // 2, base[2], base[3]) \
                if remembered is None else (remembered[0], remembered[1], base[2], base[3])
        left, top = base[0], base[1]
        left = min(max(0, left), max(0, area_w - final_w))
        top = min(max(0, top), max(0, area_h - final_h))
        if top < base[1] or left < base[0]:
            # 只在被顶到边界时才退回「以中心收拢」，避免整块窗口突然贴边。
            left = min(max(0, base[0] + base[2] // 2 - final_w // 2), max(0, area_w - final_w))
            top = min(max(0, base[1] + base[3] // 2 - final_h // 2), max(0, area_h - final_h))
    else:
        left = max(0, (area_w - final_w) // 2)
        top = max(0, (area_h - final_h) // 2)
    try:
        window.minsize(min(theme.px(window, min_width), final_w),
                       min(theme.px(window, min_height), final_h))
        window.geometry(f"{final_w}x{final_h}+{left}+{top}")
        theme.remember_placement(window, left, top, final_w, final_h)
        window.update_idletasks()
    except tk.TclError:
        return None
    return final_w, final_h


class ScrollHost(tk.Frame):
    """可滚动内容容器：Canvas + 内嵌 Frame。

    请求尺寸等于内容尺寸，外层窗口因此能按内容长高；一旦窗口被工作区截住，
    pack 会把 Canvas 压小，滚动条接管剩余内容。滚轮只在指针位于容器内时生效，
    不抢主窗口的滚动。
    """

    def __init__(self, parent, *, bg):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, takefocus=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scroll.grid(row=0, column=1, sticky="ns")
        self.scroll.grid_remove()
        self.canvas.configure(yscrollcommand=self._sync)
        self.body = tk.Frame(self.canvas, bg=bg)
        self.body.columnconfigure(0, weight=1)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self._size = (0, 0)
        self._wheel_bound = set()
        self.body.bind("<Configure>", self._on_body, add="+")
        self.canvas.bind("<Configure>", self._on_canvas, add="+")

    # ---- 测量 ----

    def content_size(self):
        try:
            self.update_idletasks()
            return self.body.winfo_reqwidth(), self.body.winfo_reqheight()
        except tk.TclError:
            return 0, 0

    def refresh(self):
        """把内容需求量变成 Canvas 的请求尺寸，并同步滚动区。"""
        if not _alive(self):
            return
        need_w, need_h = self.content_size()
        need_w, need_h = max(1, need_w), max(1, need_h)
        try:
            if (need_w, need_h) != self._size:
                self._size = (need_w, need_h)
                self.canvas.configure(width=need_w, height=need_h)
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            self._fit_width()
        except tk.TclError:
            return
        self._rebind_wheel()
        self._sync()

    def scrollable(self):
        try:
            return self.body.winfo_reqheight() > self.canvas.winfo_height() + 1
        except tk.TclError:
            return False

    def reveal(self, widget=None):
        """把焦点控件（或内容末尾）滚进可视区，返回是否真的滚动了。"""
        self.refresh()
        try:
            total, view = self.body.winfo_height(), self.canvas.winfo_height()
        except tk.TclError:
            return False
        if total <= view or view <= 1:
            return False
        target = widget if widget is not None and _alive(widget) else self._focus_target()
        start = 0.0
        if target is not None:
            try:
                offset = target.winfo_rooty() - self.body.winfo_rooty()
                end = offset + target.winfo_height()
            except tk.TclError:
                offset, end = 0, 0
            if offset > 0:
                start = max(0.0, (offset - theme.px(self, 8)) / total)
            if end > view and start == 0.0:
                start = min(1.0, (end - view) / total)
        else:
            start = 1.0
        try:
            self.canvas.yview_moveto(min(1.0, max(0.0, start)))
        except tk.TclError:
            return False
        return True

    def _focus_target(self):
        try:
            focused = self.winfo_toplevel().focus_get()
        except tk.TclError:
            return None
        if focused is None:
            return None
        parent = focused
        while parent is not None and parent is not self.body:
            if parent is self:
                return focused
            try:
                parent = parent.master
            except AttributeError:
                return None
        return focused if parent is self.body else None

    # ---- 事件 ----

    def _on_body(self, _event=None):
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except tk.TclError:
            return
        self._fit_width()
        self._rebind_wheel()
        self._sync()

    def _on_canvas(self, event=None):
        self._fit_width(event.width if event is not None else None)

    def _fit_width(self, width=None):
        try:
            width = width or self.canvas.winfo_width()
            if width > 1:
                self.canvas.itemconfigure(self._window, width=width)
        except tk.TclError:
            return

    def _sync(self, first=None, last=None):
        """yscrollcommand 回调：只有真的放不下时才显示滚动条。"""
        try:
            if first is None:
                first, last = self.canvas.yview()
            else:
                self.scroll.set(first, last)
            hidden = float(first) <= 0.0 and float(last) >= 1.0
            if hidden:
                self.scroll.grid_remove()
            else:
                self.scroll.grid()
        except (tk.TclError, ValueError):
            return

    def _rebind_wheel(self):
        """把滚轮绑到滚动区内的每个控件上。

        早期版本用 `bind_all` 接管，离开时 `unbind_all` 会把主页面滚动区
        （workspace 也绑在 all 上）一并清掉——滚过弹窗再关窗，主页面就滚不动了。
        改成逐控件绑定后，解绑也只能解到自己，绝不会波及窗口外。
        """
        targets = [self.canvas, self.body]
        stack = [self.body]
        while stack:
            widget = stack.pop()
            try:
                stack.extend(widget.winfo_children())
            except tk.TclError:
                continue
            targets.append(widget)
        for widget in targets:
            if widget in self._wheel_bound:
                continue
            try:
                widget.bind("<MouseWheel>", self._wheel, add="+")
            except tk.TclError:
                continue
            self._wheel_bound.add(widget)

    def _inside(self, event):
        """指针是否真的落在本滚动区内；不在就放行给主页面。"""
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except tk.TclError:
            return False
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _wheel(self, event):
        if not self._inside(event) or not self.scrollable():
            return None
        step = -1 * int(event.delta / 120)
        if step == 0:
            step = -1 if event.delta > 0 else 1
        try:
            self.canvas.yview_scroll(step, "units")
        except tk.TclError:
            return None
        return "break"


def scroll_host(parent, *, bg):
    """建一个滚动内容容器，返回带 `.body` 的宿主。"""
    return ScrollHost(parent, bg=bg)


class LayoutGuard:
    """常驻守护器：`<Configure>` 去抖后自检，最多自修 3 轮。

    修正优先级：① 工作区还有余量就长高/加宽；② 已到上限且给了滚动宿主，
    保证滚动可用并滚到焦点控件；③ 否则通过 `on_report` 给出可见告警。
    自动修正的轮次是累计的，只有显式 `refit()`（用户改变内容）才重置预算，
    这样重复的 `<Configure>` 不会变成无限重排。
    """

    def __init__(self, window, *, host=None, on_report=None, fit_content=True,
                 min_width=0, min_height=0, max_width=None, rounds=MAX_ROUNDS):
        self.window, self.host, self.on_report = window, host, on_report
        self.fit_content = fit_content
        self.options = {"min_width": min_width, "min_height": min_height, "max_width": max_width}
        self.max_rounds = max(1, int(rounds))
        self.rounds = 0
        self.fixed = False
        self.problems = []
        self.message = ""
        self._job = None
        self._inside = False
        try:
            self._binding = window.bind("<Configure>", self._on_configure, add="+")
        except tk.TclError:
            self._binding = None

    # ---- 对外 ----

    def refit(self, delay=0):
        """内容变了（如展开高级设置）时调用：重置轮次预算并重新自检。

        只有这条路径会为了「消掉多余空白」而缩小窗口；用户手动拉大窗口不会被弹回去。
        """
        self.rounds = 0
        self.fixed = False
        self.message = ""
        if delay:
            self._schedule(delay)
        else:
            return self.check_now(shrink=True)
        return None

    def check_now(self, *, shrink=False):
        """同步自检并修正，返回结论字典；供测试与显式调用。"""
        if not _alive(self.window):
            return self.report()
        if self.rounds >= self.max_rounds:
            # 预算用尽：只测量、不再改尺寸，避免抖动。
            self.problems = self._measure()
            if self._actionable(shrink) and not self.message:
                self._warn(f"窗口已连续修正 {self.max_rounds} 轮仍有内容放不下，"
                           "请手动调整窗口或显示缩放。")
            return self.report()

        self._inside = True
        try:
            self.problems = self._measure()
            if not self._actionable(shrink):
                self.fixed = True
                return self.report()
            while self.rounds < self.max_rounds:
                self.rounds += 1
                if self._grow(shrink):
                    self.problems = self._measure()
                    if not self._actionable(shrink):
                        self.fixed = True
                        break
                    continue
                if self._only_padding():
                    # 只是折叠后留下的空白，已被 minsize 兜住，不必再告警。
                    self.fixed = True
                    break
                if self._scroll():
                    self.fixed = True
                    break
                self._warn("窗口已到屏幕可用区域上限，内容仍然放不下："
                           "请调整显示缩放，或用鼠标把窗口边框拉大后重试。")
                break
            if not self.fixed and not self.message and self._actionable(shrink):
                # 轮次用尽但内容仍不合身：照样给结论，不留静默截断。
                self._warn(f"窗口已连续修正 {self.max_rounds} 轮仍有内容放不下，"
                           "请手动调整窗口或显示缩放。")
        finally:
            self._inside = False
        return self.report()

    def report(self):
        size = None
        try:
            size = (self.window.winfo_width(), self.window.winfo_height())
        except tk.TclError:
            pass
        return {"rounds": self.rounds, "fixed": self.fixed, "message": self.message,
                "problems": [{"kind": item["kind"], "detail": item["detail"],
                              "widget": item["widget"]} for item in self.problems],
                "size": size}

    def stop(self):
        """解绑事件，窗口销毁前调用可避免残留 after 回调。"""
        try:
            if self._job is not None:
                self.window.after_cancel(self._job)
        except tk.TclError:
            pass
        self._job = None
        try:
            if self._binding:
                self.window.unbind("<Configure>", self._binding)
        except tk.TclError:
            pass
        self._binding = None

    # ---- 内部 ----

    def _measure(self):
        if self.host is not None:
            self.host.refresh()
        # 窗口刚改过尺寸时，子控件的摆放要等下一个 Configure 才生效；
        # 只跑 update_idletasks 会量到旧坐标，误判成「仍被裁切」。
        try:
            if self.window.winfo_viewable():
                self.window.update()
        except tk.TclError:
            pass
        return diagnose(self.window, fit_content=self.fit_content)

    def _only_padding(self):
        """只剩「折叠后空白」这类不影响可操作性的问题。"""
        return bool(self.problems) and all(item["kind"] == "content_underflow"
                                          for item in self.problems)

    def _actionable(self, shrink):
        """需要动手的问题；空白与「已由滚动承接」都不算。

        `scrollable` 是信息项：内容比窗口高但滚动区已经接管，用户能滚到，
        不是裁切，不该再触发长高或告警。
        """
        if shrink:
            return [item for item in self.problems if item["kind"] != "scrollable"]
        return [item for item in self.problems
                if item["kind"] not in ("content_underflow", "scrollable")]

    def _on_configure(self, event=None):
        if self._inside:
            return
        if event is not None and event.widget is not self.window:
            return
        if self.rounds >= self.max_rounds:
            return
        self._schedule()

    def _schedule(self, delay=DEBOUNCE_MS):
        try:
            if self._job is not None:
                self.window.after_cancel(self._job)
            self._job = self.window.after(delay, self._run)
        except tk.TclError:
            self._job = None

    def _run(self):
        self._job = None
        if self.rounds >= self.max_rounds:
            return
        self.check_now()

    def _grow(self, shrink=False):
        """按内容长高/加宽；尺寸没变化说明已经到上限。"""
        if not shrink and self._only_padding():
            return False                   # 只是空白，不做没必要的缩小
        try:
            before = (self.window.winfo_width(), self.window.winfo_height())
            if self.host is not None:
                self.host.refresh()
            size = autofit(self.window, **self.options)
        except tk.TclError:
            return False
        return bool(size) and size != before

    def _scroll(self):
        if self.host is None:
            return False
        try:
            self.host.refresh()
            if not self.host.scrollable():
                return False
            self.host.reveal()
        except tk.TclError:
            return False
        return True

    def _warn(self, text):
        self.message = text
        if self.on_report is None:
            return
        try:
            self.on_report(text)
        except tk.TclError:
            pass
