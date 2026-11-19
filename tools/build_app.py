"""打一个双击即可运行的本地 APP（单文件 exe）。

与 `tools/build_release.py` 的区别：本脚本走 PyInstaller，适合本机自用与内部分发，
不做原生编译门槛；正式对外发布仍必须用 Nuitka 流程（AGENTS.md）。

用法：
    .venv\\Scripts\\python.exe -B tools/build_app.py            # 构建 + 自检
    .venv\\Scripts\\python.exe -B tools/build_app.py --check    # 只自检已有产物
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tb_autofill import APP_NAME, PRODUCT_MODULES, __version__, brand  # noqa: E402

# 运行时按需导入、静态分析容易漏掉的模块。
HIDDEN_IMPORTS = ("win32crypt", "win32com.client", "pythoncom", "pywintypes",
                  "pymysql", "xlrd", "openpyxl", "pypdf")
WORK = ROOT / "build" / "app"
TARGET = ROOT / "dist" / brand.EXECUTABLE


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_arguments(clean=True):
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
            "--name", Path(brand.EXECUTABLE).stem,
            "--distpath", str(ROOT / "dist"), "--workpath", str(WORK), "--specpath", str(WORK),
            "--add-data", f"{ROOT / 'tb_autofill' / 'assets'}{';' if sys.platform == 'win32' else ':'}tb_autofill/assets",
            "--paths", str(ROOT)]
    args += [f"--hidden-import={name}" for name in HIDDEN_IMPORTS]
    # 业务模块由 PRODUCT_MODULES 统一声明；部分模块（如 knowledge_ui）只被间接导入，
    # 不显式收集会在自检时报 ModuleNotFoundError。
    args += [f"--hidden-import=tb_autofill.{name}" for name in PRODUCT_MODULES]
    args.append("--collect-submodules=tb_autofill")
    icon = ROOT / "packaging" / "attesta.ico"
    if icon.is_file():
        args.append(f"--icon={icon}")
    # 没有版本资源，Windows 属性页的产品名称 / 版本 / 版权都是空白。
    args.append(f"--version-file={version_resource()}")
    if clean:
        args.append("--clean")
    return args + [str(ROOT / "launch.py")]


def version_resource():
    """写入临时版本资源文本并返回路径；内容统一来自 brand。"""
    WORK.mkdir(parents=True, exist_ok=True)
    target = WORK / "version_info.txt"
    target.write_text(brand.version_info_text(), encoding="utf-8")
    return target


def selftest(executable):
    """离线自检：跑 launch.py 的 --selftest，确认打包后依赖齐全。"""
    with tempfile.TemporaryDirectory(prefix="attesta_app_check_") as folder:
        report = Path(folder) / "selftest.json"
        subprocess.run([str(executable), "--selftest", f"--selftest-report={report}"],
                       timeout=600, check=True)
        data = json.loads(report.read_text(encoding="utf-8"))
    if data.get("version") != __version__ or data.get("product") != APP_NAME:
        raise RuntimeError("打包版本与源码不一致")
    return data


def revision():
    try:
        return subprocess.check_output(
            ["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "HEAD"],
            cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", type=Path, help="只对已有 exe 做自检")
    parser.add_argument("--skip-selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.check:
        print(json.dumps(selftest(args.check.resolve()), ensure_ascii=False, indent=2))
        return 0

    for name in ("PyInstaller",):
        try:
            __import__(name)
        except ImportError:
            raise SystemExit("缺少 PyInstaller，请先：.venv\\Scripts\\python.exe -m pip install pyinstaller")

    if os_name() != "nt":
        raise SystemExit("Windows APP 需在 Windows 构建")

    dist = ROOT / "dist"
    if dist.exists():
        for stale in dist.glob("*.exe"):
            stale.unlink()
    dist.mkdir(parents=True, exist_ok=True)

    print(f"构建 {APP_NAME} {__version__} → {TARGET}")
    subprocess.run(build_arguments(), cwd=ROOT, check=True)
    if not TARGET.is_file():
        raise SystemExit("构建结束但没有找到 exe")

    data = {} if args.skip_selftest else selftest(TARGET)
    digest = sha256(TARGET)
    manifest = {
        "product": APP_NAME,
        "version": __version__,
        "type": "pyinstaller-single-file-preview",
        "source_revision": revision(),
        "built_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "sha256": digest,
        "size": TARGET.stat().st_size,
        "selftest": "ok" if data else "skipped",
        "signed": False,
        "note": "本机试用版；正式发布使用 tools/build_release.py 的 Nuitka 原生编译。",
    }
    (dist / "BUILDINFO.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    (dist / "SHA256SUMS.txt").write_text(f"{digest}  {brand.EXECUTABLE}\n", encoding="utf-8")
    instructions = (ROOT / "packaging" / "安装说明.txt").read_text(encoding="utf-8")
    instructions = instructions.replace("{VERSION}", __version__)
    instructions += ("\n\n本包说明\n--------\n本包为 PyInstaller 单文件试用版，便于本机与内部分发；"
                     "正式对外发布请使用 tools/build_release.py 的 Nuitka 原生编译产物。\n")
    (dist / "INSTALL.txt").write_text(instructions, encoding="utf-8")
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"完成：{TARGET}\nSHA256 {digest}\n大小 {manifest['size'] / 1024 / 1024:.1f} MB")
    return 0


def os_name():
    import os

    return os.name


if __name__ == "__main__":
    raise SystemExit(main())
