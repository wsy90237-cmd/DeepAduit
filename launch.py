import sys

from tb_autofill import __version__


def _selftest():
    """打包后自检：只验证模块可导入，不打开窗口。供 CI 验证 EXE 可用。"""
    from tb_autofill import ai, analysis, auditlog, dxn, fields, gui, matching, settings, workbook  # noqa: F401
    print(f"selftest ok {__version__}")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    from tb_autofill.gui import main
    main()
