"""Bundle an installed Windows Python and allowlisted source; no compilation."""

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tb_autofill import APP_NAME, __version__

DEPENDENCIES = ("openpyxl", "et_xmlfile", "xlrd", "PyMySQL", "pywin32", "pypdf")
SKIP = {"__pycache__", "test", "tests", "demos", "site-packages", "idlelib", "turtledemo"}


def runtime_file(path):
    return (not path.anchor and ".." not in path.parts
            and not set(path.parts) & SKIP and path.suffix.lower() not in {".pyc", ".pyo", ".chm"})


def ignore_runtime(directory, names):
    return [name for name in names if not runtime_file(Path(name))]


def copy_runtime(destination):
    base = Path(sys.base_prefix)
    destination.mkdir()
    for pattern in ("python*.exe", "python*.dll", "vcruntime*.dll", "LICENSE.txt"):
        for path in base.glob(pattern):
            shutil.copy2(path, destination / path.name)
    for name in ("Lib", "DLLs", "tcl"):
        shutil.copytree(base / name, destination / name, ignore=ignore_runtime)
    site = destination / "Lib" / "site-packages"
    versions = {}
    for name in DEPENDENCIES:
        distribution = metadata.distribution(name)
        versions[name] = distribution.version
        for path in distribution.files or []:
            if not runtime_file(path):
                continue
            target = site / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(distribution.locate_file(path), target)
    return versions


def git(*args):
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", *args], cwd=ROOT, text=True).strip()


def main():
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("开发包需要 Windows x64 Python。")
    revision = git("rev-parse", "HEAD")
    if git("status", "--porcelain"):
        raise SystemExit("请先提交源码，再打包开发版。")
    output = ROOT / "dist" / f"{APP_NAME}-{__version__}-dev"
    output.mkdir(parents=True, exist_ok=False)
    versions = copy_runtime(output / "runtime")
    shutil.copytree(ROOT / "tb_autofill", output / "tb_autofill", ignore=ignore_runtime)
    (output / "tools").mkdir()
    shutil.copy2(ROOT / "tools/run_gui.py", output / "tools/run_gui.py")
    for name in ("launch.py", "启动DeepAudit.cmd", "DeepAudit-trial.cmd", "requirements.txt"):
        shutil.copy2(ROOT / name, output / name)
    shutil.copy2(ROOT / "packaging/开发版说明.txt", output / "使用说明.txt")
    report = output / "SELFTEST.json"
    subprocess.run([str(output / "runtime/python.exe"), "-I", "-B", str(output / "tools/run_gui.py"),
                    "--selftest", f"--selftest-report={report}"], cwd=output, timeout=180, check=True)
    result = json.loads(report.read_text(encoding="utf-8"))
    if result["version"] != __version__ or result["product"] != APP_NAME:
        raise RuntimeError("开发包自检版本不匹配。")
    if git("rev-parse", "HEAD") != revision or git("status", "--porcelain"):
        raise RuntimeError("源码在打包期间发生变化，请重新打包。")
    manifest = {"product": APP_NAME, "version": __version__, "kind": "developer-source",
                "compiled": False, "source_revision": revision, "python": platform.python_version(),
                "dependencies": versions, "selftest": result["checks"]}
    (output / "BUILDINFO.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = sorted(path for path in output.rglob("*") if path.is_file())
    sums = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        sums.append(f"{digest}  {path.relative_to(output).as_posix()}\n")
    (output / "FILES.sha256").write_text("".join(sums), encoding="utf-8")
    archive = output / f"{output.name}-windows-x64.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in [*files, output / "FILES.sha256"]:
            bundle.write(path, Path(output.name) / path.relative_to(output))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"{archive}\nSHA256 {digest}")


if __name__ == "__main__":
    main()
