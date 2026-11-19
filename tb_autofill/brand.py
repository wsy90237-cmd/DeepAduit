"""Brand constants and icon paths.

The icon files are derived from the Attesta mark supplied by the project owner.
Keep every user-visible brand reference here so a future rename touches one place.
"""

from pathlib import Path

from . import APP_NAME

ASSETS = Path(__file__).with_name("assets")
ICON_SIZES = (32, 48, 256)
EXECUTABLE = f"{APP_NAME}.exe"
WINDOW_TITLE = f"{APP_NAME} · 审计工作台"
PACKAGE_FILES = ("launch.py", "启动Attesta.cmd", "Attesta-trial.cmd", "requirements.txt")


def icon(size: int) -> Path:
    if size not in ICON_SIZES:
        raise ValueError(f"未定义的图标尺寸：{size}")
    return ASSETS / f"attesta-{size}.png"
