import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tb_autofill import __version__
from tools.build_release import verify_native
from tools.inspect_release import inspect


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "synthetic.exe"
        self.pe = bytearray(512)
        self.pe[:2] = b"MZ"
        struct.pack_into("<I", self.pe, 0x3C, 0x80)
        self.pe[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", self.pe, 0x84, 0x8664)
        struct.pack_into("<H", self.pe, 0x98, 0x20B)
        struct.pack_into("<H", self.pe, 0x98 + 70, 0x160)
        self.path.write_bytes(self.pe)

    def test_read_only_pe_checks_report_mitigations(self):
        report = inspect(self.path)
        self.assertTrue(report["aslr"] and report["dep"])
        self.assertFalse(report["authenticode_table_present"])
        self.assertFalse(report["pyinstaller_archive_cookie"])

    def test_legacy_python_archive_is_refused(self):
        self.path.write_bytes(self.pe + b"MEI\x0c\x0b\x0a\x0b\x0e")
        with self.assertRaisesRegex(ValueError, "PyInstaller"):
            inspect(self.path)

    def test_missing_dep_is_refused(self):
        struct.pack_into("<H", self.pe, 0x98 + 70, 0x40)
        self.path.write_bytes(self.pe)
        with self.assertRaisesRegex(ValueError, "ASLR|DEP"):
            inspect(self.path)

    def test_truncated_pe_is_refused(self):
        self.path.write_bytes(self.pe[:100])
        with self.assertRaisesRegex(ValueError, "截断"):
            inspect(self.path)

    def test_selftest_cannot_pass_with_an_interpreted_business_module(self):
        def simulated_target(args, **kwargs):
            report = Path(args[2].split("=", 1)[1])
            report.write_text(json.dumps({"version": __version__, "product": "DeepAudit",
                                         "native_modules": {"tb_autofill.matching": False}}), encoding="utf-8")
        with patch("tools.build_release.subprocess.run", side_effect=simulated_target):
            with self.assertRaisesRegex(RuntimeError, "原生编译"):
                verify_native(self.path)


if __name__ == "__main__":
    unittest.main()
