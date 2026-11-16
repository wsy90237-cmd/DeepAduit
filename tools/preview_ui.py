"""Development preview with fictional data and isolated settings/logs."""

import argparse
import ctypes
import time
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tb_autofill import APP_NAME, auditlog, gui, knowledge, settings, workspace
from tb_autofill.models import LedgerRow, Subject


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", choices=(*workspace.PAGES, "settings"), default="overview",
                        help="页面键；settings 为 security 的易记别名")
    parser.add_argument("--shot", help="截取当前页面到指定 PNG 后退出，用于设计核对")
    args = parser.parse_args()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    store_class, log_class, library_class = settings.SettingsStore, auditlog.AuditLog, knowledge.LocalLibrary
    with tempfile.TemporaryDirectory(prefix="attesta_preview_") as folder:
        root = Path(folder)
        with patch.object(gui.App, "_autodetect", lambda self: None), \
                patch.object(settings, "SettingsStore", lambda: store_class(root / "settings.json")), \
                patch.object(auditlog, "AuditLog", lambda: log_class(root / "synthetic.jsonl")), \
                patch.object(knowledge, "LocalLibrary", lambda: library_class(root / "synthetic-knowledge.sqlite3")):
            app = gui.App()
        app.install_var.set("合成演示 · 不连接真实账套")
        app.project_var.set(f"{APP_NAME} / 合成审计项目")
        subject = Subject("SYNTHETIC", "合成示例企业（虚构）", 2026, 6, "synthetic-only")
        app.current_subject = subject
        app.subject_var.set(subject.label)
        app.year_var.set("2026")
        app.month_var.set("6")
        app.unit_var.set("元")
        rows = [LedgerRow(code, name, opening=Decimal(opening), ending=Decimal(ending),
                          subject_id=subject.id, year=subject.year, month=subject.month, direction=direction)
                for code, name, opening, ending, direction in (
                    ("1001", "库存现金", "5000", "12000", "借"),
                    ("1002", "银行存款", "800000", "1230000", "借"),
                    ("1122", "应收账款", "300000", "260000", "借"),
                    ("1221", "其他应收款", "20000", "15000", "借"),
                    ("1601", "固定资产", "500000", "500000", "借"),
                    ("2202", "应付账款", "120000", "150000", "贷"),
                    ("4001", "实收资本", "1000000", "1000000", "贷"),
                    ("4104", "未分配利润", "500000", "723000", "贷"))]
        app._on_ledger(rows, subject)
        app.template_var.set(str(ROOT / "tests" / "fixtures" / "试算平衡表-合成.xlsx"))
        app._recognize()
        reference = root / "合成审计方法示例.md"
        reference.write_text("合成示例，不是有效准则：复核库存现金时核对期初期末、科目方向、金额单位及支持证据。", encoding="utf-8")
        app.library.import_file(reference)
        page = "security" if args.page == "settings" else args.page
        app.after(600, lambda: workspace.navigate(app, page))
        if args.shot:
            app.after(1500, lambda: _capture(app, Path(args.shot)))
        app.mainloop()


def _capture(app, target):
    """截图优先用 PrintWindow，窗口被其他程序遮挡时也能拿到完整界面。"""
    from PIL import Image, ImageGrab
    for _ in range(3):
        app.update_idletasks()
        app.update()
    time.sleep(0.4)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from ctypes import windll
        import win32gui
        import win32ui
        hwnd = windll.user32.GetParent(app.winfo_id())
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width, height = right - left, bottom - top
        source = win32ui.CreateDCFromHandle(win32gui.GetWindowDC(hwnd))
        memory = source.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source, width, height)
        memory.SelectObject(bitmap)
        windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2)
        info = bitmap.GetInfo()
        Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                         bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1).save(target)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        source.DeleteDC()
    except Exception:
        x, y = app.winfo_rootx(), app.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height())).save(target)
    app.destroy()


if __name__ == "__main__":
    main()
