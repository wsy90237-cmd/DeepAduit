"""Brand constants and icon paths.

The icon files are derived from the Attesta mark supplied by the project owner.
Keep every user-visible brand reference here so a future rename touches one place.
"""

from pathlib import Path

from . import APP_NAME, __version__

ASSETS = Path(__file__).with_name("assets")
ICON_SIZES = (32, 48, 256)
EXECUTABLE = f"{APP_NAME}.exe"
WINDOW_TITLE = f"{APP_NAME} · 审计工作台"
PACKAGE_FILES = ("launch.py", "启动Attesta.cmd", "Attesta-trial.cmd", "requirements.txt")

# 文件属性与界面页脚共用同一处来源：PE 资源里的公司、版权、产品名都从这里取，
# 避免打包脚本各写一份、Windows 属性页出现空白。
COMPANY = "WSY"
COPYRIGHT = "© WSY"
PRODUCT_NAME = APP_NAME
FILE_DESCRIPTION = f"{APP_NAME} · 审计工作台"
FOOTER_TEXT = f"{APP_NAME}  v{__version__} · {COPYRIGHT}"


def icon(size: int) -> Path:
    if size not in ICON_SIZES:
        raise ValueError(f"未定义的图标尺寸：{size}")
    return ASSETS / f"attesta-{size}.png"


def version_numbers():
    """把 `0.8.2` 之类的版本号补成 PE 需要的四段整数。"""
    parts = []
    for chunk in __version__.split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        parts.append(int(digits or 0))
    return tuple((parts + [0, 0, 0, 0])[:4])


def version_info_text() -> str:
    """生成 PyInstaller `--version-file` 使用的 VSVersionInfo 文本。

    没有这份资源，Windows「属性 → 详细信息」里的产品名称、版本、版权都是空白，
    用户无法从 exe 本身确认来源与版本。
    """
    major, minor, patch, build = version_numbers()
    strings = (
        ("CompanyName", COMPANY),
        ("FileDescription", FILE_DESCRIPTION),
        ("FileVersion", __version__),
        ("InternalName", PRODUCT_NAME),
        ("LegalCopyright", COPYRIGHT),
        ("OriginalFilename", EXECUTABLE),
        ("ProductName", PRODUCT_NAME),
        ("ProductVersion", __version__),
    )
    body = ",\n".join(f"            StringStruct('{key}', '{value}')" for key, value in strings)
    return (
        "# UTF-8\n"
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(\n"
        f"    filevers=({major}, {minor}, {patch}, {build}),\n"
        f"    prodvers=({major}, {minor}, {patch}, {build}),\n"
        "    mask=0x3f,\n"
        "    flags=0x0,\n"
        "    OS=0x40004,\n"
        "    fileType=0x1,\n"
        "    subtype=0x0,\n"
        "    date=(0, 0)\n"
        "  ),\n"
        "  kids=[\n"
        "    StringFileInfo(\n"
        "      [\n"
        "        StringTable('080404b0', [\n"
        f"{body}\n"
        "        ])\n"
        "      ]\n"
        "    ),\n"
        "    VarFileInfo([VarStruct('Translation', [2052, 1200])])\n"
        "  ]\n"
        ")\n"
    )
