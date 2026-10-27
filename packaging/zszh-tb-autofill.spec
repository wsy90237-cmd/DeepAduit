# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件窗口程序，无控制台。

构建：pyinstaller packaging/zszh-tb-autofill.spec
产物：dist/ZSZH-TB自动填写.exe
"""

import os

# spec 内的相对路径以本文件所在目录为基准，入口需回退到项目根目录。
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # noqa: F821 - SPECPATH 由 PyInstaller 注入

a = Analysis(
    [os.path.join(ROOT, "launch.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[
        "pymysql",
        "win32com",
        "win32com.client",
        "pythoncom",
        "pywintypes",
        "win32crypt",
        "openpyxl",
        "xlrd",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "pandas", "scipy", "akshare", "tkinter.test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ZSZH-TB自动填写",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
