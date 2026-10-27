"""Development preview with fictional data and isolated settings/logs."""

import argparse
import ctypes
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tb_autofill import auditlog, gui, settings, workspace
from tb_autofill.models import LedgerRow, Subject


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", choices=workspace.PAGES, default="overview")
    args = parser.parse_args()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    store_class, log_class = settings.SettingsStore, auditlog.AuditLog
    with tempfile.TemporaryDirectory(prefix="deepaudit_preview_") as folder:
        root = Path(folder)
        with patch.object(gui.App, "_autodetect", lambda self: None), \
                patch.object(settings, "SettingsStore", lambda: store_class(root / "settings.json")), \
                patch.object(auditlog, "AuditLog", lambda: log_class(root / "synthetic.jsonl")):
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
        app.after(600, lambda: workspace.navigate(app, args.page))
        app.mainloop()


if __name__ == "__main__":
    main()
