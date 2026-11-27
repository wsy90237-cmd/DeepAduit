"""主题化消息框：返回值与原生一致，视觉走纸面令牌，且不再调用系统消息框。"""

import tkinter as tk
import unittest
from unittest.mock import patch

from tb_autofill import dialogs, theme


def make_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:            # pragma: no cover - 取决于运行环境
        raise unittest.SkipTest(f"无可用显示：{exc}")
    root.withdraw()
    theme.install(root)
    return root


class DialogTests(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(self.root.destroy)
        self.seen = {}

    def respond(self, answer):
        """注入应答：免去人工点击，并在窗口销毁前取走要断言的事实。"""
        def responder(box):
            order = list(box.buttons["yes"].master.pack_slaves()) if "yes" in box.buttons else []
            self.seen.update({
                "background": box.cget("bg"),
                "card": box.text.cget("bg"),
                "badge": box.badge.cget("bg"),
                "buttons": list(box.buttons),
                "primary_first": bool(order) and order[0] is box.buttons.get("yes"),
                "wraplength": int(box.text.cget("wraplength")),
                "text_width": box.text.winfo_reqwidth(),
            })
            return answer
        return responder

    def test_showinfo_returns_ok_and_paints_on_one_white_sheet(self):
        with patch.object(dialogs, "_RESPONDER", self.respond("ok")), \
                patch("tkinter.messagebox.showinfo") as native:
            result = dialogs.showinfo("提示", "合成正文", parent=self.root)

        self.assertEqual(result, "ok")
        native.assert_not_called()
        self.assertEqual(self.seen["background"], theme.SURFACE)
        self.assertEqual(self.seen["card"], theme.SURFACE)
        self.assertEqual(self.seen["badge"], theme.SURFACE)

    def test_askyesno_returns_true_and_false(self):
        for answer, expected in (("yes", True), ("no", False)):
            with self.subTest(answer=answer):
                with patch.object(dialogs, "_RESPONDER", self.respond(answer)), \
                        patch("tkinter.messagebox.askyesno") as native:
                    self.assertIs(dialogs.askyesno("确认", "是否继续？", parent=self.root), expected)
                native.assert_not_called()

    def test_askokcancel_returns_bool(self):
        with patch.object(dialogs, "_RESPONDER", self.respond("cancel")):
            self.assertIs(dialogs.askokcancel("确认", "合成正文", parent=self.root), False)
        with patch.object(dialogs, "_RESPONDER", self.respond("ok")):
            self.assertIs(dialogs.askokcancel("确认", "合成正文", parent=self.root), True)

    def test_askyesnocancel_returns_none_on_cancel(self):
        with patch.object(dialogs, "_RESPONDER", self.respond("cancel")):
            self.assertIsNone(dialogs.askyesnocancel("三选", "合成正文", parent=self.root))

    def test_enter_follows_the_focused_button(self):
        """焦点在「否」时按回车不能执行「是」。"""
        box = dialogs._MessageBox(
            self.root, "确认", "合成正文", kind="warning",
            buttons=(("yes", "是", "primary"), ("no", "否", "secondary")),
            results={"yes": True, "no": False}, default="yes", cancel="no")
        with patch.object(box, "focus_get", return_value=box.buttons["no"]):
            box._on_return()
        self.assertIs(box.result, False)

        box = dialogs._MessageBox(
            self.root, "确认", "合成正文", kind="warning",
            buttons=(("yes", "是", "primary"), ("no", "否", "secondary")),
            results={"yes": True, "no": False}, default="yes", cancel="no")
        with patch.object(box, "focus_get", return_value=None):
            box._on_return()
        self.assertIs(box.result, True)

    def test_primary_button_is_packed_first_so_it_sits_rightmost(self):
        with patch.object(dialogs, "_RESPONDER", self.respond("yes")):
            dialogs.askyesno("确认", "合成正文", parent=self.root)
        # side="right" 时先 pack 的在最右，主操作必须排第一。
        self.assertTrue(self.seen["primary_first"])

    def test_long_message_is_wrapped_instead_of_widening_the_window(self):
        long_text = "合成说明" * 40
        with patch.object(dialogs, "_RESPONDER", self.respond("ok")):
            dialogs.showwarning("注意", long_text, parent=self.root)
        self.assertGreater(self.seen["wraplength"], 0)
        self.assertLessEqual(self.seen["text_width"], self.seen["wraplength"] + theme.px(self.root, 40))


if __name__ == "__main__":
    unittest.main()
