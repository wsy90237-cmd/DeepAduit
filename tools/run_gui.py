"""Start the source/developer package and report otherwise invisible failures."""

from __future__ import annotations

from pathlib import Path
import runpy
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
LOG = Path(tempfile.gettempdir()) / "deepaudit-gui.log"


def main():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    try:
        runpy.run_path(str(ROOT / "launch.py"), run_name="__main__")
    except Exception:
        detail = traceback.format_exc()
        message = "DeepAudit 启动失败。"
        try:
            LOG.write_text(detail, encoding="utf-8")
            message += f"\n诊断日志：{LOG}"
        except OSError:
            message += "\n无法保存日志，请运行 DeepAudit-trial.cmd 查看错误。"
        if sys.stderr is not None:
            print(detail, file=sys.stderr)
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "DeepAudit", 0x10)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
