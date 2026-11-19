"""布局自检与自修正：诊断能定位裁切，修正能收敛，轮次有上限。"""

import tkinter as tk
import unittest
from unittest.mock import patch

from tb_autofill import layout, theme


def make_root():
    """无显示器环境直接跳过：Tk 建不出来就没有可测的布局。"""
    try:
        root = tk.Tk()
    except tk.TclError as exc:            # pragma: no cover - 取决于运行环境
        raise unittest.SkipTest(f"无可用显示：{exc}")
    root.withdraw()
    theme.install(root)
    return root


def make_window(root, width, height):
    """离屏的独立窗口：不抢焦点、不闪，但照常参与几何计算。"""
    window = tk.Toplevel(root)
    window.overrideredirect(True)
    window.attributes("-alpha", 0.0)
    window.geometry(f"{width}x{height}+-3000+-3000")
    return window


class DiagnoseTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(self.root.destroy)

    def test_grid_content_below_the_window_is_reported_as_clipped_bottom(self):
        # 400px 内容放进 200px 窗口：grid 不会压缩行高，第二块被裁掉。
        window = make_window(self.root, 400, 200)
        self.addCleanup(window.destroy)
        upper = tk.Frame(window, height=200, width=380, bg=theme.SURFACE)
        upper.grid(row=0, column=0)
        lower = tk.Frame(window, height=200, width=380, bg=theme.PAPER_DEEP)
        lower.grid(row=1, column=0)
        window.update()

        kinds = {item["kind"] for item in layout.diagnose(window)}
        self.assertIn("clipped_bottom", kinds)
        self.assertIn("content_overflow", kinds)
        details = [item["detail"] for item in layout.diagnose(window)]
        self.assertTrue(all(detail.strip() for detail in details), "问题描述不能为空")

    def test_pack_content_is_reported_as_content_overflow(self):
        window = make_window(self.root, 400, 200)
        self.addCleanup(window.destroy)
        tk.Frame(window, height=400, width=380, bg=theme.SURFACE).pack()
        window.update()
        kinds = {item["kind"] for item in layout.diagnose(window)}
        self.assertIn("content_overflow", kinds)

    def test_healthy_window_reports_nothing(self):
        window = make_window(self.root, 420, 260)
        self.addCleanup(window.destroy)
        tk.Label(window, text="合成内容", bg=theme.PAPER).pack(padx=16, pady=16)
        window.update()
        self.assertEqual(layout.diagnose(window), [])

    def test_underflow_only_when_the_window_opted_into_content_fitting(self):
        window = make_window(self.root, 400, 600)
        self.addCleanup(window.destroy)
        tk.Frame(window, height=120, width=380, bg=theme.SURFACE).pack()
        window.update()
        self.assertEqual(layout.diagnose(window), [])
        kinds = {item["kind"] for item in layout.diagnose(window, fit_content=True)}
        self.assertIn("content_underflow", kinds)

    def test_over_wide_wraplength_is_reported(self):
        window = make_window(self.root, 300, 200)
        self.addCleanup(window.destroy)
        tk.Label(window, text="合成说明", bg=theme.PAPER, wraplength=900).pack(anchor="w")
        window.update()
        kinds = {item["kind"] for item in layout.diagnose(window)}
        self.assertIn("wrap_too_wide", kinds)


class AutofitTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(self.root.destroy)

    def test_window_is_capped_by_a_1366_by_728_work_area(self):
        window = make_window(self.root, 400, 200)
        self.addCleanup(window.destroy)
        tk.Frame(window, height=2000, width=1400, bg=theme.SURFACE).pack()
        window.update()

        with patch.object(theme, "work_area", return_value=(1366, 728)):
            size = layout.autofit(window, min_width=360, min_height=180)

        self.assertIsNotNone(size)
        margin = theme.px(window, layout.MARGIN) * 2
        self.assertEqual(size, (1366 - margin, 728 - margin))
        self.assertEqual((window.winfo_width(), window.winfo_height()), size)

    def test_autofit_is_idempotent(self):
        window = make_window(self.root, 400, 200)
        self.addCleanup(window.destroy)
        tk.Label(window, text="合成内容", bg=theme.PAPER).pack(padx=12, pady=12)
        window.update()
        first = layout.autofit(window, min_width=320, min_height=160)
        window.update()
        second = layout.autofit(window, min_width=320, min_height=160)
        self.assertEqual(first, second)

    def test_autofit_never_shrinks_below_minsize(self):
        window = make_window(self.root, 400, 200)
        self.addCleanup(window.destroy)
        tk.Label(window, text="短", bg=theme.PAPER).pack()
        window.update()
        size = layout.autofit(window, min_width=460, min_height=300)
        self.assertGreaterEqual(size[0], theme.px(window, 460))
        self.assertGreaterEqual(size[1], theme.px(window, 300))

    def test_repeated_autofit_keeps_the_window_origin(self):
        """几何的 +X+Y 是外框坐标，winfo_rootx 是客户区坐标；用错基准会每次漂移。

        这条用例专门盯住「每点一次高级设置窗口就往下跳」的回归：尺寸可收紧，
        但位置不能动。
        """
        window = tk.Toplevel(self.root)
        self.addCleanup(window.destroy)
        window.attributes("-alpha", 0.0)
        window.geometry("400x300+200+150")
        tk.Label(window, text="合成内容", bg=theme.PAPER).pack(padx=12, pady=12)
        window.update()

        placed = []
        for _ in range(3):
            layout.autofit(window, min_width=320, min_height=160)
            window.update()
            placed.append(window.geometry())

        origins = {value.split("+", 1)[1] for value in placed}
        self.assertEqual(len(origins), 1, f"窗口位置发生漂移：{placed}")

    def test_scrollable_window_is_not_reported_as_clipped(self):
        # 内容超出可视高、但滚动区已接管：这是可用状态，不能算裁切。
        window = make_window(self.root, 420, 220)
        self.addCleanup(window.destroy)
        host = layout.scroll_host(window, bg=theme.PAPER)
        host.pack(fill="both", expand=True)
        for _ in range(20):
            tk.Label(host.body, text="合成明细行", bg=theme.PAPER).pack(anchor="w")
        window.update()
        host.refresh()

        kinds = {item["kind"] for item in layout.diagnose(window)}
        self.assertIn("scrollable", kinds)
        self.assertNotIn("clipped_bottom", kinds)
        self.assertNotIn("content_overflow", kinds)

    def test_scroll_host_does_not_steal_the_global_wheel_binding(self):
        """`unbind_all` 会把主页面的滚轮绑定一起清掉，这里守住「只绑自己」。"""
        self.root.bind_all("<MouseWheel>", lambda _event: None)   # 模拟主页面的滚动绑定
        sentinel = self.root.bind_all("<MouseWheel>")
        self.assertTrue(sentinel, "根窗口应已有滚轮绑定（由测试自己安装）")
        window = make_window(self.root, 420, 220)
        self.addCleanup(window.destroy)
        host = layout.scroll_host(window, bg=theme.PAPER)
        host.pack(fill="both", expand=True)
        tk.Label(host.body, text="合成明细行", bg=theme.PAPER).pack(anchor="w")
        window.update()
        host.refresh()

        self.assertEqual(self.root.bind_all("<MouseWheel>"), sentinel,
                         "滚动区不得改写全局滚轮绑定")


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(self.root.destroy)

    def test_check_now_grows_the_window_and_reports_fixed(self):
        window = make_window(self.root, 400, 200)
        self.addCleanup(window.destroy)
        upper = tk.Frame(window, height=200, width=380, bg=theme.SURFACE)
        upper.grid(row=0, column=0)
        lower = tk.Frame(window, height=200, width=380, bg=theme.PAPER_DEEP)
        lower.grid(row=1, column=0)
        window.update()

        guard = layout.LayoutGuard(window, min_width=360, min_height=180, fit_content=False)
        self.addCleanup(guard.stop)
        report = guard.check_now()

        self.assertTrue(report["fixed"], report["message"])
        self.assertGreaterEqual(window.winfo_height(), 400)
        self.assertEqual(report["problems"], [])
        self.assertGreaterEqual(report["rounds"], 1)

    def test_rounds_never_exceed_the_limit(self):
        window = make_window(self.root, 400, 200)
        self.addCleanup(window.destroy)
        # 内容比工作区还高：只能告警，不能无休止地重排。
        tk.Frame(window, height=900, width=380, bg=theme.SURFACE).pack()
        window.update()

        with patch.object(theme, "work_area", return_value=(800, 400)):
            guard = layout.LayoutGuard(window, min_width=360, min_height=180, fit_content=False)
            self.addCleanup(guard.stop)
            for _ in range(5):
                report = guard.check_now()
                self.assertLessEqual(report["rounds"], 3)
            self.assertLessEqual(guard.rounds, 3)
            self.assertTrue(report["message"], "放不下时必须给出可见告警")

    def test_guard_reports_through_callback_instead_of_staying_silent(self):
        window = make_window(self.root, 400, 200)
        self.addCleanup(window.destroy)
        tk.Frame(window, height=900, width=380, bg=theme.SURFACE).pack()
        window.update()
        messages = []

        with patch.object(theme, "work_area", return_value=(800, 400)):
            guard = layout.LayoutGuard(window, min_width=360, min_height=180,
                                       fit_content=False, on_report=messages.append)
            self.addCleanup(guard.stop)
            guard.check_now()

        self.assertTrue(messages)
        self.assertIn("窗口", messages[0])

    def test_manual_enlargement_is_not_undone_by_the_guard(self):
        # 用户自己把窗口拉高后，守护器只处理裁切，不再把空白「纠正」回去。
        window = make_window(self.root, 400, 200)
        self.addCleanup(window.destroy)
        tk.Frame(window, height=120, width=380, bg=theme.SURFACE).pack()
        window.update()
        window.geometry("400x600")
        window.update()

        guard = layout.LayoutGuard(window, min_width=360, min_height=180, fit_content=True)
        self.addCleanup(guard.stop)
        report = guard.check_now()

        self.assertTrue(report["fixed"])
        self.assertEqual(window.winfo_height(), 600)

    def test_scroll_host_keeps_scrolling_when_the_window_is_capped(self):
        window = make_window(self.root, 420, 200)
        self.addCleanup(window.destroy)
        host = layout.scroll_host(window, bg=theme.PAPER)
        host.pack(fill="both", expand=True)
        for _ in range(20):
            tk.Label(host.body, text="合成明细行", bg=theme.PAPER).pack(anchor="w")
        window.update()

        with patch.object(theme, "work_area", return_value=(800, 400)):
            guard = layout.LayoutGuard(window, host=host, min_width=360, min_height=180)
            self.addCleanup(guard.stop)
            report = guard.check_now()

        self.assertTrue(report["fixed"])
        self.assertTrue(host.scrollable(), "内容超过可视区时必须可以滚动")
        self.assertEqual(report["message"], "")


if __name__ == "__main__":
    unittest.main()
