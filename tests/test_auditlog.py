import json
import tempfile
import unittest
from pathlib import Path

from tb_autofill.auditlog import GENESIS, AuditLog


class AuditLogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "audit" / "2026-10.jsonl"
        self.log = AuditLog(self.path, actor="审计员A")

    def tearDown(self):
        self.dir.cleanup()

    def test_append_builds_hash_chain(self):
        first = self.log.append("write_excel", cells=[{"cell": "期末!D10", "after": "12000"}])
        second = self.log.append("export_report", path="x.csv")
        self.assertEqual((first["seq"], first["prev"]), (1, GENESIS))
        self.assertEqual((second["seq"], second["prev"]), (2, first["hash"]))
        self.assertEqual(first["actor"], "审计员A")
        self.assertIn("ts", first)
        self.assertEqual(self.log.verify(), (True, None, ""))

    def test_empty_log_is_valid(self):
        self.assertEqual(self.log.verify(), (True, None, ""))

    def test_rewritten_record_detected(self):
        self.log.append("write_excel", after="12000")
        self.log.append("write_excel", after="18000")
        lines = self.path.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[0])
        tampered["after"] = "1"
        lines[0] = json.dumps(tampered, ensure_ascii=False, sort_keys=True)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, seq, reason = self.log.verify()
        self.assertFalse(ok)
        self.assertEqual(seq, 1)
        self.assertIn("改写", reason)

    def test_deleted_record_detected(self):
        for count in range(3):
            self.log.append("write_excel", after=str(count))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
        ok, seq, _reason = self.log.verify()
        self.assertFalse(ok)
        self.assertEqual(seq, 2)

    def test_records_roundtrip(self):
        self.log.append("write_excel", after="1")
        self.log.append("export_report", path="a.csv")
        records = self.log.records()
        self.assertEqual([item["event"] for item in records], ["write_excel", "export_report"])


if __name__ == "__main__":
    unittest.main()
