from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

from tools import build_dev, run_gui


def digest(data):
    return hashlib.sha256(data).hexdigest()


class TemplateCarryTests(unittest.TestCase):
    """模板原件随包携带时必须可复核：逐文件校验，缺件或篡改直接拒绝。"""

    def library(self, folder):
        root = Path(folder) / "library"
        (root / "合成准则" / "originals" / "xls_demo").mkdir(parents=True)
        (root / "合成准则" / "originals" / "xls_demo" / "货币资金.xls").write_bytes(b"synthetic-blank")
        manifest = digest(b"synthetic-blank") + "  xls_demo/货币资金.xls\n"
        (root / "合成准则" / "ORIGINALS.sha256").write_text(manifest, encoding="utf-8")
        (root / "coverage.json").write_text(json.dumps({"standards": [
            {"standard": "合成准则", "declared": {"version": "9.9.9.9"}}]}, ensure_ascii=False), encoding="utf-8")
        return root

    def test_templates_are_copied_with_a_verifiable_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            library = self.library(folder)
            output = Path(folder) / "package"
            output.mkdir()
            result = build_dev.carry_templates(library, output)
            self.assertEqual(result["standards"], 1)
            self.assertEqual(result["files"], 1)
            copied = output / "templates" / "合成准则" / "originals" / "xls_demo" / "货币资金.xls"
            self.assertEqual(copied.read_bytes(), b"synthetic-blank")
            rows = (output / "templates" / "TEMPLATES.sha256").read_text(encoding="utf-8").splitlines()
            self.assertEqual(rows, [f"{digest(b'synthetic-blank')}  合成准则/originals/xls_demo/货币资金.xls"])
            self.assertEqual(result["manifest_sha256"],
                             digest((output / "templates" / "TEMPLATES.sha256").read_bytes()))

    def test_tampered_original_is_rejected_instead_of_shipped(self):
        with tempfile.TemporaryDirectory() as folder:
            library = self.library(folder)
            target = library / "合成准则" / "originals" / "xls_demo" / "货币资金.xls"
            target.write_bytes(b"synthetic-changed")
            output = Path(folder) / "package"
            output.mkdir()
            with self.assertRaises(RuntimeError):
                build_dev.carry_templates(library, output)

    def test_missing_coverage_stops_the_build(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "package"
            output.mkdir()
            with self.assertRaises(SystemExit):
                build_dev.carry_templates(Path(folder) / "empty", output)


class DeveloperPackageTests(unittest.TestCase):
    def test_runtime_allowlist_excludes_caches_tests_and_external_scripts(self):
        for name in ("../Scripts/private.py", "package/__pycache__/x.pyc", "package/tests/x.py",
                     "/private.py", "C:/private.py", "C:private.py"):
            self.assertFalse(build_dev.runtime_file(Path(name)), name)
        for name in ("win32/win32crypt.pyd", "pywin32.pth", "pypdf/_reader.py", "assets/skills/reference.md"):
            self.assertTrue(build_dev.runtime_file(Path(name)), name)

    def test_launchers_are_ascii_and_use_windows_line_endings(self):
        for name in ("启动DeepAudit.cmd", "DeepAudit-trial.cmd"):
            data = (build_dev.ROOT / name).read_bytes()
            data.decode("ascii")
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
            self.assertIn(b"runtime\\python", data)

    def test_failed_windowed_start_writes_log_and_displays_location(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "synthetic-error.log"
            with patch.object(run_gui, "LOG", log), \
                    patch.object(run_gui.runpy, "run_path", side_effect=ImportError("synthetic missing dependency")), \
                    patch("ctypes.windll.user32.MessageBoxW") as dialog, patch.object(run_gui.sys, "stderr", None):
                self.assertEqual(run_gui.main(), 1)
            self.assertIn("synthetic missing dependency", log.read_text(encoding="utf-8"))
            self.assertIn(str(log), dialog.call_args.args[1])
