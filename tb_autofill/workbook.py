import csv
import os
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple

from .fields import file_digest
from .models import ledger_digest, money


def validate_source_context(scan, results, rows):
    if scan.source_digest and (rows is None or ledger_digest(rows) != scan.source_digest):
        raise ValueError("账套或主体期间已变化，请重新读取并识别模板。")
    if rows is not None:
        available = set(rows)
        if any(source not in available for result in results for source in result.sources):
            raise ValueError("结果包含当前账套之外的来源，请重新识别。")


def planned_writes(scan, results, *, rows=None):
    from .matching import resolve_match, validate_claims
    validate_source_context(scan, results, rows)
    known = {field.id: field for field in scan.fields}
    selected = [result for result in results if result.selected]
    if not selected:
        raise ValueError("请先勾选需要写入的已匹配字段。")
    writes, seen = [], set()
    checked = []
    for result in selected:
        target = known.get(result.target.id)
        if target is None or target != result.target or target.id in seen:
            raise ValueError("目标字段已变化或重复，请重新识别。")
        seen.add(target.id)
        if target.is_formula:
            raise ValueError(f"{target.id} 是公式，禁止覆盖。")
        check = resolve_match(target, result.sources, method=result.method)
        if check.status != "matched" or check.amount is None or check.amount != result.amount:
            raise ValueError(f"{target.id} 未通过写入前复核：{check.reason}")
        if target.value is not None and str(target.value).strip() and not result.overwrite:
            raise ValueError(f"{target.id} 已有数值，请明确勾选允许覆盖。")
        check.selected = True
        checked.append(check)
        writes.append((target, result.amount))
    validate_claims(checked)
    if any(item.status != "matched" for item in checked):
        raise ValueError("选中字段重复使用了同一来源或其上下级，请先处理重复领取。")
    if file_digest(scan.path) != scan.digest:
        raise ValueError("模板文件已被修改，请重新识别后再导出。")
    return writes


def _fingerprint(cell):
    value = cell.value
    if cell.data_type == "f" and not isinstance(value, str):
        return "formula", getattr(value, "text", str(value)), getattr(value, "ref", "")
    return cell.data_type, value


def verify_output(source, output, writes):
    expected = {field.id: amount for field, amount in writes}
    before = load_workbook(source, read_only=False, data_only=False, keep_links=True)
    after = load_workbook(output, read_only=False, data_only=False, keep_links=True)
    try:
        if before.sheetnames != after.sheetnames:
            raise ValueError("输出工作表结构发生变化，结果未通过验证。")
        for sheet in before.worksheets:
            other = after[sheet.title]
            if set(map(str, sheet.merged_cells.ranges)) != set(map(str, other.merged_cells.ranges)):
                raise ValueError(f"{sheet.title} 合并单元格发生变化。")
            # Compare both extents without materializing a potentially enormous rectangle.
            for row, col in sorted(set(sheet._cells) | set(other._cells)):
                cell = sheet.cell(row, col)
                key = f"{sheet.title}!{cell.coordinate}"
                actual = other[cell.coordinate]
                if key in expected:
                    value = money(actual.value)
                    if value is None or abs(value - expected[key]) > Decimal("0.005"):
                        raise ValueError(f"{key} 保存后的金额与计划不一致。")
                elif _fingerprint(cell) != _fingerprint(actual):
                    raise ValueError(f"{key} 是保护区域但内容发生变化，输出未通过验证。")
    finally:
        before.close()
        after.close()


def write_results(scan, results, output, *, rows=None):
    writes = planned_writes(scan, results, rows=rows)
    output = Path(output).resolve()
    if output == scan.path or (output.exists() and os.path.samefile(output, scan.path)):
        raise ValueError("请另存为新文件，不能覆盖原始模板。")
    if output.suffix.lower() != scan.path.suffix.lower():
        raise ValueError("输出扩展名必须与模板相同，以保留原格式。")
    if output.exists():
        raise ValueError("输出文件已存在，请使用新的文件名。")
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, draft_name = tempfile.mkstemp(prefix=".zszh-", suffix=output.suffix, dir=output.parent)
    os.close(handle)
    draft = Path(draft_name)
    excel = book = None
    initialized = False
    try:
        shutil.copy2(scan.path, draft)
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            initialized = True
            excel = win32com.client.DispatchEx("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False
            excel.EnableEvents = False
            excel.AskToUpdateLinks = False
            excel.AutomationSecurity = 3
            book = excel.Workbooks.Open(str(draft), UpdateLinks=0, ReadOnly=False, IgnoreReadOnlyRecommended=True, AddToMru=False)
            if book.ReadOnly:
                raise ValueError("输出副本只能以只读方式打开，未写入。")
            for field, amount in writes:
                row, col = coordinate_to_tuple(field.cell)
                cell = book.Worksheets(field.sheet).Cells(row, col)
                if cell.HasFormula:
                    raise ValueError(f"{field.id} 在 Excel 中是公式，已停止写入。")
                cell.Value2 = float(amount)
            excel.CalculateFullRebuild()
            book.Save()
            book.Close(SaveChanges=False)
            book = None
        except (ImportError, ModuleNotFoundError) as exc:
            raise ValueError("保存与重算需要 Windows Microsoft Excel 和 pywin32；请先安装。") from exc
        finally:
            if book is not None:
                book.Close(SaveChanges=False)
            if excel is not None:
                excel.Quit()
            if initialized:
                pythoncom.CoUninitialize()
        verify_output(scan.path, draft, writes)
        if file_digest(scan.path) != scan.digest:
            raise ValueError("导出期间原模板发生变化，未发布输出，请重新识别。")
        # Windows rename refuses an existing destination, including one created after the initial check.
        os.rename(draft, output)
        return output
    finally:
        if draft.exists():
            draft.unlink()


def _safe_text(value):
    text = str(value or "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def export_report(path, results, *, subject="", unit="元", warnings=()):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig", newline="") as destination:
        writer = csv.writer(destination)
        writer.writerow(["主体与期间", "目标字段", "名称", "父科目", "期间", "金额单位", "状态", "来源科目", "应填值", "现有值", "差额", "方法", "原因", "选中", "允许覆盖"])
        for result in results:
            field = result.target
            writer.writerow([_safe_text(subject), _safe_text(field.id), _safe_text(field.label), _safe_text(field.parent),
                "期初" if field.period == "opening" else "期末", unit, result.status,
                _safe_text("; ".join(f"{x.code} {x.name}" for x in result.sources)),
                str(result.amount) if result.amount is not None else "", _safe_text(field.value),
                str(result.difference) if result.difference is not None else "", result.method,
                _safe_text(result.reason), "是" if result.selected else "否", "是" if result.overwrite else "否"])
        for warning in warnings:
            writer.writerow(["提示", _safe_text(warning)])
    return path
