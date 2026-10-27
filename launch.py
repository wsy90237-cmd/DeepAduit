import sys
import json
from pathlib import Path
import tempfile
from decimal import Decimal

from tb_autofill import APP_NAME, __version__


def _selftest(report=None):
    """Offline packaged checks use synthetic data and never read user settings."""
    from tb_autofill import ai, analysis, auditlog, dxn, fields, gui, matching, models, settings, workbook, workspace
    from openpyxl import Workbook
    modules = (ai, analysis, auditlog, dxn, fields, gui, matching, models, settings, workbook, workspace)
    result = matching.resolve_match(models.TargetField("Synthetic", "B2", "库存现金", "ending"),
                                    [models.LedgerRow("1001", "库存现金", ending=Decimal("100"))])
    if result.amount != 100 or not result.selected:
        raise RuntimeError("金额保护自检失败")
    with tempfile.TemporaryDirectory(prefix="deepaudit_selftest_") as folder:
        root = Path(folder)
        store = settings.SettingsStore(root / "synthetic-settings.json")
        synthetic_key = "selftest-non-secret-key"
        store.save_ai(synthetic_key, enabled=True)
        if store.get_key() != synthetic_key or synthetic_key in store.path.read_text(encoding="utf-8"):
            raise RuntimeError("本机密钥加密自检失败")
        log = auditlog.AuditLog(root / "synthetic.jsonl", actor="selftest")
        log.append("synthetic_first")
        log.append("synthetic_second")
        if not log.verify()[0]:
            raise RuntimeError("日志自检失败")
        lines = log.path.read_text(encoding="utf-8").splitlines()
        log.path.write_text(lines[0] + "\n", encoding="utf-8")
        if log.verify()[0]:
            raise RuntimeError("日志截断保护自检失败")
        book = Workbook()
        sheet = book.active
        sheet.append(["科目名称", "期末余额"])
        sheet.merge_cells("A1:A2")
        sheet["B2"], sheet["A3"] = "审定数", "库存现金"
        path = root / "synthetic.xlsx"
        book.save(path)
        book.close()
        scan = fields.scan_template(path, unit="元")
        if not scan.fields or scan.fields[0].basis != "audited":
            raise RuntimeError("表头保护自检失败")
    native = {module.__name__: bool(getattr(module, "__compiled__", False)) for module in modules}
    value = {"product": APP_NAME, "version": __version__, "native_modules": native,
             "checks": ["decimal_amount", "dpapi_key", "audit_checkpoint", "protected_header"],
             "data": "synthetic only"}
    if report:
        with Path(report).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
    if sys.stdout is not None:
        print(f"{APP_NAME} selftest ok {__version__}")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        report = next((arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--selftest-report=")), None)
        raise SystemExit(_selftest(report))
    from tb_autofill.gui import main
    main()
