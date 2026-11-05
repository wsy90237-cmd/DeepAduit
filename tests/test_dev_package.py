from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import build_dev, run_gui


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
