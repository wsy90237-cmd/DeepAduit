"""Bundle an installed Windows Python and allowlisted source; no compilation."""

import argparse
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
TEMPLATE_LIBRARY = ROOT / "local-data" / "dxn-library-20261105"


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


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def carry_templates(library, output):
    """把本地模板原件按标准目录带入开发包，并生成可复核的校验清单。

    原件来自静态解析的安装空白模板，不依赖原软件安装路径；每个标准目录的
    ``ORIGINALS.sha256`` 先与复制结果逐一比对，再汇总成 ``templates/TEMPLATES.sha256``。
    """
    library = Path(library).resolve()
    coverage = library / "coverage.json"
    if not coverage.is_file():
        raise SystemExit(f"模板库缺少 coverage.json：{library}")
    standards = json.loads(coverage.read_text(encoding="utf-8"))["standards"]
    missing = [item["standard"] for item in standards
               if not (library / item["standard"] / "originals").is_dir()]
    if not standards or missing:
        raise SystemExit(f"模板库原件不完整，缺少：{'、'.join(missing) or '全部'}")
    target = output / "templates"
    target.mkdir()
    lines, copied = [], 0
    for item in standards:
        slug = item["standard"]
        source = library / slug / "originals"
        manifest = library / slug / "ORIGINALS.sha256"
        expected = {}
        for row in manifest.read_text(encoding="utf-8").splitlines():
            digest, _, relative = row.partition("  ")
            expected[relative] = digest
        destination = target / slug / "originals"
        shutil.copytree(source, destination)
        for relative, digest in expected.items():
            actual = sha256_file(destination / relative)
            if actual != digest:
                raise RuntimeError(f"模板原件校验失败：{slug}/{relative}")
            lines.append(f"{actual}  {slug}/originals/{relative}")
            copied += 1
    manifest = target / "TEMPLATES.sha256"
    manifest.write_text("\n".join(sorted(lines)) + "\n", encoding="utf-8")
    shutil.copy2(coverage, target / "COVERAGE.json")
    return {"standards": len(standards), "files": copied,
            "manifest": "templates/TEMPLATES.sha256", "manifest_sha256": sha256_file(manifest)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template-library", default=str(TEMPLATE_LIBRARY),
                        help="携带模板原件的本地库目录；默认 local-data/dxn-library-20261105")
    parser.add_argument("--skip-templates", action="store_true", help="不携带模板原件（仅调试用）")
    args = parser.parse_args()
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
    # 自检只针对程序本身；模板原件体积大，放在自检之后携带，避免拖慢启动校验。
    templates = {} if args.skip_templates else carry_templates(args.template_library, output)
    manifest = {"product": APP_NAME, "version": __version__, "kind": "developer-source",
                "compiled": False, "source_revision": revision, "python": platform.python_version(),
                "dependencies": versions, "templates": templates, "selftest": result["checks"]}
    (output / "BUILDINFO.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files = sorted(path for path in output.rglob("*") if path.is_file())
    sums = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        sums.append(f"{digest}  {path.relative_to(output).as_posix()}\n")
    (output / "FILES.sha256").write_text("".join(sums), encoding="utf-8")
    archive = output / f"{output.name}-windows-x64.zip"
    with zipfile.ZipFile(archive, "x") as bundle:
        for path in [*files, output / "FILES.sha256"]:
            name = Path(output.name) / path.relative_to(output)
            # 模板原件是已压缩的 OLE 工作簿，原样存储可把打包时间控制在可接受范围。
            if path.relative_to(output).parts[:1] == ("templates",):
                bundle.write(path, name, compress_type=zipfile.ZIP_STORED)
            else:
                bundle.write(path, name, compress_type=zipfile.ZIP_DEFLATED)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"{archive}\nSHA256 {digest}")


if __name__ == "__main__":
    main()
