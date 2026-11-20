"""附注/报告草稿与 DOCX 导出：只用合成账套、不联网、不读写真实项目资料。"""

from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from tb_autofill import reporting
from tb_autofill.models import LedgerRow, MatchResult, TargetField, ledger_digest

SUBJECT = "合成测试公司 / 2026-12"
WORKSPACE_CACHE = Path(__file__).resolve().parent.parent / ".cache"
REFS = [{"citation_id": f"K{index}", "title": f"合成参考资料 {index}",
         "source": "内置知识库/合成测试", "excerpt": "合成参考正文"}
        for index in range(1, 8)]


def row(code, name, *, level=1, path="", direction="借", opening=None, ending=None,
        debit=None, credit=None, period_amount=None, previous_period_amount=None,
        is_profit_loss=False):
    def amount(value):
        return None if value is None else Decimal(value)

    return LedgerRow(code=code, name=name, path=path or name, direction=direction,
                     level=level, subject_id="S1", year=2026, month=12,
                     opening=amount(opening), ending=amount(ending),
                     debit=amount(debit), credit=amount(credit),
                     is_profit_loss=is_profit_loss,
                     period_amount=amount(period_amount),
                     previous_period_amount=amount(previous_period_amount))


def rows():
    return [
        row("1001", "库存现金", opening="1000", ending="1200", debit="500", credit="300"),
        row("1002", "银行存款", opening="50000", ending="60000", debit="20000", credit="10000"),
        row("100201", "工商银行", level=2, path="银行存款/工商银行", opening="50000",
            ending="60000", debit="20000", credit="10000"),
        row("6602", "管理费用", is_profit_loss=True, period_amount="1234.56",
            previous_period_amount="1000"),
    ]


def target(label, cell, period="ending", value=None, is_formula=False):
    return TargetField(sheet="期末", cell=cell, label=label, period=period, value=value,
                       is_formula=is_formula)


def results():
    return [
        MatchResult(target=target("库存现金", "D10", value=Decimal("1000")),
                    amount=Decimal("1200"), status="matched"),
        MatchResult(target=target("银行存款", "D11"), amount=Decimal("60000"),
                    status="matched"),
        MatchResult(target=target("管理费用", "D12", period="period"), status="unmatched"),
        MatchResult(target=target("合计", "D13", value="=SUM(D10:D11)", is_formula=True),
                    status="protected"),
    ]


def scan():
    return SimpleNamespace(unit="万元", source_digest="fingerprint-synthetic",
                           digest="template-synthetic", fields=(1, 2, 3, 4, 5))


def facts(**kwargs):
    values = dict(subject_label=SUBJECT, rows=rows(), results=results(), scan=scan(),
                  workpapers=({"name": "货币资金审定表", "status": "已完成"},))
    values.update(kwargs)
    return reporting.build_facts(**values)


class BuildFactsTests(unittest.TestCase):
    def test_tb_counts_unit_and_fingerprint(self):
        value = facts()
        self.assertEqual(value.entity, "合成测试公司")
        self.assertEqual((value.year, value.month), (2026, 12))
        self.assertEqual(value.tb["fields"], 5)
        self.assertEqual(value.tb["matched"], 2)
        self.assertEqual(value.tb["pending"], 1)
        self.assertEqual(value.tb["protected"], 1)
        self.assertEqual(value.tb["differences"], 1)
        self.assertEqual(value.tb["unit"], "万元")
        self.assertEqual(value.tb["rows"], 4)
        self.assertEqual(value.tb["accounts"], 4)
        self.assertEqual(value.source_fingerprint, "fingerprint-synthetic")

    def test_projects_aggregate_local_amounts(self):
        value = facts()
        self.assertEqual([project["index_no"] for project in value.projects],
                         ["1-010", "5-110"])
        money = {project["index_no"]: project["balance"] for project in value.projects}
        self.assertEqual(money["1-010"]["opening"], "51,000")
        self.assertEqual(money["1-010"]["ending"], "61,200")
        self.assertEqual(money["1-010"]["debit"], "20,500")
        self.assertEqual(money["1-010"]["credit"], "10,300")
        self.assertEqual(money["5-110"]["period"], "1,234.56")
        self.assertEqual(money["5-110"]["previous_period"], "1,000")
        self.assertNotIn("ending", money["5-110"])
        currency = next(project for project in value.projects if project["index_no"] == "1-010")
        self.assertEqual([account["code"] for account in currency["accounts"]],
                         ["1001", "1002", "100201"])
        self.assertTrue(value.projects[1]["is_profit_loss"])

    def test_notes_tables_carry_subject_detail(self):
        value = facts()
        self.assertEqual(len(value.notes_tables), 2)
        table = value.notes_tables[0]
        self.assertEqual(table["columns"][-2:], ["期初余额", "期末余额"])
        self.assertIn(["1001", "库存现金", "1", "1,000", "1,200"], table["rows"])
        self.assertEqual(value.notes_tables[1]["columns"][-2:], ["本期发生额", "上期发生额"])

    def test_workpapers_accept_dict_tuple_and_empty(self):
        self.assertEqual(facts(workpapers=()).workpapers, ())
        mapping = facts(workpapers={"货币资金审定表": {"status": "已完成"}})
        self.assertEqual(mapping.workpapers[0]["name"], "货币资金审定表")
        self.assertEqual(mapping.workpapers[0]["status"], "已完成")
        self.assertEqual(facts(workpapers=("编制完成",)).workpapers[0]["name"], "编制完成")

    def test_source_fingerprint_falls_back_to_ledger_digest(self):
        value = facts(scan=None)
        self.assertEqual(value.tb["unit"], "元")
        self.assertEqual(value.source_fingerprint, ledger_digest(rows()))

    def test_readiness_override_is_kept(self):
        override = {"checks": [{"item": "合成检查", "status": "待定", "detail": "合成"}],
                    "ready_to_issue": False}
        value = facts(readiness=override)
        self.assertEqual(value.readiness, override)
        self.assertEqual(reporting.readiness(value)["workflow_checks"], override["checks"])

    def test_empty_rows_do_not_fabricate_projects(self):
        value = reporting.build_facts(subject_label="", rows=[], results=[])
        self.assertEqual(value.projects, ())
        self.assertEqual(value.tb["fields"], 0)
        self.assertEqual(value.source_fingerprint, "")
        state = reporting.readiness(value)
        self.assertIn("尚未导入科目余额或 TB 字段", state["blocking_gaps"])
        self.assertFalse(state["can_draft_report"])


class RequestTests(unittest.TestCase):
    def test_notes_request_has_expected_shape_and_bounded_refs(self):
        request = reporting.notes_request(facts(), refs=REFS)
        self.assertEqual(set(request), {"system", "context", "question"})
        self.assertLessEqual(len(request["context"]["knowledge_refs"]), 5)
        self.assertEqual(request["context"]["knowledge_refs"][0]["citation_id"], "K1")
        self.assertIn("knowledge_refs", json.dumps(request["context"], ensure_ascii=False))
        self.assertIn("待补", request["question"])
        self.assertIn("K1", request["question"])
        self.assertIn("facts", request["context"])
        dumped = json.dumps(request, ensure_ascii=False)
        self.assertNotIn("sk-", dumped)
        self.assertNotIn("api_key", dumped)

    def test_notes_request_default_search_is_local_and_bounded(self):
        request = reporting.notes_request(facts())
        self.assertLessEqual(len(request["context"]["knowledge_refs"]), 5)
        self.assertIn("知识", request["question"])

    def test_report_request_locks_opinion_and_kam(self):
        request = reporting.report_request(facts(), refs=REFS)
        self.assertIn(reporting.OPINION_PLACEHOLDER, request["system"])
        self.assertIn(reporting.OPINION_PLACEHOLDER, request["question"])
        self.assertIn("候选", request["question"])
        constraints = request["context"]["constraints"]
        self.assertEqual(constraints["opinion_type"], reporting.OPINION_PLACEHOLDER)
        self.assertFalse(constraints["ready_to_issue"])
        self.assertIn("签发", request["system"])
        self.assertIn("不得", request["system"])
        self.assertNotIn("sk-", json.dumps(request, ensure_ascii=False))

    def test_empty_facts_request_asks_for_pending(self):
        empty = reporting.build_facts(subject_label="", rows=[], results=[])
        request = reporting.notes_request(empty)
        self.assertIn("待补", request["question"])
        self.assertEqual(request["context"]["facts"]["projects"], [])
        self.assertIn("不得编造", request["question"])


class ParseNotesTests(unittest.TestCase):
    def setUp(self):
        self.facts = facts()
        self.request = reporting.notes_request(self.facts, refs=REFS)

    def payload(self):
        return {
            "sections": [
                {"title": "货币资金",
                 "items": ["期末货币资金余额为 61,200 元，其中银行存款 60,000 元。",
                           "合计口径参见[K1]的披露要求。"]},
                {"title": "管理费用", "items": ["本期管理费用发生额 1,234.56 元。"]},
            ],
            "tables": [{"title": "货币资金明细",
                        "columns": ["科目编码", "科目名称", "期末余额"],
                        "rows": [["1001", "库存现金", "1,200"],
                                 ["1002", "银行存款", "60,000"]]}],
        }

    def test_valid_draft_accepted_with_citations(self):
        result = reporting.parse_notes({"answer": json.dumps(self.payload(), ensure_ascii=False)},
                                       self.request)
        self.assertEqual(result["kind"], "notes")
        self.assertEqual(len(result["body_sections"]), 2)
        self.assertEqual(len(result["tables"]), 1)
        self.assertEqual(result["warnings"], [])
        self.assertEqual([item["citation_id"] for item in result["refs"]], ["K1"])
        self.assertIn("[K1]", result["body_sections"][0]["paragraphs"][1])

    def test_content_field_is_tolerated(self):
        result = reporting.parse_notes({"content": json.dumps(self.payload(), ensure_ascii=False)},
                                       self.request)
        self.assertEqual(result["kind_label"], "财务报表附注草稿")

    def test_fabricated_amount_sentence_is_replaced(self):
        payload = {"sections": [{"title": "货币资金",
                                 "items": ["期末余额为 9,888,888.88 元。"]}]}
        result = reporting.parse_notes(json.dumps(payload, ensure_ascii=False), self.request)
        paragraph = result["body_sections"][0]["paragraphs"][0]
        self.assertEqual(paragraph, reporting.AMOUNT_REJECT)
        self.assertTrue(any("9,888,888.88" in warning for warning in result["warnings"]))

    def test_unknown_citation_sentence_is_replaced(self):
        payload = {"sections": [{"title": "货币资金",
                                 "items": ["依据[K9]列示披露。"]}]}
        result = reporting.parse_notes(json.dumps(payload, ensure_ascii=False), self.request)
        self.assertEqual(result["body_sections"][0]["paragraphs"][0],
                         reporting.CITATION_REJECT)
        self.assertTrue(any("K9" in warning for warning in result["warnings"]))
        self.assertEqual(result["refs"], [])

    def test_table_cells_are_number_checked(self):
        payload = {"tables": [{"title": "合成明细",
                               "columns": ["科目编码", "期末余额"],
                               "rows": [["1001", "77,777.77"]]}]}
        result = reporting.parse_notes(json.dumps(payload, ensure_ascii=False), self.request)
        self.assertEqual(result["tables"][0]["rows"][0][1], reporting.AMOUNT_REJECT)
        self.assertTrue(any("77,777.77" in warning for warning in result["warnings"]))

    def test_malformed_structure_is_rejected(self):
        with self.assertRaises(ValueError):
            reporting.parse_notes(json.dumps({"sections": "不是数组"}), self.request)
        with self.assertRaises(ValueError):
            reporting.parse_notes(json.dumps({"other": []}), self.request)
        with self.assertRaises(ValueError):
            reporting.parse_notes({"answer": "不是 JSON"}, self.request)

    def test_missing_amount_index_blocks_parse(self):
        request = {"context": {"facts": {"projects": []}, "knowledge_refs": []}}
        with self.assertRaises(ValueError):
            reporting.parse_notes(json.dumps({"sections": [{"title": "待补",
                                                            "items": ["待补。"]}]}), request)


class ParseReportTests(unittest.TestCase):
    def setUp(self):
        self.facts = facts()
        self.request = reporting.report_request(self.facts, refs=REFS)

    def test_opinion_is_fixed_and_limitations_are_kept(self):
        payload = {
            "opinion": "无保留意见",
            "sections": [{"title": "形成审计意见的基础",
                          "items": ["我们已执行全部审计程序，认为财务报表在所有重大方面公允反映。",
                                    "本期货币资金余额 61,200 元。"]}],
            "kam": [{"matter": "收入确认", "reason": "涉及管理层判断，列为候选事项。"}],
            "limitations": ["尚未取得银行函证回函。"],
        }
        result = reporting.parse_report(json.dumps(payload, ensure_ascii=False), self.request)
        self.assertEqual(result["kind"], "report")
        self.assertEqual(result["opinion"], reporting.OPINION_PLACEHOLDER)
        titles = [section["title"] for section in result["body_sections"]]
        self.assertIn("意见类型", titles)
        self.assertIn("限制与未完成事项", titles)
        text = "\n".join(paragraph for section in result["body_sections"]
                         for paragraph in section["paragraphs"])
        self.assertIn(reporting.OPINION_PLACEHOLDER, text)
        self.assertNotIn("无保留意见", text)
        self.assertIn("（该句涉及意见类型或结论，需项目组确定，未采纳。）", text)
        self.assertEqual(len(result["kam"]), 1)
        self.assertTrue(any("模型自拟的意见类型未采纳" in warning
                            for warning in result["warnings"]))
        limitations = next(section["paragraphs"] for section in result["body_sections"]
                           if section["title"] == "限制与未完成事项")
        self.assertIn("尚未取得银行函证回函。", limitations)
        self.assertTrue(any("附注草稿尚未生成" in item for item in limitations))

    def test_unverified_report_amount_is_rejected(self):
        payload = {"sections": [{"title": "关键财务数据",
                                 "items": ["资产总额为 123,456,789.00 元。"]}]}
        result = reporting.parse_report(json.dumps(payload, ensure_ascii=False), self.request)
        text = "\n".join(paragraph for section in result["body_sections"]
                         for paragraph in section["paragraphs"])
        self.assertIn(reporting.AMOUNT_REJECT, text)
        self.assertTrue(any("123,456,789.00" in warning for warning in result["warnings"]))

    def test_invalid_kam_and_limitation_shape_are_dropped(self):
        payload = {"sections": [], "kam": ["非对象"], "limitations": {"x": "y"}}
        result = reporting.parse_report(json.dumps(payload, ensure_ascii=False), self.request)
        self.assertEqual(result["kam"], [])
        self.assertTrue(any("关键审计事项候选第 1 项结构不合法" in warning
                            for warning in result["warnings"]))


class ReadinessTests(unittest.TestCase):
    def test_ready_to_issue_is_always_false(self):
        state = reporting.readiness(facts())
        self.assertIs(state["ready_to_issue"], False)
        self.assertFalse(state["can_draft_report"])
        gaps = "\n".join(state["blocking_gaps"])
        self.assertIn("TB 尚有 1 个字段未确认", gaps)
        self.assertIn("TB 有 1 个已填值差异待复核", gaps)
        self.assertIn("附注草稿尚未生成", gaps)
        self.assertIn("未提供重要性水平", gaps)

    def test_non_december_and_missing_workpapers_are_gaps(self):
        september = [LedgerRow(code="1001", name="库存现金", path="库存现金", level=1,
                               subject_id="S1", year=2026, month=9, opening=Decimal("1"),
                               ending=Decimal("1"), debit=Decimal("0"), credit=Decimal("0"))]
        value = reporting.build_facts(subject_label="合成测试公司 / 2026-09",
                                      rows=september, results=[])
        gaps = "\n".join(reporting.readiness(value)["blocking_gaps"])
        self.assertIn("非 12 月", gaps)
        self.assertIn("尚未取得审计底稿", gaps)

    def test_complete_inputs_can_draft_but_never_issue(self):
        clean_rows = [row("1001", "库存现金", opening="1000", ending="1200", debit="500",
                          credit="300"),
                      row("1002", "银行存款", opening="50000", ending="60000",
                          debit="20000", credit="10000")]
        clean_results = [
            MatchResult(target=target("库存现金", "D10"), amount=Decimal("1200"),
                        status="matched"),
            MatchResult(target=target("银行存款", "D11"), amount=Decimal("60000"),
                        status="matched"),
        ]
        value = reporting.build_facts(subject_label=SUBJECT, rows=clean_rows,
                                      results=clean_results,
                                      workpapers=({"name": "货币资金审定表"},),
                                      materiality="100000")
        self.assertEqual(value.gaps, ())
        state = reporting.readiness(value)
        self.assertIn("附注草稿尚未生成", "\n".join(state["blocking_gaps"]))
        self.assertFalse(state["can_draft_report"])
        ready = replace(value, notes_draft={"body_sections": [{"title": "货币资金",
                                                               "paragraphs": ["待补"]}]})
        state = reporting.readiness(ready)
        self.assertTrue(state["can_draft_report"])
        self.assertIs(state["ready_to_issue"], False)


class DocxExportTests(unittest.TestCase):
    def setUp(self):
        WORKSPACE_CACHE.mkdir(exist_ok=True)
        self.folder = WORKSPACE_CACHE / f"test-reporting-{os.getpid()}-{time.time_ns()}"
        self.folder.mkdir()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.facts = facts()
        request = reporting.notes_request(self.facts, refs=REFS)
        self.notes = reporting.parse_notes(json.dumps({
            "sections": [{"title": "货币资金",
                          "items": ["期末货币资金余额为 61,200 元。"]}]},
            ensure_ascii=False), request)
        report_request = reporting.report_request(self.facts, refs=REFS)
        self.report = reporting.parse_report(json.dumps({
            "sections": [{"title": "形成审计意见的基础", "items": ["货币资金余额 61,200 元。"]}],
            "kam": [{"matter": "货币资金", "reason": "列为候选事项。"}],
            "limitations": ["尚未取得银行函证回函。"]}, ensure_ascii=False), report_request)

    def test_notes_docx_has_tables_and_unissued_footer(self):
        from docx import Document
        target = self.folder / "notes.docx"
        result = reporting.export_docx(target, self.notes, self.facts, "notes")
        self.assertEqual(result, target)
        document = Document(target)
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("财务报表附注草稿", text)
        self.assertIn("61,200", text)
        self.assertIn("未签发", text)
        self.assertGreaterEqual(len(document.tables), 1)
        cells = [cell.text for table in document.tables for row in table.rows for cell in row.cells]
        self.assertIn("库存现金", cells)
        footer = "\n".join(paragraph.text for section in document.sections
                           for paragraph in section.footer.paragraphs)
        self.assertIn(reporting.FOOTER_TEXT, footer)
        self.assertEqual(sorted(path.name for path in self.folder.glob("notes*")),
                         ["notes.docx"])

    def test_report_docx_has_opinion_placeholder_and_limitations(self):
        from docx import Document
        target = self.folder / "report.docx"
        reporting.export_docx(target, self.report, self.facts, "report")
        document = Document(target)
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn(reporting.OPINION_PLACEHOLDER, text)
        self.assertIn("限制与未完成事项", text)
        self.assertIn("未签发", text)
        footer = "\n".join(paragraph.text for section in document.sections
                           for paragraph in section.footer.paragraphs)
        self.assertIn(reporting.FOOTER_TEXT, footer)

    def test_existing_target_is_not_overwritten(self):
        target = self.folder / "notes.docx"
        target.write_text("原有内容", encoding="utf-8")
        with self.assertRaises(ValueError):
            reporting.export_docx(target, self.notes, self.facts, "notes")
        self.assertEqual(target.read_text(encoding="utf-8"), "原有内容")

    def test_suffix_is_validated(self):
        with self.assertRaises(ValueError):
            reporting.export_docx(self.folder / "notes.html", self.notes, self.facts, "notes")
        with self.assertRaises(ValueError):
            reporting.export_docx(self.folder / "x.docx", self.notes, self.facts, "unknown")

    def test_missing_python_docx_raises_readable_value_error(self):
        with mock.patch.dict(sys.modules, {"docx": None}):
            with self.assertRaises(ValueError) as caught:
                reporting.export_docx(self.folder / "fallback.docx", self.notes,
                                      self.facts, "notes")
        self.assertIn("python-docx", str(caught.exception))

    def test_html_export_is_available_fallback(self):
        target = self.folder / "notes.html"
        reporting.export_html(target, self.notes, self.facts, "notes")
        page = target.read_text(encoding="utf-8")
        self.assertIn(reporting.FOOTER_TEXT, page)
        self.assertIn("库存现金", page)


if __name__ == "__main__":
    unittest.main()
