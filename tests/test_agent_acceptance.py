"""独立验收：合成 TB 模板经真实 Excel 写入，以及 1-010 底稿「证据核对与风险」页写回。

约束（见 AGENTS.md）：只用合成数据、不联网、不读真实账套、不改产品代码。
临时目录一律建在工作区 ``build/`` 下并用 ``Path.mkdir`` + ``addCleanup`` 清理，
不使用 ``tempfile.TemporaryDirectory``（DSH 沙箱会拒绝其子目录写入）。

真实 TB 写入依赖本机 Microsoft Excel + pywin32：
- 可用时走 ``agent_tools.export_tb_copy`` 的真实调用路径（内部 ``workbook.write_results``、
  ``tb_balance_checks``、``export_report``），并用 openpyxl 回读单元格；
- 不可用时该用例 ``skipTest`` 并写明“未执行/原因”，另由 mock 用例只验证编排，
  不把 mock 结果当作真实重算。
"""

import hashlib
import os
import shutil
import time
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

from tb_autofill import agent_tools, audit_index, fields, matching, workbook
from tb_autofill.agent import ProjectSnapshot
from tb_autofill.models import LedgerRow, PaperData, Subject, ledger_digest, money

ROOT = Path(__file__).resolve().parents[1]
END_SHEET = "期末"
EVIDENCE_SHEET = "证据核对与风险"
EVIDENCE_REF = "EV-SYN-0001"
# 产品提示原文：“货币资金未关联已确认的银行回函/对账单，底稿证据页需项目组补充。”
GAP_WARNING = "未关联已确认"
GAP_DETAIL = "银行回函/对账单"

# Excel COM 探测结果在进程内缓存，避免每个用例都启动一次 Excel。
_EXCEL_PROBE = None


def _sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _console_safe(text, limit=400):
    """控制台编码可能是 GBK；跳过原因里不能带入不可编码字符导致报告失败。"""
    text = " ".join(str(text).split())
    return text.encode("gbk", errors="replace").decode("gbk")[:limit]


def _probe_excel():
    """真实尝试一次 Excel COM 新建工作簿；返回 ``(可用, 说明)``。"""
    try:
        import pythoncom
        import win32com.client
    except Exception as exc:  # pywin32 缺失
        return False, f"pywin32 未安装（{_console_safe(exc)}）"
    excel = book = None
    initialized = False
    try:
        pythoncom.CoInitialize()
        initialized = True
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        book = excel.Workbooks.Add()
        version = _console_safe(excel.Version, 40)
        book.Close(SaveChanges=False)
        book = None
        return True, f"Microsoft Excel {version}"
    except Exception as exc:
        return False, f"Excel COM 无法新建/打开工作簿（{_console_safe(exc)}）"
    finally:
        if book is not None:
            try:
                book.Close(SaveChanges=False)
            except Exception:
                pass
        if excel is not None:
            try:
                excel.Quit()
            except Exception:
                pass
        if initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def excel_probe():
    global _EXCEL_PROBE
    if _EXCEL_PROBE is None:
        _EXCEL_PROBE = _probe_excel()
    return _EXCEL_PROBE


def synthetic_template(path):
    """合成 TB 模板：表头「科目名称 / 期末余额」，两行科目，金额留空。"""
    book = Workbook()
    sheet = book.active
    sheet.title = END_SHEET
    sheet.append(["科目名称", "期末余额"])
    sheet.append(["库存现金", None])
    sheet.append(["银行存款", None])
    book.save(path)
    book.close()
    return Path(path)


def ledger_rows():
    """合成科目余额：两行与模板对应，第三行制造“未确认完整入表”的待处理缺口。"""
    return [
        LedgerRow("1001", "库存现金", path="库存现金", opening=Decimal("50"),
                  debit=Decimal("60"), credit=Decimal("10"), ending=Decimal("100"),
                  subject_id="S1", year=2026, month=12, ledger_id="L1"),
        LedgerRow("1002", "银行存款", path="银行存款", opening=Decimal("200"),
                  debit=Decimal("30"), credit=Decimal("20"), ending=Decimal("210"),
                  subject_id="S1", year=2026, month=12, ledger_id="L1"),
        LedgerRow("1003", "其他货币资金", path="其他货币资金", opening=Decimal("0"),
                  debit=Decimal("50"), credit=Decimal("0"), ending=Decimal("50"),
                  subject_id="S1", year=2026, month=12, ledger_id="L1"),
    ]


def money_fund_rows():
    """合成货币资金：1-010 底稿只取 1001、1002；金额四栏齐全。"""
    return ledger_rows()[:2]


def evidence_row(digest):
    """符合 ``evidence_draft.add_sheet`` 字段结构的一条合成证据。"""
    return {
        "field": "银行存款期末余额",
        "value": "210.00",
        "quote": "截至2026年12月31日，贵公司银行存款余额为210.00元，与银行对账单一致。",
        "risk": "未覆盖全部银行账户，需核对账户清单完整性。",
        "next_step": "取得全部银行账户对账单并与银行回函核对。",
        "comparison": "账面银行存款 210.00 元",
        "filename": "工商银行回函-合成.pdf",
        "version": "v1",
        "page": 1,
        "ref": EVIDENCE_REF,
        "sha256": digest,
        "reviewer": "合成复核人",
    }


class AgentAcceptanceTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "build"
        base.mkdir(parents=True, exist_ok=True)
        self.root = base / f"acceptance-{os.getpid()}-{time.time_ns()}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.template = synthetic_template(self.root / "合成TB模板.xlsx")
        self.template_sha = _sha256(self.template)
        self.subject = Subject("S1", "合成企业", 2026, 12, str(self.root), ledger_id="L1")

    # ---------- 辅助 ----------

    def scan_and_match(self, rows=None):
        rows = list(rows if rows is not None else ledger_rows())
        scan = fields.scan_template(self.template, ("ending",), "元")
        scan.source_digest = ledger_digest(rows)
        results = matching.match_fields(scan.fields, rows)
        return scan, results, rows

    def snapshot(self, *, rows, scan, results):
        return ProjectSnapshot(
            subject=self.subject, rows=tuple(rows), subject_label="合成企业 / 2026-12",
            template_path=str(self.template), unit="元", periods=("ending",),
            scan=scan, results=tuple(results), paper_library=(), app_revision=1,
            ledger_digest="synthetic-ledger", template_digest=self.template_sha)

    def runtime(self, output):
        """只提供工具需要的两个属性；不建模型客户端，不联网。"""
        return SimpleNamespace(client=None, approved_path=str(output))

    def prepare_workpaper_context(self, snapshot, *, evidence=None):
        paper = audit_index.find((), "1-010")
        self.assertIsNotNone(paper, "内置底稿索引缺少 1-010 货币资金")
        context = snapshot.context(
            output_dir=str(self.root / "out"),
            evidence_rows={"1-010": tuple(evidence or ())},
            current_fingerprint=lambda: snapshot.revision())
        context.prepared_workpapers["1-010"] = {
            "paper": paper,
            "data": PaperData(rows=tuple(money_fund_rows())),
            "draft": {"kind": "local"},
            "warnings": [],
        }
        return context

    # ---------- 1. 真实 TB Excel ----------

    def test_real_excel_tb_copy_and_pending_report(self):
        """真实 Excel：export_tb_copy → write_results → 回读单元格、模板不变、待处理清单。"""
        available, detail = excel_probe()
        if not available:
            self.skipTest(f"真实 Excel 写入与重算未执行：{detail}")

        scan, results, rows = self.scan_and_match()
        by_label = {result.target.label: result for result in results}
        self.assertEqual(set(by_label), {"库存现金", "银行存款"})
        self.assertEqual(by_label["库存现金"].amount, Decimal("100"))
        self.assertEqual(by_label["银行存款"].amount, Decimal("210"))
        for result in results:
            self.assertEqual(result.status, "matched", result.reason)
            self.assertTrue(result.selected, result.reason)

        snapshot = self.snapshot(rows=rows, scan=scan, results=results)
        context = snapshot.context(
            output_dir=str(self.root / "out"), objective="验收 TB 副本",
            stages=("tb",), allowed_scope={"ledger", "tb"},
            current_fingerprint=lambda: snapshot.revision())
        output = self.root / "out" / "合成TB-副本.xlsx"
        output.parent.mkdir(parents=True, exist_ok=True)

        value = agent_tools.export_tb_copy(context, {}, self.runtime(output))
        self.assertTrue(value["ok"], value)

        # 模板未被覆盖，写的是另一份副本。
        self.assertTrue(output.is_file())
        self.assertNotEqual(output.resolve(), self.template.resolve())
        self.assertEqual(_sha256(self.template), self.template_sha)

        # openpyxl 回读真实 Excel 写出的金额。
        book = load_workbook(output, data_only=False)
        try:
            sheet = book[END_SHEET]
            self.assertEqual(money(sheet["B2"].value), Decimal("100"))
            self.assertEqual(money(sheet["B3"].value), Decimal("210"))
        finally:
            book.close()
        original = load_workbook(self.template, data_only=False)
        try:
            self.assertIsNone(original[END_SHEET]["B2"].value)
            self.assertIsNone(original[END_SHEET]["B3"].value)
        finally:
            original.close()

        # 待处理清单由 export_report 真实写出，并含本地勾稽发现的未入表科目。
        report = output.with_name(f"{output.stem}-待处理.csv")
        self.assertTrue(report.is_file(), "未生成待处理清单")
        report_text = report.read_text(encoding="utf-8-sig")
        self.assertIn("目标字段", report_text)
        self.assertIn("1003", report_text)
        self.assertIn("未确认完整入表", report_text)
        self.assertEqual({item["kind"] for item in context.artifacts}, {"tb", "tb_pending"})
        self.assertTrue(context.tb_exported)
        self.assertTrue(any("未识别到可核验的合计" in note
                            for note in value["data"]["balance"]),
                        "tb_balance_checks/tb_balance_summary 未按真实路径执行")

        # 再直接调用一次真实 tb_balance_checks/export_report（本模板无合计行，应为空）。
        checks = workbook.tb_balance_checks(self.template, scan)
        self.assertEqual(checks, [])
        own_report = self.root / "验收-待处理.csv"
        workbook.export_report(own_report, results, subject="合成企业 / 2026-12",
                               unit="元", warnings=["验收提示：合成数据"])
        own_text = own_report.read_text(encoding="utf-8-sig")
        self.assertIn("库存现金", own_text)
        self.assertIn("验收提示：合成数据", own_text)
        self.assertEqual(_sha256(self.template), self.template_sha)

    def test_export_tb_copy_orchestration_with_mocked_excel(self):
        """写 Excel 由 mock 代替：只验 export_tb_copy 编排、清单与模板不变。

        本用例不证明真实 Excel 重算；真实写入是否执行见
        ``test_real_excel_tb_copy_and_pending_report`` 与 ``tools/qa/agent_excel_probe.py``。
        """
        snapshot = self.snapshot(rows=ledger_rows(), scan=None, results=())
        context = snapshot.context(
            output_dir=str(self.root / "out"), objective="验收 TB 编排", stages=("tb",),
            allowed_scope={"ledger", "tb"},
            current_fingerprint=lambda: snapshot.revision())
        output = self.root / "out" / "合成TB-编排.xlsx"
        output.parent.mkdir(parents=True, exist_ok=True)

        def fake_write(scan, results, path, **kwargs):
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.template, path)   # 只模拟“生成了副本”，不重算
            return path

        with patch.object(agent_tools.workbook, "write_results", side_effect=fake_write):
            value = agent_tools.export_tb_copy(context, {}, self.runtime(output))

        self.assertTrue(value["ok"], value)
        self.assertTrue(output.is_file())
        self.assertEqual(_sha256(self.template), self.template_sha)
        self.assertEqual(len(context.results), 2)
        self.assertTrue(all(item.status == "matched" for item in context.results))
        report = output.with_name(f"{output.stem}-待处理.csv")
        self.assertTrue(report.is_file())
        self.assertIn("1003", report.read_text(encoding="utf-8-sig"))
        self.assertEqual({item["kind"] for item in context.artifacts}, {"tb", "tb_pending"})

    # ---------- 2. 底稿证据页 ----------

    def test_workpaper_evidence_sheet_really_written(self):
        """1-010：含已确认证据时，工作簿真实写入「证据核对与风险」页与银行回函/对账单行。"""
        rows = money_fund_rows()
        snapshot = self.snapshot(rows=rows, scan=None, results=())
        digest = hashlib.sha256(b"synthetic bank confirmation").hexdigest()
        evidence = evidence_row(digest)
        context = self.prepare_workpaper_context(snapshot, evidence=[evidence])
        output = self.root / "out" / "货币资金-底稿.xlsx"
        output.parent.mkdir(parents=True, exist_ok=True)

        value = agent_tools.export_workpaper(context, {"index_no": "1-010"},
                                             self.runtime(output))
        self.assertTrue(value["ok"], value)
        self.assertEqual(value["data"]["evidence"], 1)
        self.assertEqual({item["kind"] for item in context.artifacts}, {"workpaper"})

        book = load_workbook(output, read_only=True)
        try:
            self.assertIn(EVIDENCE_SHEET, book.sheetnames)
            sheet = book[EVIDENCE_SHEET]
            header = [str(cell) for cell in next(sheet.iter_rows(max_row=1, values_only=True))]
            self.assertEqual(header, ["字段", "提取值", "引用原文", "待核风险", "下一步程序",
                                      "本地余额核对", "文件", "版本", "页码", "证据编号",
                                      "项目资料 SHA-256", "确认人"])
            matching_rows = [row for row in sheet.iter_rows(min_row=2, values_only=True)
                             if EVIDENCE_REF in {str(cell) for cell in row}]
            self.assertEqual(len(matching_rows), 1, "证据行未写入正确工作表")
            values = [str(cell) for cell in matching_rows[0]]
            joined = " ".join(values)
            self.assertIn(evidence["value"], values)
            self.assertIn(evidence["filename"], values)
            self.assertIn(evidence["sha256"], values)
            self.assertIn(evidence["reviewer"], values)
            self.assertIn("银行回函", joined)
            self.assertIn("对账单", joined)
            # 证据页在工作簿里而不只是返回值里。
            other_sheets = [name for name in book.sheetnames if name != EVIDENCE_SHEET]
            self.assertTrue(other_sheets)   # 整册底稿的其余页确实存在
        finally:
            book.close()

    def test_workpaper_without_evidence_records_gap(self):
        """1-010：无已确认证据时，export_workpaper 返回 warnings 并登记 gaps。"""
        rows = money_fund_rows()
        snapshot = self.snapshot(rows=rows, scan=None, results=())
        context = self.prepare_workpaper_context(snapshot)
        output = self.root / "out" / "货币资金-底稿-无证据.xlsx"
        output.parent.mkdir(parents=True, exist_ok=True)

        value = agent_tools.export_workpaper(context, {"index_no": "1-010"},
                                             self.runtime(output))
        self.assertTrue(value["ok"], value)
        self.assertEqual(value["data"]["evidence"], 0)
        warnings = " ".join(str(item) for item in value["warnings"])
        gaps = " ".join(str(item) for item in context.gaps)
        self.assertIn(GAP_WARNING, warnings, f"未记录证据缺口：{value['warnings']}")
        self.assertIn(GAP_DETAIL, warnings, f"未记录证据缺口：{value['warnings']}")
        self.assertIn(GAP_WARNING, gaps, f"未登记运行缺口：{context.gaps}")
        self.assertIn(GAP_DETAIL, gaps, f"未登记运行缺口：{context.gaps}")

        book = load_workbook(output, read_only=True)
        try:
            self.assertNotIn(EVIDENCE_SHEET, book.sheetnames)
        finally:
            book.close()


if __name__ == "__main__":
    unittest.main()
