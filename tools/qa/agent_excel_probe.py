"""真实 Excel 写入探针：合成 TB 模板 + 合成科目余额，验证真实 Excel COM 写入与重算。

用途：单独确认「本机 Microsoft Excel + pywin32」链路是否可用，避免把 mock 当成通过。
只使用合成数据，不联网、不读真实账套；临时文件只建在工作区 ``build/`` 下并清理。

退出码：
- 0：真实 Excel COM 写入、重算、回读校验均通过（已验证）。
- 2：真实 Excel COM 不可用，本次“未执行”，原因见输出；不代表通过。
- 1：Excel COM 可用，但写入或回读校验失败。
"""

import hashlib
import os
import shutil
import sys
import time
import traceback
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook, load_workbook

from tb_autofill import fields, matching, workbook
from tb_autofill.models import LedgerRow, ledger_digest, money

COLUMNS = ("科目名称", "期末余额")
END_SHEET = "期末"


def console_safe(text, limit=400):
    """控制台编码可能是 GBK；错误文本不能带不可编码字符。"""
    text = " ".join(str(text).split())
    return text.encode("gbk", errors="replace").decode("gbk")[:limit]


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def probe_excel():
    """真实尝试 Excel COM 新建工作簿；返回 ``(可用, 说明)``。"""
    try:
        import pythoncom
        import win32com.client
    except Exception as exc:
        return False, f"pywin32 未安装（{console_safe(exc)}）"
    excel = book = None
    initialized = False
    try:
        pythoncom.CoInitialize()
        initialized = True
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        book = excel.Workbooks.Add()
        version = console_safe(excel.Version, 40)
        book.Close(SaveChanges=False)
        book = None
        return True, f"Microsoft Excel {version}"
    except Exception as exc:
        return False, f"Excel COM 无法新建/打开工作簿（{console_safe(exc)}）"
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


def synthetic_template(path):
    book = Workbook()
    sheet = book.active
    sheet.title = END_SHEET
    sheet.append(list(COLUMNS))
    sheet.append(["库存现金", None])
    sheet.append(["银行存款", None])
    book.save(path)
    book.close()
    return Path(path)


def ledger_rows():
    return [
        LedgerRow("1001", "库存现金", path="库存现金", opening=Decimal("50"),
                  debit=Decimal("60"), credit=Decimal("10"), ending=Decimal("100"),
                  subject_id="S1", year=2026, month=12, ledger_id="L1"),
        LedgerRow("1002", "银行存款", path="银行存款", opening=Decimal("200"),
                  debit=Decimal("30"), credit=Decimal("20"), ending=Decimal("210"),
                  subject_id="S1", year=2026, month=12, ledger_id="L1"),
    ]


def main():
    print("Attesta 真实 Excel 写入探针（合成数据，不联网）")
    available, detail = probe_excel()
    print(f"pywin32/Excel COM：{'可用 - ' if available else '不可用 - '}{detail}")
    if not available:
        print("真实 Excel 写入与重算：未执行")
        print(f"原因：{detail}")
        print("说明：本探针不伪造通过；请在没有沙箱限制的本机会话中重跑以验证真实重算。")
        return 2

    base = ROOT / "build"
    base.mkdir(parents=True, exist_ok=True)
    folder = base / f"excel-probe-{os.getpid()}-{time.time_ns()}"
    folder.mkdir()
    try:
        template = synthetic_template(folder / "合成TB模板.xlsx")
        before = sha256(template)
        rows = ledger_rows()
        scan = fields.scan_template(template, ("ending",), "元")
        scan.source_digest = ledger_digest(rows)
        results = matching.match_fields(scan.fields, rows)
        selected = [item for item in results if item.selected]
        print(f"合成模板字段：{len(results)} 个；待写入：{len(selected)} 个")

        output = folder / "合成TB副本.xlsx"
        saved = workbook.write_results(scan, results, output, rows=rows)
        print(f"真实 Excel 已生成副本：{saved.name}")

        book = load_workbook(saved, data_only=False)
        try:
            sheet = book[END_SHEET]
            written = {"B2": money(sheet["B2"].value), "B3": money(sheet["B3"].value)}
        finally:
            book.close()
        expected = {"B2": Decimal("100"), "B3": Decimal("210")}
        print(f"openpyxl 回读：B2={written['B2']}；B3={written['B3']}（期望 100 / 210）")
        unchanged = sha256(template) == before
        print(f"原模板 SHA-256 不变：{'是' if unchanged else '否'}")
        checks = workbook.tb_balance_checks(template, scan)
        print(f"tb_balance_checks：{len(checks)} 项（本模板无合计行，属预期）")

        problems = []
        if written != expected:
            problems.append("回读金额与计划不一致")
        if not unchanged:
            problems.append("原模板被修改")
        if problems:
            print("真实 Excel 写入与重算：失败 - " + "；".join(problems))
            return 1
        print("真实 Excel 写入与重算：已验证")
        return 0
    except Exception:
        traceback.print_exc()
        print("真实 Excel 写入与重算：失败")
        return 1
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
