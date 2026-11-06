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
from tb_autofill import auditlog, gui, knowledge, settings, workspace
from tb_autofill.models import LedgerRow, Subject


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", choices=workspace.PAGES, default="overview")
    parser.add_argument("--shot", help="截取当前页面到指定 PNG 后退出，用于设计核对")
    args = parser.parse_args()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    store_class, log_class, library_class = settings.SettingsStore, auditlog.AuditLog, knowledge.LocalLibrary
    with tempfile.TemporaryDirectory(prefix="deepaudit_preview_") as folder:
        root = Path(folder)
        with patch.object(gui.App, "_autodetect", lambda self: None), \
                patch.object(settings, "SettingsStore", lambda: store_class(root / "settings.json")), \
                patch.object(auditlog, "AuditLog", lambda: log_class(root / "synthetic.jsonl")), \
                patch.object(knowledge, "LocalLibrary", lambda: library_class(root / "synthetic-knowledge.sqlite3")):
            app = gui.App()
        app.install_var.set("合成演示 · 不连接真实账套")
        app.project_var.set("DeepAudit / 合成审计项目")
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
        app.after(600, lambda: workspace.navigate(app, args.page))
        if args.shot:
            app.after(1500, lambda: _capture(app, Path(args.shot)))
        app.mainloop()


def _capture(app, target):
    """在进程内截图：与 Tk 使用同一套坐标，避免跨进程 DPI 与遮挡问题。"""
    from PIL import ImageGrab
    app.lift()
    app.focus_force()
    for _ in range(3):
        app.update_idletasks()
        app.update()
    time.sleep(0.5)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    width, height = app.winfo_width(), app.winfo_height()
    target.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=(x, y, x + width, y + height)).save(target)
    app.destroy()


if __name__ == "__main__":
    main()
