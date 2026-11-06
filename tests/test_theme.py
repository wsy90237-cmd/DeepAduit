"""设计令牌的可访问性与组件冒烟测试：对比度、圆角卡片、动效降级。"""

import os
import tkinter as tk
import unittest
from unittest.mock import patch

from tb_autofill import theme


def luminance(color):
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
              for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground, background):
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class PaletteTests(unittest.TestCase):
    def test_body_and_muted_text_meet_contrast_on_both_surfaces(self):
        for background in (theme.PAPER, theme.SURFACE, theme.SURFACE_ALT):
            with self.subTest(background=background):
                self.assertGreaterEqual(contrast(theme.INK, background), 4.5)
                self.assertGreaterEqual(contrast(theme.MUTED, background), 4.5)

    def test_primary_button_text_and_link_color_meet_contrast(self):
        self.assertGreaterEqual(contrast("#FFFFFF", theme.ACCENT), 4.5)
        self.assertGreaterEqual(contrast(theme.ACCENT, theme.PAPER), 4.5)
        self.assertLess(contrast(theme.ACCENT, theme.ACCENT_DARK), 1.0 + 0.35)


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        theme.install(self.root)

    def tearDown(self):
        self.root.destroy()

    def test_card_wraps_its_content_with_padding_and_a_border(self):
        holder = tk.Frame(self.root, bg=theme.PAPER)
        holder.pack()
        card = theme.RoundCard(holder, pad=16)
        card.pack(fill="x")
        tk.Label(card.body, text="合成内容", bg=theme.SURFACE).pack(anchor="w")
        for _ in range(3):
            self.root.update_idletasks()
        self.assertEqual(card.winfo_reqheight(), card.body.winfo_reqheight() + 34)
        self.assertEqual(card.cget("highlightthickness"), 1)

    def test_stat_tile_updates_value_and_hint(self):
        tile = theme.StatTile(self.root, "待确认", "—", "读取后更新")
        tile.update_value(3, "需人工确认", tone=theme.WARN)
        self.assertEqual(tile.value.cget("text"), "3")
        self.assertEqual(tile.value.cget("fg"), theme.WARN)
        self.assertEqual(tile.hint.cget("text"), "需人工确认")


class MotionTests(unittest.TestCase):
    def test_reduced_motion_environment_disables_animation(self):
        with patch.dict(os.environ, {"DEEPAUDIT_REDUCED_MOTION": "1"}), \
                patch.object(theme, "_REDUCED", None):
            self.assertFalse(theme.motion_enabled())

    def test_fade_lands_on_final_color_without_animation(self):
        root = tk.Tk()
        root.withdraw()
        frame = tk.Frame(root, bg=theme.PAPER)
        with patch.dict(os.environ, {"DEEPAUDIT_REDUCED_MOTION": "1"}), \
                patch.object(theme, "_REDUCED", None):
            theme.fade(frame, end=theme.SURFACE)
        self.assertEqual(frame.cget("bg"), theme.SURFACE)
        root.destroy()


if __name__ == "__main__":
    unittest.main()
