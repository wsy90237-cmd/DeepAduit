import json
import os
import shutil
import time
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import Workbook

from tb_autofill import agent_tools, matching
from tb_autofill.agent import AgentRuntime, ApprovalDecision, ProjectSnapshot
from tb_autofill.models import JournalRow, JournalRows, LedgerRow, PaperData, Subject


def make_template(path):
    book = Workbook()
    sheet = book.active
    sheet.title = "期末"
    sheet.append(["科目名称", "期末余额"])
    sheet.append(["库存现金", None])
    sheet.append(["待确认科目", None])
    book.save(path)
    book.close()


def rows():
    return [
        LedgerRow("1001", "库存现金", path="库存现金", opening=Decimal("50"),
                  debit=Decimal("60"), credit=Decimal("10"), ending=Decimal("100"),
                  subject_id="S1", year=2026, month=12, ledger_id="L1"),
        LedgerRow("1002", "银行存款", path="银行存款", opening=Decimal("200"),
                  debit=Decimal("30"), credit=Decimal("20"), ending=Decimal("210"),
                  subject_id="S1", year=2026, month=12, ledger_id="L1"),
    ]


def tool_call(name, arguments=None, call_id="call-1"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False)}}


def reply(calls=()):
    normalized = [{"id": raw["id"], "name": raw["function"]["name"],
                   "arguments": json.loads(raw["function"]["arguments"] or "{}"), "raw": raw}
                  for raw in calls]
    return {"content": "", "tool_calls": normalized,
            "finish_reason": "tool_calls" if calls else "stop"}


class ScenarioClient:
    model = "deepseek-flash"
    base_url = "https://api.deepseek.com"

    def __init__(self, replies):
        self.replies = list(replies)
        self.messages = []

    def chat_tools(self, messages, tools):
        self.messages.append([dict(item) for item in messages])
        return self.replies.pop(0)

    def paper_text(self, request):
        return {"intent": "audit", "answer": "{}"}


class AgentScenarioTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / "build"
        base.mkdir(parents=True, exist_ok=True)
        self.root = base / f"test-agent-scenario-{os.getpid()}-{time.time_ns()}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.template = self.root / "template.xlsx"
        make_template(self.template)
        self.original = self.template.read_bytes()
        subject = Subject("S1", "合成企业", 2026, 12, str(self.root), ledger_id="L1")
        self.snapshot = ProjectSnapshot(
            subject=subject, rows=tuple(rows()), subject_label="合成企业 / 2026-12",
            template_path=str(self.template), unit="元", periods=("ending",), scan=None,
            results=(), paper_library=(), app_revision=1, ledger_digest="ledger",
            template_digest="template")

    def test_tb_and_money_fund_workpaper_end_to_end(self):
        out = self.root / "out"
        context = self.snapshot.context(
            output_dir=str(out), objective="生成本期 TB 与货币资金整册底稿",
            stages=("tb", "papers"), report_indexes=("1-010",),
            allowed_scope={"ledger", "tb", "journal", "workpaper", "knowledge"},
            current_fingerprint=lambda: self.snapshot.revision())
        client = ScenarioClient([
            reply([tool_call("scan_template")]),
            reply([tool_call("export_tb_copy")]),
            reply([tool_call("list_report_projects")]),
            reply([tool_call("prepare_workpaper", {"index_no": "1-010"})]),
            reply([tool_call("export_workpaper", {"index_no": "1-010"})]),
            reply([tool_call("finish", {"status": "done", "summary": "已生成 TB 与货币资金底稿"})]),
        ])
        paths = {"tb": self.root / "out" / "tb.xlsx",
                 "paper": self.root / "out" / "paper.xlsx"}

        def approve(request):
            if "导出底稿" in request.summary:
                return ApprovalDecision(True, path=str(paths["paper"]))
            return ApprovalDecision(True, path=str(paths["tb"]))

        def fake_write(scan, results, path, **kwargs):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic-tb")
            return path

        def fake_paper(path, **kwargs):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic-paper")
            return path

        paper_data = PaperData(rows=tuple(rows()), journal=JournalRows([
            JournalRow(12, "2026-12-31", "记-1", "1", "收到货款", "1001",
                       debit=Decimal("10"), credit=None)]))

        with patch.object(agent_tools.workbook, "write_results", side_effect=fake_write), \
                patch.object(agent_tools.workbook, "tb_balance_checks", return_value=[]), \
                patch.object(agent_tools.workbook, "export_report",
                             side_effect=lambda path, *args, **kwargs: Path(path).write_text("pending")), \
                patch.object(agent_tools.dxn, "read_paper_data", return_value=paper_data), \
                patch.object(agent_tools.papers, "pack_result", return_value={"kind": "local"}), \
                patch.object(agent_tools.dxn_paper, "build", side_effect=fake_paper):
            runtime = AgentRuntime(client, agent_tools.build_registry(), context,
                                   approve=approve, audit=lambda *_: None)
            result = runtime.run()

        self.assertEqual(result.status, "done", result.summary + " / " + str(result.gaps))
        kinds = {item["kind"] for item in result.artifacts}
        self.assertEqual(kinds, {"tb", "tb_pending", "workpaper"})
        self.assertEqual(self.template.read_bytes(), self.original)
        self.assertTrue(any("银行回函" in gap or "对账单" in gap for gap in result.gaps))
        self.assertTrue(paths["tb"].is_file())
        self.assertTrue(paths["paper"].is_file())

    def test_source_change_between_scan_and_write_blocks_export(self):
        from tb_autofill.agent import ToolSpec
        state = {"changed": False}

        def current():
            return ("changed",) if state["changed"] else self.snapshot.revision()

        def flip(context, arguments, runtime):
            state["changed"] = True
            return {"ok": True, "summary": "模拟模板在确认前变化"}

        context = self.snapshot.context(
            output_dir=str(self.root / "out"), stages=("tb",), allowed_scope={"ledger", "tb"},
            current_fingerprint=current)
        registry = agent_tools.build_registry(include_papers=False)
        registry.specs["flip"] = ToolSpec("flip", "模拟来源变化", {"type": "object",
            "properties": {}, "additionalProperties": False}, "read", flip, lambda _args: "模拟变化")
        client = ScenarioClient([
            reply([tool_call("scan_template")]),
            reply([tool_call("flip")]),
            reply([tool_call("export_tb_copy")]),
            reply([tool_call("finish", {"status": "done", "summary": "完成"})]),
        ])
        runtime = AgentRuntime(client, registry, context, audit=lambda *_: None)
        result = runtime.run()
        self.assertEqual(result.status, "stale")
        self.assertFalse((self.root / "out" / "tb.xlsx").exists())


if __name__ == "__main__":
    unittest.main()
