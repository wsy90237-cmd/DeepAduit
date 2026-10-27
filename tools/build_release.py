"""Build only a native-compiled customer release; never distribute source trees."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tb_autofill import APP_NAME, __version__
try:
    from .inspect_release import inspect
except ImportError:
    from inspect_release import inspect


def build_arguments(compiler, jobs):
    output = ROOT / "dist"
    options = [sys.executable, "-m", "nuitka", "--mode=onefile", "--include-package=tb_autofill",
               "--enable-plugin=tk-inter", "--include-module=win32com.client", "--include-module=win32crypt",
               "--include-module=pythoncom", "--windows-console-mode=disable", "--python-flag=isolated",
               "--python-flag=no_docstrings", "--file-reference-choice=runtime",
               "--nofollow-import-to=tests,unittest,pytest,tkinter.test",
               "--assume-yes-for-downloads", f"--jobs={jobs}", "--lto=no",
               f"--output-dir={output}", "--output-filename=DeepAudit.exe",
               f"--report={ROOT / 'build' / 'native-compilation-report.xml'}",
               f"--product-name={APP_NAME}", f"--file-description={APP_NAME} Audit Workspace",
               f"--file-version={__version__}", f"--product-version={__version__}"]
    if compiler == "msvc":
        options.append("--msvc=latest")
    elif compiler == "zig":
        options.append("--zig")
    icon = ROOT / "packaging" / "deepaudit.ico"
    if icon.is_file():
        options.append(f"--windows-icon-from-ico={icon}")
    return options + [str(ROOT / "launch.py")]


def verify_native(executable):
    inspect(executable)
    with tempfile.TemporaryDirectory(prefix="deepaudit_release_check_") as folder:
        report = Path(folder) / "selftest.json"
        subprocess.run([str(executable), "--selftest", f"--selftest-report={report}"], timeout=180, check=True)
        result = json.loads(report.read_text(encoding="utf-8"))
        if result["version"] != __version__ or result["product"] != APP_NAME:
            raise RuntimeError("打包版本不匹配")
        if not result["native_modules"] or not all(result["native_modules"].values()):
            raise RuntimeError("拒绝发布：业务模块并非全部原生编译")
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", choices=("auto", "msvc", "zig"), default="auto")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--check-only", type=Path)
    args = parser.parse_args()
    if args.check_only:
        print(json.dumps(verify_native(args.check_only.resolve()), ensure_ascii=False, indent=2))
        return
    if os.name != "nt":
        raise SystemExit("Windows 发布包必须在 Windows 构建")
    if args.jobs < 1:
        raise SystemExit("jobs 必须为正数")
    tag = os.environ.get("GITHUB_REF", "")
    if tag.startswith("refs/tags/v") and tag.removeprefix("refs/tags/v") != __version__:
        raise SystemExit("发布标签与包版本不一致，停止构建")
    (ROOT / "build").mkdir(exist_ok=True)
    subprocess.run(build_arguments(args.compiler, args.jobs), cwd=ROOT, check=True)
    executable = ROOT / "dist" / "DeepAudit.exe"
    result = verify_native(executable)
    with executable.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    manifest = {"product": APP_NAME, "version": __version__, "source_revision": revision, "source_dirty": dirty,
                "python": platform.python_version(), "compiler": args.compiler,
                "sha256": digest, "native_modules": result["native_modules"],
                "selftest": result["checks"], "pe": inspect(executable), "signed": False}
    (ROOT / "dist" / "BUILDINFO.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "dist" / "SHA256SUMS.txt").write_text(f"{digest}  DeepAudit.exe\n", encoding="utf-8")
    instructions = (ROOT / "packaging" / "安装说明.txt").read_text(encoding="utf-8")
    (ROOT / "dist" / "INSTALL.txt").write_text(instructions.replace("{VERSION}", __version__), encoding="utf-8")
    print(f"DeepAudit {__version__}: {executable}\nSHA256 {digest}")


if __name__ == "__main__":
    main()
