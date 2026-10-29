import hashlib
import os
import re
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

from openpyxl import load_workbook

from .models import TargetField


def text_key(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class TemplateScan:
    path: Path
    digest: str
    fields: list[TargetField]
    warnings: list[str]
    unit: str = "元"
    source_digest: str = ""


UNITS = {"元": Decimal("1"), "千元": Decimal("1000"), "万元": Decimal("10000"), "亿元": Decimal("100000000")}


def _unit_text(workbook, sheet, value, seen=()):
    if not isinstance(value, str) or not value.startswith("="):
        return str(value or "")
    if len(seen) >= 5:
        return ""
    expression = value[1:]
    reference = re.fullmatch(r"(?:(?:'([^']+)'|([^'!]+))!)?(\$?[A-Z]{1,3}\$?\d+)", expression)
    if reference:
        name = reference[1] or reference[2] or sheet.title
        address = reference[3].replace("$", "")
        identity = (name, address)
        if name in workbook.sheetnames and identity not in seen:
            return _unit_text(workbook, workbook[name], workbook[name][address].value, (*seen, identity))
    concat = re.fullmatch(r'"((?:[^"]|"")*)"&((?:\$?[A-Z]{1,3}\$?\d+))', expression)
    if concat:
        return concat[1].replace('""', '"') + _unit_text(workbook, sheet, "=" + concat[2], seen)
    return ""


def _find_units(workbook, sheets=None):
    units = set()
    for sheet in workbook.worksheets if sheets is None else sheets:
        if _scan_bounds(sheet)[2]:
            continue
        # Snapshot existing cells so distant units do not materialize empty sheet regions.
        for cell in tuple(sheet._cells.values()):
            if cell.value is None:
                continue
            text = text_key(_unit_text(workbook, sheet, cell.value))
            label = re.search(r"(?:金额单位|币种单位)[:：]?|^单位[:：]", text)
            if not label:
                continue
            value = text[label.end():]
            if not value:
                for col in range(cell.column + 1, min(cell.column + 5, sheet.max_column) + 1):
                    value = text_key(_unit_text(workbook, sheet, sheet.cell(cell.row, col).value))
                    if value:
                        break
            found = re.fullmatch(r"(?:人民币)?[（(]?(亿元|万元|千元|元)[）)]?", value)
            if found:
                units.add(found[1])
    return units


def _period(text):
    begin = bool(re.search(r"期初|年初|上期|上年|年初数", text))
    end = bool(re.search(r"期末|年末|本期|本年|本月|当月|单月", text))
    return "opening" if begin and not end else "ending" if end and not begin else ""


def _column_role(header, context, detail=""):
    header, detail = text_key(header), text_key(detail)
    header = header + detail
    if not header or any(x in header for x in ("调整", "重分类", "差额", "比例", "比率", "索引", "行次", "客户报出", "未调整")):
        return None
    if not any(x in header for x in ("余额", "未审数", "审定数", "账面数", "期初数", "期末数", "年初数", "年末数", "上年数", "本年数", "本期数", "金额", "发生额")):
        return None
    if re.search(r"期初|年初|上期|上年", header) and re.search(r"期末|年末|本期|本年|本月|当月", header):
        return None
    period = _period(header) or _period(context)
    if not period:
        return None
    basis = "audited" if any(x in header for x in ("审定", "已审", "审后")) else "book"
    if "企业报表" in header or "客户" in header:
        basis = "unknown"
    measure = "balance"
    side = ""
    if "发生额" in header:
        if "借" in header and "贷" in header:
            return None
        measure = "debit" if "借" in header else "credit" if "贷" in header else "movement"
    elif "余额" in header and ("借" in header or "贷" in header):
        if "借" in header and "贷" in header:
            return None
        side = "借" if "借" in header else "贷"
    scope = "month" if re.search(r"本月|当月|单月", header) else "year_to_date"
    return period, basis, measure, side, scope


LABEL_HEADERS = {"报表项目", "报表项目名称", "项目", "项目名称", "科目", "科目名称", "会计科目", "账户名称"}
CODE_PATTERN = re.compile(r"(?:[A-Z]{1,4}\d+(?:-\d+)*|\d{4,})$")

# Declared extents include stale formatting; bounding by populated cells keeps
# rectangle scans finite while oversized auxiliary pages stay out of the scan.
AUXILIARY_ROW_LIMIT = 50000
AUXILIARY_CELL_LIMIT = 300000


def _populated_extent(sheet):
    """Rows and columns that actually hold cells, ignoring inflated formatting."""
    rows = cols = 0
    for row, col in sheet._cells:
        if row > rows:
            rows = row
        if col > cols:
            cols = col
    return rows, cols


def _scan_bounds(sheet):
    """Maximum safe scan bounds plus whether the page is an auxiliary ledger."""
    rows, cols = _populated_extent(sheet)
    populated = len(sheet._cells)
    auxiliary = populated > AUXILIARY_CELL_LIMIT or rows > AUXILIARY_ROW_LIMIT
    return rows, cols, auxiliary


def _trimmed_sheet(sheet):
    """Declared extent that is further clipped to populated cells."""
    rows, cols = _populated_extent(sheet)
    return min(sheet.max_row, rows or 1), min(sheet.max_column, cols or 1)


# Parsing an oversized auxiliary page costs more than the whole scan. Such pages
# can never hold the standalone TB, so they are excluded from the scan copy.
HEAVY_SHEET_BYTES = 3 * 1024 * 1024
TB_TITLE_HINT = re.compile(r"试算平衡表|TB[-_ (（]*期[初末]|期[初末][-_ ()）]*TB", re.I)
REL_NAMESPACE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SHEET_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PACKAGE_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"


def _sheet_parts(archive):
    """Worksheet titles and their archive parts, in workbook order."""
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {node.get("Id"): node.get("Target", "") for node in rels}
    parts = {}
    sheet_nodes = workbook.find(f"{{{SHEET_NAMESPACE}}}sheets")
    for node in sheet_nodes if sheet_nodes is not None else ():
        target = targets.get(node.get(f"{{{REL_NAMESPACE}}}id"), "")
        name = node.get("name")
        if not name or not target:
            continue
        part = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        parts[name] = part.replace("xl/xl/", "xl/")
    return parts


def _heavy_auxiliary_parts(path):
    """Oversized worksheet parts that cannot be the standalone TB page."""
    try:
        with zipfile.ZipFile(path) as archive:
            drops = {}
            for title, part in _sheet_parts(archive).items():
                if TB_TITLE_HINT.search(text_key(title)):
                    continue
                try:
                    size = archive.getinfo(part).file_size
                except KeyError:
                    continue
                if size >= HEAVY_SHEET_BYTES:
                    drops[title] = part
            return drops
    except (OSError, KeyError, ElementTree.ParseError, zipfile.BadZipFile):
        return {}


def _drop_sheet_nodes(data, drops):
    root = ElementTree.fromstring(data)
    sheets = root.find(f"{{{SHEET_NAMESPACE}}}sheets")
    if sheets is None:
        return data
    for node in list(sheets):
        if node.get("name") in drops:
            sheets.remove(node)
    return ElementTree.tostring(root, encoding="UTF-8", xml_declaration=True)


def _drop_rel_nodes(data, drops):
    root = ElementTree.fromstring(data)
    dropped = set(drops.values())
    for node in list(root):
        target = node.get("Target", "")
        part = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        if part.replace("xl/xl/", "xl/") in dropped:
            root.remove(node)
    return ElementTree.tostring(root, encoding="UTF-8", xml_declaration=True)


def _drop_type_nodes(data, drops):
    root = ElementTree.fromstring(data)
    dropped = {f"/{part}" for part in drops.values()}
    for node in list(root):
        if node.get("PartName") in dropped:
            root.remove(node)
    return ElementTree.tostring(root, encoding="UTF-8", xml_declaration=True)


def _projected_scan_copy(path, drops):
    """Scan-only copy without heavy auxiliary pages; never touches the original."""
    handle, name = tempfile.mkstemp(prefix="deepaudit_scan_", suffix=".xlsx")
    os.close(handle)
    target = Path(name)
    rewritten = {"xl/workbook.xml": _drop_sheet_nodes,
                 "xl/_rels/workbook.xml.rels": _drop_rel_nodes,
                 "[Content_Types].xml": _drop_type_nodes}
    try:
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as output:
            dropped = set(drops.values())
            for item in source.infolist():
                if item.filename in dropped:
                    continue
                payload = source.read(item.filename)
                rewrite = rewritten.get(item.filename)
                if rewrite:
                    payload = rewrite(payload, drops)
                output.writestr(item, payload)
        return target
    except Exception:
        target.unlink(missing_ok=True)
        return None


def _merged_value(sheet, ranges, row, col):
    value = sheet.cell(row, col).value
    if value is None:
        for region in ranges:
            if region.min_row <= row <= region.max_row and region.min_col <= col <= region.max_col:
                return sheet.cell(region.min_row, region.min_col).value
    return value


def _header_band_end(sheet, rownum, label_col, merged_ranges):
    band_end = rownum
    for region in merged_ranges:
        if region.min_row == rownum and region.min_col <= label_col <= region.max_col:
            band_end = max(band_end, region.max_row)
    # Only follow actual header rows; never consume the first account row.
    scan_row_limit, scan_col_limit = _trimmed_sheet(sheet)
    for candidate in range(band_end + 1, min(rownum + 4, scan_row_limit) + 1):
        label_value = sheet.cell(candidate, label_col).value
        fragments = [text_key(sheet.cell(candidate, col).value)
                     for col in range(1, scan_col_limit + 1)]
        if label_value not in (None, "") or not any(
                re.fullmatch(r"(?:期初|期末|本月|本年)?(?:余额|发生额|借方|贷方|账面数|未审数|审定数|调整分录)", text)
                for text in fragments):
            break
        band_end = candidate
    return band_end


def _is_tb_title(text):
    return "试算平衡表" in text or re.fullmatch(
        r"(?:TB[-_(]*期[初末](?:数)?\)?|期[初末](?:数)?[-_()]*TB)",
        re.sub(r"\((?:合并|抵消|合并抵消)\)$", "", text), re.I)


def _tb_sheet_period(sheet):
    captions = [text_key(sheet.title)]
    header_row = label_col = 0
    scan_row_limit, scan_col_limit, auxiliary = _scan_bounds(sheet)
    if auxiliary:
        header_row = min((cell.row for cell in sheet._cells.values()
                          if cell.data_type != "f" and text_key(cell.value) in LABEL_HEADERS),
                         default=sheet.max_row)
        captions.extend(text_key(cell.value) for cell in sheet._cells.values()
                        if cell.row <= header_row and cell.data_type != "f"
                        and isinstance(cell.value, str) and "试算平衡表" in text_key(cell.value))
        return "" if any(_is_tb_title(text) for text in captions) else None
    for row in sheet.iter_rows(max_row=scan_row_limit or 1, max_col=scan_col_limit or 1):
        texts = [text_key(c.value) for c in row if c.data_type != "f" and isinstance(c.value, str)]
        captions.extend(t for t in texts if "试算平衡表" in t)
        labels = [c.column for c in row if c.data_type != "f" and text_key(c.value) in LABEL_HEADERS]
        if labels:
            header_row = row[0].row
            label_col = labels[0]
            break
    titles = [t for t in captions if _is_tb_title(t)]
    if not titles:
        return None
    if any(re.search(r"合并|抵消", t) for t in captions):
        return ""
    periods = {_period(t) for t in titles if re.search(r"期初|年初|上期|上年|期末|年末|本期|本年", t)}
    if not periods and header_row:
        parts = {"上期", "审定数", "上期审定数", "期末", "未审数", "期末未审数"}
        ranges = list(sheet.merged_cells.ranges)
        band_end = _header_band_end(sheet, header_row, label_col, ranges)
        audit_header = False
        for col in range(1, scan_col_limit + 1):
            fragments = []
            texts = [text_key(_merged_value(sheet, ranges, row, col))
                     for row in range(header_row, band_end + 1)]
            audit_header |= any(text == "审定数" or "上期审定数" in text or "未审数" in text for text in texts)
            for text in texts:
                if text and text not in parts:
                    break
                if text and text not in fragments:
                    fragments.append(text)
            header = "".join(fragments)
            if header in {"上期审定数", "期末未审数"}:
                periods.add(_period(header))
        if not periods and audit_header:
            return ""
    if not periods:
        # Plain opening/ending balance tables retain their existing generic import.
        return None
    return next(iter(periods)) if len(periods) == 1 and "" not in periods else ""


def scan_template(path, periods=("ending",), unit="自动"):
    path = Path(path).resolve()
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ValueError("请导入 .xlsx 或 .xlsm 模板；旧版 .xls 请先用 Excel 另存。")
    periods = set(periods)
    if not periods or not periods <= {"opening", "ending"}:
        raise ValueError("请选择有效的填列期间")
    before = file_digest(path)
    drops = _heavy_auxiliary_parts(path)
    scan_copy = _projected_scan_copy(path, drops) if drops else None
    workbook = load_workbook(scan_copy or path, read_only=False, data_only=False, keep_links=True)
    fields, warnings = [], []
    try:
        tb_periods = {sheet: period for sheet in workbook.worksheets
                      if (period := _tb_sheet_period(sheet)) is not None}
        tb_sheets = list(tb_periods)
        if drops and not tb_sheets:
            # Only a projection was parsed; retry the original so no page is silently lost.
            workbook.close()
            if scan_copy is not None:
                scan_copy.unlink(missing_ok=True)
            scan_copy = None
            workbook = load_workbook(path, read_only=False, data_only=False, keep_links=True)
            tb_periods = {sheet: period for sheet in workbook.worksheets
                          if (period := _tb_sheet_period(sheet)) is not None}
            tb_sheets = list(tb_periods)
            warnings.append(f"模板含超大辅助页，识别较慢：{'、'.join(list(drops)[:3])}；建议拆出试算平衡表页后重试。")
        elif drops:
            warnings.append(f"已跳过 {len(drops)} 个超大辅助页（{'、'.join(list(drops)[:3])}），仅解析试算平衡表所在页；原文件未修改。")
        sheets = tb_sheets or workbook.worksheets
        if re.search(r"合并|抵消", text_key(path.stem)):
            warnings.append("合并或抵消底稿不能使用单体账套自动填列，已跳过。")
            sheets = []
        found_units = _find_units(workbook, sheets)
        if unit == "自动":
            if len(found_units) > 1:
                raise ValueError("模板内识别到多种金额单位，请在界面明确选择输出单位，或统一模板单位后重试。")
            unit = next(iter(found_units), "元")
            if not found_units:
                warnings.append("未识别到金额单位，当前按元；请在预览前核对并可手动改选单位。")
        if unit not in UNITS:
            raise ValueError("请选择有效的金额单位")
        for sheet in sheets:
            strict_tb = sheet in tb_sheets
            scan_row_limit, scan_col_limit, auxiliary = _scan_bounds(sheet)
            if auxiliary:
                warnings.append(f"{sheet.title}：实际内容超出单体TB识别范围（{scan_row_limit}行），未自动识别，请拆出试算平衡表页后重试。")
                continue
            scan_row_limit = scan_row_limit or 1
            scan_col_limit = scan_col_limit or 1
            if strict_tb and not tb_periods[sheet]:
                warnings.append(f"{sheet.title}：合并、抵消或期间标题存在冲突，无法确认单体目标，已跳过。")
                continue
            merged_ranges = list(sheet.merged_cells.ranges)
            def resolved_label(cell):
                value = cell.value
                if cell.data_type == "f":
                    return _unit_text(workbook, sheet, value) if isinstance(value, str) else ""
                return value
            def header_value(row, col):
                return _merged_value(sheet, merged_ranges, row, col)
            context = sheet.title + " " + " ".join(str(c.value or "") for row in sheet.iter_rows(max_row=min(5, scan_row_limit), max_col=scan_col_limit) for c in row if c.data_type != "f")
            header_row, label_col, columns, parent, section_profit = 0, 0, {}, "", False
            index_cols = set()
            balance_direction = ""
            business_labels = None
            for row in sheet.iter_rows(max_row=scan_row_limit, max_col=scan_col_limit):
                rownum = row[0].row
                values = [(c.column, c.value) for c in row if c.value is not None and c.data_type != "f"]
                joined = " ".join(str(v) for _, v in values)
                if re.search(r"利润表|利润及|利润试算|损益表", joined):
                    section_profit = True
                if "资产负债表试算" in text_key(joined):
                    section_profit = False
                section_label = text_key(sheet.cell(rownum, label_col or 1).value)
                if strict_tb and not section_profit and section_label.endswith(":"):
                    if "负债" in section_label or "权益" in section_label:
                        balance_direction = "贷"
                    elif "资产" in section_label:
                        balance_direction = "借"
                labels = [col for col, val in values if text_key(val) in LABEL_HEADERS]
                if labels:
                    band_end = _header_band_end(sheet, rownum, labels[0], merged_ranges)
                    index_cols = {col for col in range(1, scan_col_limit + 1)
                                  if any("索引" in text_key(header_value(r, col))
                                         for r in range(rownum, band_end + 1))}
                    proposed = {}
                    for col in range(1, scan_col_limit + 1):
                        header = " ".join(str(header_value(r, col) or "") for r in range(rownum, band_end + 1))
                        if not header:
                            continue
                        if strict_tb:
                            fragments = list(dict.fromkeys(text_key(header_value(r, col))
                                for r in range(rownum, band_end + 1) if text_key(header_value(r, col))))
                            wanted = "上期审定数" if tb_periods[sheet] == "opening" else "期末未审数"
                            role = _column_role(wanted, context) if "".join(fragments) == wanted else None
                        else:
                            role = _column_role(header, context)
                        if role and role[0] in periods:
                            proposed[col] = role
                    if strict_tb and len(proposed) > 1:
                        warnings.append(f"{sheet.title}：同一表头存在多列目标金额，需要确认，未自动填列。")
                        proposed = {}
                    if proposed:
                        if strict_tb and business_labels is None:
                            business_labels = {text_key(value).replace("-", "")
                                for cells in sheet.iter_rows(min_col=labels[0], max_col=min(proposed) - 1, max_row=scan_row_limit)
                                for cell in cells if isinstance((value := resolved_label(cell)), str) and value}
                        # A book input column takes precedence over an audited result column for the same period.
                        for period in periods:
                            if any(r[0] == period and r[1] == "book" for r in proposed.values()):
                                proposed = {c: r for c, r in proposed.items() if r[0] != period or r[1] == "book"}
                        header_row, label_col, columns, parent = band_end, labels[0], proposed, ""
                    else:
                        header_row, label_col, columns, parent = band_end, labels[0], {}, ""
                    continue
                if not columns or rownum <= header_row:
                    continue
                primary = resolved_label(sheet.cell(rownum, label_col))
                label = primary if isinstance(primary, str) and not primary.startswith("=") else ""
                index = ""
                for col in range(label_col + 1, min(columns)):
                    value = resolved_label(sheet.cell(rownum, col))
                    if not isinstance(value, str) or not value.strip() or value.startswith("="):
                        continue
                    if col in index_cols:
                        candidate = text_key(value)
                        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", candidate):
                            index = candidate
                        continue
                    if CODE_PATTERN.fullmatch(text_key(value)):
                        index = text_key(value)
                    else:
                        label = value
                label = str(label).strip()
                if strict_tb and (re.search(r"试\s*算\s*平\s*衡\s*表|^被审计单位|^截止日期|^会计期间|^项\s*目\s*[:：]|^本表取值说明", label)
                                  or text_key(label).startswith(("资产负债表试算", "利润表试算", "损益表试算", "利润试算"))
                                  or text_key(label).endswith(":") or text_key(label) in {"减:", "加:"}):
                    continue
                if not label or len(label) > 100 or text_key(label) in {"借方", "贷方", "其中", "其中:", "金额", "索引号:", "流动资产:", "非流动资产:"}:
                    continue
                if isinstance(primary, str) and primary.strip() and not text_key(primary).startswith("其中"):
                    if label != primary.strip():
                        if not text_key(primary).startswith(("加:", "减:")):
                            parent = primary.strip().rstrip(":：")
                    elif (section_profit or not text_key(primary).startswith(("加:", "减:"))
                          or (strict_tb and index and "-" not in index)):
                        parent = label.rstrip(":：")
                for col, (period, basis, measure, side, scope) in columns.items():
                    cell = sheet.cell(rownum, col)
                    if cell.__class__.__name__ == "MergedCell":
                        continue
                    actual_measure = "profit_loss" if section_profit and measure == "balance" else measure
                    statement = ("profit_loss" if section_profit else "balance_sheet") if strict_tb else ""
                    direction = balance_direction if statement == "balance_sheet" else ""
                    if direction and (text_key(primary).startswith("减:") or text_key(label).startswith("减:")):
                        direction = "贷" if direction == "借" else "借"
                    if direction and text_key(label).endswith(("未确认融资费用", "未确认的融资费用")):
                        direction = "借"
                    elif direction and text_key(label).endswith(("未确认融资收益", "未实现融资收益")):
                        direction = "贷"
                    component = ""
                    if statement == "balance_sheet":
                        name = text_key(label).replace("-", "")
                        if name == "投资性房地产公允价值变动":
                            component = "fair_value_change"
                        elif name == "投资性房地产" and "投资性房地产公允价值变动" in (business_labels or set()):
                            component = "cost"
                    fields.append(TargetField(sheet.title, cell.coordinate, label, period, basis,
                        parent if parent != label else "", index, cell.value, cell.data_type == "f", actual_measure, side, UNITS[unit], scope,
                        statement=statement, presentation_direction=direction, component=component))
        if not fields:
            warnings.append("未找到所选期间的输入字段：请检查项目名称、科目名称以及期初/期末余额等表头文字。")
        if tb_sheets:
            warnings.append("已限定识别期初表的上期审定数、期末表的期末未审数；其他表和调整列保留。上期审定数需要审定来源，不能使用账面期初替代。")
    finally:
        workbook.close()
        if scan_copy is not None:
            scan_copy.unlink(missing_ok=True)
    if file_digest(path) != before:
        raise ValueError("识别期间模板发生变化，请重新导入。")
    return TemplateScan(path, before, fields, warnings, unit)
