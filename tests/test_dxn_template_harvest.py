"""Synthetic wrapper blocks and workbook metadata; no customer files or Excel UI."""

import io
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
import zlib

from openpyxl import Workbook

from tools.harvest_dxn_templates import _xls_formulas, compact_catalog, decode_zv, harvest, inspect_template


def wrapped(*parts):
    result = b"ZV\0\0" + bytes(20)
    for part in parts:
        chunk = zlib.compress(part)
        result += struct.pack("<I", len(chunk)) + chunk
    return result


class TemplateHarvestTests(unittest.TestCase):
    def test_multiple_blocks_are_joined_not_silently_truncated(self):
        self.assertEqual(decode_zv(wrapped(b"first block", b"second block")), b"first blocksecond block")

    def test_truncated_block_and_trailing_bytes_are_rejected(self):
        for raw in (wrapped(b"first", b"second")[:-2], wrapped(b"first") + b"bad"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_zv(raw)

    def test_decode_is_bounded(self):
        with self.assertRaises(ValueError):
            decode_zv(wrapped(b"x" * 1000), max_decoded=100)

    def test_plain_format_is_preserved(self):
        self.assertEqual(decode_zv(b"PK plain workbook"), b"PK plain workbook")

    def test_shared_formula_is_expanded_for_each_cell(self):
        def record(code, value):
            return struct.pack("<HH", code, len(value)) + value
        tokens = b"\x4c" + struct.pack("<HH", 0, 0xC0FF) + b"\x1e\x02\x00\x05"
        memory = b""
        for row in (0, 1):
            expression = b"\x01" + struct.pack("<HH", 0, 1)
            value = struct.pack("<HH", row, 1) + bytes(16) + struct.pack("<H", len(expression)) + expression
            memory += record(6, value)
        memory += record(0x4BC, struct.pack("<HHBBBBH", 0, 1, 1, 1, 0, 2, len(tokens)) + tokens)
        memory += record(0x0A, b"")
        book = SimpleNamespace(mem=memory, base=0, stream_len=len(memory), _sh_abs_posn=[0],
                               biff_version=80, logfile=io.StringIO())
        formulas, errors = _xls_formulas(book, 0, {})
        self.assertEqual(errors, [])
        self.assertEqual(formulas, [{"cell": "B1", "formula": "A1*2.0", "shared_from": "B1"},
                                    {"cell": "B2", "formula": "A2*2.0", "shared_from": "B1"}])
        missing = record(6, struct.pack("<HH", 0, 1) + bytes(16) + struct.pack("<H", 5)
                         + b"\x01" + struct.pack("<HH", 0, 1)) + record(0x0A, b"")
        book.mem, book.stream_len = missing, len(missing)
        formulas, errors = _xls_formulas(book, 0, {})
        self.assertEqual(formulas, [])
        self.assertEqual(errors[0]["cell"], "B1")

    def test_structure_keeps_fields_and_formulas_without_amounts(self):
        book = Workbook()
        sheet = book.active
        sheet.title = "明细表"
        sheet["A1"], sheet["B1"], sheet["C1"] = "科目名称", "1月发生额", "账面上期发生额"
        sheet["A13"], sheet["B13"], sheet["C13"] = "private-row-label", 1234567, "=B13*2"
        content = io.BytesIO()
        book.save(content)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "synthetic.xlsx"
            source.write_bytes(wrapped(content.getvalue()[:100], content.getvalue()[100:]))
            item, decoded = inspect_template(source)
            self.assertEqual(decoded, content.getvalue())
            self.assertEqual(item["sheets"][0]["fields"][1], {"cell": "B1", "text": "1月发生额"})
            self.assertEqual(item["sheets"][0]["formulas"], [{"cell": "C13", "formula": "B13*2"}])
            self.assertNotIn("private-row-label", str(item))
            self.assertNotIn("1234567", str(item))
            report = harvest(root)
            self.assertEqual(report["coverage"]["parsed_templates"], 1)
            self.assertEqual(compact_catalog(report)["templates"][0]["sheets"][0]["formula_count"], 1)

    def test_original_installation_is_not_an_extraction_destination(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            harvest(folder, extract_dir=Path(folder) / "decoded")


class LibraryHarvestTests(unittest.TestCase):
    """多标准覆盖清单：每个标准独立记录版本、覆盖与原件校验，且拒绝写回安装目录。"""

    def library(self, folder):
        root = Path(folder) / "hydemo"
        standard = root / "合成准则"
        (standard / "xls_demo").mkdir(parents=True)
        book = Workbook()
        book.active["A1"] = "科目名称"
        content = io.BytesIO()
        book.save(content)
        (standard / "xls_demo" / "货币资金.xlsx").write_bytes(
            wrapped(content.getvalue()[:80], content.getvalue()[80:]))
        (standard / "Ver.txt").write_bytes("合成准则\t1100099999\t9.9.9.9\n".encode("gb18030"))
        return root

    def test_each_standard_records_version_coverage_and_original_manifest(self):
        from tools import harvest_dxn_library
        with tempfile.TemporaryDirectory() as folder:
            root = self.library(folder)
            out = Path(folder) / "out"
            inventory = harvest_dxn_library.harvest_one(root / "合成准则", out, keep_originals=True)
            self.assertEqual(inventory["declared"]["version"], "9.9.9.9")
            self.assertEqual(inventory["coverage"]["parsed_templates"], 1)
            self.assertEqual(inventory["originals"]["files"], 2)
            manifest = (out / "合成准则" / "ORIGINALS.sha256").read_text(encoding="utf-8")
            self.assertIn("xls_demo/货币资金.xlsx", manifest)
            self.assertIn("Ver.txt", manifest)

    def test_output_inside_installation_directory_is_refused(self):
        from tools import harvest_dxn_library
        with tempfile.TemporaryDirectory() as folder:
            root = self.library(folder)
            for target in (root / "合成准则" / "originals", root):
                with self.subTest(target=target), self.assertRaises(SystemExit):
                    harvest_dxn_library.check_destination(root, target)

    def test_reused_evidence_must_match_the_same_source_root(self):
        from tools import harvest_dxn_library
        with tempfile.TemporaryDirectory() as folder:
            root = self.library(folder)
            other = Path(folder) / "other.json"
            other.write_text('{"source_root": "C:/synthetic/elsewhere"}', encoding="utf-8")
            self.assertIsNone(harvest_dxn_library._reusable(root / "合成准则", [other]))
