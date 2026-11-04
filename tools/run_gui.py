"""独立启动入口：供 pythonw 拉起界面，异常写入本机日志便于排查。

用法（不弹控制台窗口）：
    .venv\\Scripts\\pythonw.exe tools\\run_gui.py
"""

from __future__ import annotations

from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
LOG = Path.home() / "AppData" / "Local" / "Temp" / "deepaudit-gui.log"


def main():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    site_packages = ROOT / ".venv" / "Lib" / "site-packages"
    if site_packages.is_dir():
        sys.path.append(str(site_packages))
    try:
        from tb_autofill.gui import main as start_gui
        start_gui()
    except Exception:                       # 打包或依赖问题时留下可读堆栈
        LOG.write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    main()
