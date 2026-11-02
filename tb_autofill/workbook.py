import csv
import os
import tempfile
import ntpath
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlsplit

from openpyxl import load_workbook
from openpyxl.formula import Tokenizer
from openpyxl.utils.cell import coordinate_to_tuple

from .fields import file_digest
from .models import ledger_digest, money


def validate_source_context(scan, results, rows, *, strict_period=False):
    if scan.source_digest and (rows is None or ledger_digest(rows) != scan.source_digest):
        raise ValueError("账套或主体期间已变化，请重新读取并识别模板。")
    if scan.ending_periods:
        actual = {(row.year, row.month) for row in rows} if rows else set()
        declared = {(year, month) for _, year, month in scan.ending_periods}
        if any(actual != {(year, month)} for _, year, month in scan.ending_periods):
            # 金额仍按账套取数；期间差异只提示，不阻断识别，写入前由人工再确认。
            scan.period_mismatch = sorted(declared)
            note = "模板声明的截止日期与账套期间不一致（模板：%s；账套：%s）。金额按当前账套填列，写入前请确认期间口径。" % (
                "、".join(f"{year}-{month:02d}" for year, month in sorted(declared)) or "未声明",
                "、".join(f"{year}-{month:02d}" for year, month in sorted(actual)) or "缺失")
            if note not in scan.warnings:
                scan.warnings.append(note)
            if strict_period and not getattr(scan, "allow_period_mismatch", False):
                raise ValueError("模板截止日期与账套期间不一致，已按人工确认要求停止写入；确认口径后可继续。")
        else:
            scan.period_mismatch = None
    if rows is not None:
        available = set(rows)
        if any(source not in available for result in results for source in result.sources):
            raise ValueError("结果包含当前账套之外的来源，请重新识别。")


def planned_writes(scan, results, *, rows=None):
    from .matching import resolve_match, validate_claims
    validate_source_context(scan, results, rows, strict_period=True)
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
        check = resolve_match(target, result.sources, method=result.method, rows=rows)
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


def _external_targets(book, path):
    result = []
    for link in book._external_links:
        relationship = link.file_link
        if relationship is None:
            result.append(None)
            continue
        target = relationship.Target
        if re.match(r"^[A-Za-z]:", target) and not re.match(r"^[A-Za-z]:[/\\]", target):
            result.append(None)
            continue
        url = urlsplit(target)
        if url.scheme and url.scheme.lower() != "file" and not re.match(r"^[A-Za-z]:[/\\]", target):
            result.append(("uri", target))
            continue
        drive_path = bool(re.match(r"^[A-Za-z]:[/\\]", target))
        value = unquote(target if drive_path else url.path)
        if url.scheme.lower() == "file":
            value = "//"+url.netloc+value if url.netloc else re.sub(r"^/(?=[A-Za-z]:)", "", value)
        base = str(Path(path).parent)
        if not ntpath.splitdrive(value)[0]:
            value = ntpath.splitdrive(base)[0]+value if ntpath.isabs(value) else ntpath.join(base,value)
        result.append(("file", ntpath.normpath(value), "" if drive_path else url.query, "" if drive_path else url.fragment))
    return tuple(result)


def _verify_external_targets(before, after):
    if None in before or None in after:
        raise ValueError("存在无法核对的外部链接类型，未自动保存。")
    if Counter(before) != Counter(after):
        raise ValueError("外部链接目标发生变化，输出未通过验证。")


def _formula_key(value, targets):
    if not isinstance(value, str):
        return ("opaque_formula", repr(value), targets) if targets else value
    if not targets or not re.search(r"\[\d+\]", value):
        return value
    try:
        tokens = Tokenizer(value).items
    except Exception:
        return "opaque_formula", value, targets
    parts, changed = [], False
    for token in tokens:
        item = token.value
        match = re.match(r"^('?)(\[(\d+)\])(?=[^!]+!)", item) if token.type == "OPERAND" and token.subtype == "RANGE" else None
        if match and 0 < int(match[3]) <= len(targets) and targets[int(match[3])-1] is not None:
            item = (item[:match.start(2)], targets[int(match[3])-1], item[match.end(2):])
            changed = True
        elif token.type == "OPERAND" and token.subtype == "RANGE" and re.search(r"\[\d+\].*!", item):
            item = ("opaque_range", item, targets)
            changed = True
        parts.append((token.type, token.subtype, item))
    return tuple(parts) if changed else value


def _fingerprint(cell, targets=()):
    value = cell.value
    if cell.data_type == "f" and not isinstance(value, str):
        return "formula", _formula_key(getattr(value, "text", str(value)), targets), getattr(value, "ref", "")
    return cell.data_type, _formula_key(value, targets) if cell.data_type == "f" else value


def verify_output(source, output, writes):
    expected = {field.id: (amount, field.unit_factor) for field, amount in writes}
    before = load_workbook(source, read_only=False, data_only=False, keep_links=True)
    after = load_workbook(output, read_only=False, data_only=False, keep_links=True)
    try:
        before_links, after_links = _external_targets(before, source), _external_targets(after, output)
        _verify_external_targets(before_links, after_links)
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
                    amount, factor = expected[key]
                    if value is None or abs(value - amount) * factor > Decimal("0.005"):
                        raise ValueError(f"{key} 保存后的金额与计划不一致。")
                elif _fingerprint(cell, before_links) != _fingerprint(actual, after_links):
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
    excel = book = cell = None
    initialized = False
    try:
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
            book = excel.Workbooks.Open(str(scan.path), UpdateLinks=0, ReadOnly=True, IgnoreReadOnlyRecommended=True, AddToMru=False)
            if not book.ReadOnly:
                raise ValueError("原始模板未以只读方式打开，未写入。")
            book.SaveAs(str(draft), FileFormat=book.FileFormat)
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
            cell = None
            if book is not None:
                book.Close(SaveChanges=False)
                book = None
            if excel is not None:
                excel.Quit()
                excel = None
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
    text = "" if value is None else str(value)
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
