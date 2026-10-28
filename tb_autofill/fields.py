import hashlib
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

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
        for row in sheet.iter_rows(max_row=min(sheet.max_row, 100), max_col=min(sheet.max_column, 40)):
            for cell in row:
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


def scan_template(path, periods=("ending",), unit="自动"):
    path = Path(path).resolve()
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ValueError("请导入 .xlsx 或 .xlsm 模板；旧版 .xls 请先用 Excel 另存。")
    periods = set(periods)
    if not periods or not periods <= {"opening", "ending"}:
        raise ValueError("请选择有效的填列期间")
    before = file_digest(path)
    workbook = load_workbook(path, read_only=False, data_only=False, keep_links=True)
    fields, warnings = [], []
    try:
        tb_sheets = [sheet for sheet in workbook.worksheets
                     if re.fullmatch(r"试算平衡表\((?:期初|期末)\)", text_key(sheet.title))]
        sheets = tb_sheets or workbook.worksheets
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
            if strict_tb and re.search(r"\((?:合并|合并抵消)\)", text_key(path.stem)):
                warnings.append(f"{sheet.title}：合并或抵消底稿不能使用单体账套自动填列，已跳过。")
                continue
            if sheet.max_row > 50000 or sheet.max_column > 500:
                warnings.append(f"{sheet.title}：使用区域过大，未自动识别，请清理无用格式后重试。")
                continue
            merged_ranges = list(sheet.merged_cells.ranges)
            def resolved_label(cell):
                value = cell.value
                if cell.data_type == "f":
                    return _unit_text(workbook, sheet, value) if isinstance(value, str) else ""
                return value
            def header_value(row, col):
                value = sheet.cell(row, col).value
                if value is None:
                    for region in merged_ranges:
                        if region.min_row <= row <= region.max_row and region.min_col <= col <= region.max_col:
                            return sheet.cell(region.min_row, region.min_col).value
                return value
            context = sheet.title + " " + " ".join(str(c.value or "") for row in sheet.iter_rows(max_row=min(5, sheet.max_row)) for c in row if c.data_type != "f")
            header_row, label_col, columns, parent, section_profit = 0, 0, {}, "", False
            index_cols = set()
            balance_direction = ""
            business_labels = None
            for row in sheet.iter_rows():
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
                    band_end = rownum
                    for region in merged_ranges:
                        if region.min_row == rownum and region.min_col <= labels[0] <= region.max_col:
                            band_end = max(band_end, region.max_row)
                    # Only follow actual header rows; never consume the first account row.
                    for candidate in range(band_end + 1, min(rownum + 4, sheet.max_row) + 1):
                        label_value = sheet.cell(candidate, labels[0]).value
                        fragments = [str(sheet.cell(candidate, c).value or "")
                                     for c in range(1, sheet.max_column + 1)]
                        if label_value not in (None, "") or not any(
                                re.fullmatch(r"(?:期初|期末|本月|本年)?(?:余额|发生额|借方|贷方|账面数|未审数|审定数|调整分录)", text_key(v))
                                for v in fragments):
                            break
                        band_end = candidate
                    index_cols = {col for col in range(1, sheet.max_column + 1)
                                  if any("索引" in text_key(header_value(r, col))
                                         for r in range(rownum, band_end + 1))}
                    proposed = {}
                    for col in range(1, sheet.max_column + 1):
                        header = " ".join(str(header_value(r, col) or "") for r in range(rownum, band_end + 1))
                        if not header:
                            continue
                        if strict_tb:
                            fragments = {text_key(header_value(r, col)) for r in range(rownum, band_end + 1)} - {""}
                            wanted = "上期审定数" if "期初" in sheet.title else "期末未审数"
                            role = _column_role(wanted, context) if fragments == {wanted} else None
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
                                for cells in sheet.iter_rows(min_col=labels[0], max_col=min(proposed) - 1)
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
    if file_digest(path) != before:
        raise ValueError("识别期间模板发生变化，请重新导入。")
    return TemplateScan(path, before, fields, warnings, unit)
