import os
import shutil
import time
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import Workbook

from openpyxl import load_workbook

from tb_autofill import agent_tools, audit_index, matching
from tb_autofill.agent import ProjectSnapshot
from tb_autofill.models import JournalRows, LedgerRow, PaperData, Subject


def make_template(path):
    book = Workbook()
    sheet = book.active
    sheet.title = "期末"
    sheet.append(["科目名称", "期末余额"])
    sheet.append(["库存现金", None])
    sheet.append(["银行存款", None])
    book.save(path)
    book.close()
    return path


def rows():
    return [
        LedgerRow("1001", "库存现金", path="库存现金", opening=Decimal("50"),
                  debit=Decimal("60"), credit=Decimal("10"), ending=Decimal("100"),
                  subject_id="S1", year=2026, month=12, ledger_id="L1"),
        LedgerRow("1002", "银行存款", path="银行存款", opening=Decimal("200"),
                  debit=Decimal("30"), credit=Decimal("20"), ending=Decimal("210"),
                  subject_id="S1", year=2026, month=12, ledger_id="L1"),
    ]


def context(template, *, output=None):
    subject = Subject("S1", "合成企业", 2026, 12, str(Path(template).parent), ledger_id="L1")
    snapshot = ProjectSnapshot(subject=subject, rows=tuple(rows()),
                               subject_label="合成企业 / 2026-12", template_path=str(template),
                               unit="元", periods=("ending",), scan=None, results=(),
                               paper_library=(), app_revision=1, ledger_digest="ledger",
                               template_digest="template")
    return snapshot.context(output_dir=str(output or Path(template).parent / "out"))


def runtime(path=""):
    return SimpleNamespace(client=None, approved_path=str(path))


class AgentToolTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / "build"
        base.mkdir(parents=True, exist_ok=True)
        self.root = base / f"test-agent-tools-{os.getpid()}-{time.time_ns()}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.template = make_template(self.root / "template.xlsx")
        self.ctx = context(self.template)
        self.registry = agent_tools.build_registry()

    def call(self, name, arguments=None, path=""):
        return self.registry.call(name, arguments or {}, self.ctx, runtime(path))

    def test_project_context_and_scan(self):
        value = self.call("get_project_context")
        self.assertTrue(value["ok"])
        self.assertEqual(value["data"]["project"]["accounts"], 2)
        value = self.call("scan_template")
        self.assertTrue(value["ok"])
        self.assertEqual(len(self.ctx.results), 2)
        self.assertTrue(all(result.status == "matched" for result in self.ctx.results))

    def test_search_and_account_facts(self):
        value = self.call("search_accounts", {"query": "银行"})
        self.assertEqual([item["code"] for item in value["data"]["accounts"]], ["1002"])
        value = self.call("get_account_facts", {"codes": ["1001"]})
        self.assertEqual(value["data"]["accounts"][0]["ending"], "100")

    def test_run_tb_ai_fill_returns_cleanly_when_no_batch(self):
        self.call("scan_template")
        value = self.call("run_tb_ai_fill")
        self.assertTrue(value["ok"], value)
        self.assertEqual(value["data"]["applied"], 0)

    def test_stage_tb_sources_recomputes_locally(self):
        self.call("scan_template")
        target = self.ctx.results[0]
        value = self.call("stage_tb_sources", {"items": [{
            "target_id": target.target.id, "source_codes": ["1001"]}]})
        self.assertTrue(value["ok"])
        self.assertEqual(value["data"]["items"][0]["amount"], "100")
        self.assertEqual(self.ctx.results[0].amount, Decimal("100"))

    def test_unknown_target_is_rejected(self):
        value = self.call("stage_tb_sources", {"items": [{
            "target_id": "期末!Z99", "source_codes": ["1001"]}]})
        self.assertFalse(value["data"]["items"][0]["ok"])
        self.assertIn("快照", value["data"]["items"][0]["error"])

    def test_saved_tb_cannot_be_changed_within_the_same_run(self):
        self.call("scan_template")
        before = [(item.selected, list(item.sources)) for item in self.ctx.results]
        self.ctx.tb_exported = True
        args = {"items": [{"target_id": self.ctx.results[0].target.id, "source_codes": ["1002"]}]}
        self.assertFalse(self.call("stage_tb_sources", args)["ok"])
        self.assertFalse(self.call("run_tb_ai_fill")["ok"])
        self.assertEqual([(item.selected, item.sources) for item in self.ctx.results], before)

    def test_export_tb_copy_requires_new_path_and_records_artifact(self):
        self.call("scan_template")
        output = self.root / "out" / "tb.xlsx"
        output.parent.mkdir()

        def fake_write(scan, results, path, **kwargs):
            Path(path).write_bytes(b"synthetic")
            return Path(path)

        with patch.object(agent_tools.workbook, "write_results", side_effect=fake_write), \
                patch.object(agent_tools.workbook, "tb_balance_checks", return_value=[]), \
                patch.object(agent_tools.workbook, "export_report",
                             side_effect=lambda path, *args, **kwargs: Path(path).write_text("pending")):
            value = self.call("export_tb_copy", {}, path=str(output))
        self.assertTrue(value["ok"], value)
        self.assertTrue(output.exists())
        self.assertTrue(any(item["kind"] == "tb" for item in self.ctx.artifacts))
        self.assertTrue(self.ctx.tb_exported)

    def test_export_tb_copy_refuses_original_template(self):
        self.call("scan_template")
        value = self.call("export_tb_copy", {}, path=str(self.template))
        self.assertFalse(value["ok"])

    def test_list_report_projects_maps_money_funds(self):
        value = self.call("list_report_projects")
        self.assertTrue(value["ok"])
        indexes = [item["index_no"] for item in value["data"]["projects"]]
        self.assertIn("1-010", indexes)

    def test_company_overview_uses_independent_period_amounts_and_preserves_missing_values(self):
        figures = [
            LedgerRow("6001", "主营业务收入", direction="贷", is_profit_loss=True,
                      ending=Decimal("0"), credit=Decimal("5000"), period_amount=Decimal("900"),
                      previous_period_amount=Decimal("750")),
            LedgerRow("6401", "主营业务成本", is_profit_loss=True,
                      ending=Decimal("0"), debit=Decimal("300")),
        ]
        summary = agent_tools.company_overview(figures, ())
        reports = {item["index_no"]: item for item in summary["projects"]}
        self.assertEqual(reports["4-010"]["period"], "900")
        self.assertEqual(reports["4-010"]["change"], "150")
        self.assertIsNone(reports["5-010"]["period"])
        self.assertIsNone(reports["5-010"]["change"])

    def test_search_knowledge_returns_citation_ids(self):
        value = self.call("search_knowledge", {"query": "收入确认 控制权转移", "limit": 3})
        self.assertTrue(value["ok"])
        refs = value["data"]["references"]
        self.assertTrue(refs)
        self.assertTrue(all(item["citation_id"].startswith("K") for item in refs))

    def test_finish_schema_present(self):
        self.assertIn("finish", self.registry.specs)
        self.assertIn("ask_user", self.registry.specs)

    def test_export_workpaper_writes_real_xlsx_for_report_project(self):
        paper = audit_index.find(self.ctx.paper_library, "1-010")
        self.assertIsNotNone(paper)
        self.ctx.prepared_workpapers["1-010"] = {
            "paper": paper,
            "data": PaperData(rows=tuple(self.ctx.rows), journal=JournalRows([])),
            "draft": {"kind": "local"},
            "warnings": [],
        }
        self.ctx.add_gap(agent_tools.EVIDENCE_READ_GAP)
        output = self.root / "out" / "money-fund.xlsx"
        value = agent_tools.export_workpaper(self.ctx, {"index_no": "1-010"},
                                             runtime(str(output)))
        self.assertTrue(value["ok"], value)
        self.assertTrue(output.is_file())
        book = load_workbook(output, read_only=True, data_only=True)
        try:
            contents = "\n".join(str(cell) for sheet in book.worksheets
                                 for row in sheet.iter_rows(values_only=True) for cell in row if cell)
        finally:
            book.close()
        self.assertIn("库存现金", contents)
        self.assertIn("1-010", contents)
        self.assertIn(agent_tools.EVIDENCE_READ_GAP, contents)

    def test_stage_registry_can_exclude_paper_tools(self):
        stage = agent_tools.build_registry(include_papers=False)
        self.assertNotIn("prepare_workpaper", stage.specs)
        self.assertIn("export_tb_copy", stage.specs)

    def test_tb_approval_details_include_selected_and_pending(self):
        self.call("scan_template")
        details = dict(agent_tools._tb_approval_details(self.ctx, {}))
        self.assertIn("将写入字段", details)
        self.assertIn("待处理字段", details)

    def test_tb_approval_details_scan_before_counting(self):
        # 未预先扫描时，确认信息也必须等于实际扫描后的字段数，而不是 0。
        fresh = context(self.template)
        details = dict(agent_tools._tb_approval_details(fresh, {}))
        self.assertNotIn("0 个；允许覆盖", details["将写入字段"])
        self.assertEqual(len(fresh.results), 2)

    def test_workpaper_export_rechecks_source_snapshot(self):
        paper = audit_index.find(self.ctx.paper_library, "1-010")
        data = PaperData(rows=tuple(self.ctx.rows), journal=JournalRows([]),
                         source_path=str(self.root / "source"))
        self.ctx.prepared_workpapers["1-010"] = {
            "paper": paper, "data": data, "draft": {"kind": "local"}, "warnings": [],
        }
        output = self.root / "out" / "stale.xlsx"
        with patch.object(agent_tools.dxn, "validate_paper_data_source", return_value=False):
            value = agent_tools.export_workpaper(self.ctx, {"index_no": "1-010"},
                                                 runtime(str(output)))
        self.assertFalse(value["ok"])
        self.assertFalse(output.exists())
        self.assertTrue(any("一致副本" in gap for gap in self.ctx.gaps))


if __name__ == "__main__":
    unittest.main()
