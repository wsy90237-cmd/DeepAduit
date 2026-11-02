import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tb_autofill import __version__
from tools import build_release
from tools.build_release import NATIVE_MODULES, verify_native
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

    def test_selftest_requires_the_complete_native_module_set_and_real_booleans(self):
        for native in ({'tb_autofill.matching': True}, {name: 'true' for name in NATIVE_MODULES},
                       {name: True for name in NATIVE_MODULES if name != 'tb_autofill.skills'}):
            def simulated_target(args, **kwargs):
                report = Path(args[2].split('=', 1)[1])
                report.write_text(json.dumps({'version': __version__, 'product': 'DeepAudit',
                                              'native_modules': native}), encoding='utf-8')
            with self.subTest(native=native), patch('tools.build_release.subprocess.run', side_effect=simulated_target):
                with self.assertRaisesRegex(RuntimeError, '原生编译'):
                    verify_native(self.path)

    def test_complete_native_selftest_passes(self):
        def simulated_target(args, **kwargs):
            report = Path(args[2].split('=', 1)[1])
            report.write_text(json.dumps({'version': __version__, 'product': 'DeepAudit',
                                          'native_modules': dict.fromkeys(NATIVE_MODULES, True)}), encoding='utf-8')
        with patch('tools.build_release.subprocess.run', side_effect=simulated_target):
            self.assertEqual(set(verify_native(self.path)['native_modules']), NATIVE_MODULES)

    def test_dirty_source_is_refused_before_compilation(self):
        with patch.object(build_release.sys, 'argv', ['build_release.py']), \
                patch.object(build_release.subprocess, 'check_output', side_effect=['a' * 40, ' M source.py']), \
                patch.object(build_release.subprocess, 'run') as compile_run:
            with self.assertRaisesRegex(SystemExit, '工作区干净'):
                build_release.main()
            compile_run.assert_not_called()

    def test_source_change_during_compilation_refuses_manifest(self):
        root = Path(self.folder.name)
        (root / 'dist').mkdir()
        (root / 'dist/DeepAudit.exe').write_bytes(self.pe)
        result = {'native_modules': dict.fromkeys(NATIVE_MODULES, True), 'checks': []}
        with patch.object(build_release, 'ROOT', root), \
                patch.object(build_release.sys, 'argv', ['build_release.py']), \
                patch.object(build_release.subprocess, 'check_output', side_effect=['a' * 40, '', 'b' * 40]), \
                patch.object(build_release.subprocess, 'run'), \
                patch.object(build_release, 'verify_native', return_value=result):
            with self.assertRaisesRegex(RuntimeError, '构建期间源码'):
                build_release.main()
        self.assertFalse((root / 'dist/BUILDINFO.json').exists())


if __name__ == "__main__":
    unittest.main()
