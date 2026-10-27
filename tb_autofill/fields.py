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


def _find_units(workbook):
    units = set()
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(max_row=min(sheet.max_row, 100), max_col=min(sheet.max_column, 40)):
            for cell in row:
                text = text_key(cell.value)
                if "单位" not in text or not any(x in text for x in ("金额", "币种", "单位:", "单位：")):
                    continue
                nearby = text + " ".join(str(sheet.cell(cell.row, col).value or "") for col in range(cell.column + 1, min(cell.column + 5, sheet.max_column) + 1))
                found = re.findall(r"亿元|万元|千元|(?<![万千亿])元", nearby)
                units.update(found)
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
        found_units = _find_units(workbook)
        if unit == "自动":
            if len(found_units) > 1:
                raise ValueError("模板内识别到多种金额单位，请在界面明确选择输出单位，或统一模板单位后重试。")
            unit = next(iter(found_units), "元")
            if not found_units:
                warnings.append("未识别到金额单位，当前按元；请在预览前核对并可手动改选单位。")
        if unit not in UNITS:
            raise ValueError("请选择有效的金额单位")
        for sheet in workbook.worksheets:
            if sheet.max_row > 50000 or sheet.max_column > 500:
                warnings.append(f"{sheet.title}：使用区域过大，未自动识别，请清理无用格式后重试。")
                continue
            merged_ranges = list(sheet.merged_cells.ranges)
            def header_value(row, col):
                value = sheet.cell(row, col).value
                if value is None:
                    for region in merged_ranges:
                        if region.min_row <= row <= region.max_row and region.min_col <= col <= region.max_col:
                            return sheet.cell(region.min_row, region.min_col).value
                return value
            context = sheet.title + " " + " ".join(str(c.value or "") for row in sheet.iter_rows(max_row=min(5, sheet.max_row)) for c in row if c.data_type != "f")
            header_row, label_col, columns, parent, section_profit = 0, 0, {}, "", False
            for row in sheet.iter_rows():
                rownum = row[0].row
                values = [(c.column, c.value) for c in row if c.value is not None and c.data_type != "f"]
                joined = " ".join(str(v) for _, v in values)
                if re.search(r"利润表|利润及|利润试算|损益表", joined):
                    section_profit = True
                if "资产负债表试算" in text_key(joined):
                    section_profit = False
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
                    proposed = {}
                    for col in range(1, sheet.max_column + 1):
                        header = " ".join(str(header_value(r, col) or "") for r in range(rownum, band_end + 1))
                        if not header:
                            continue
                        role = _column_role(header, context)
                        if role and role[0] in periods:
                            proposed[col] = role
                    if proposed:
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
                primary = sheet.cell(rownum, label_col).value
                label = primary if isinstance(primary, str) and not primary.startswith("=") else ""
                index = ""
                for col in range(label_col + 1, min(columns)):
                    value = sheet.cell(rownum, col).value
                    if not isinstance(value, str) or not value.strip() or value.startswith("="):
                        continue
                    if CODE_PATTERN.fullmatch(text_key(value)):
                        index = text_key(value)
                    else:
                        label = value
                label = str(label).strip()
                if not label or len(label) > 100 or text_key(label) in {"借方", "贷方", "其中", "其中:", "金额", "索引号:", "流动资产:", "非流动资产:"}:
                    continue
                if isinstance(primary, str) and primary.strip() and not text_key(primary).startswith(("其中", "加:", "减:")):
                    if label != primary.strip():
                        parent = primary.strip().rstrip(":：")
                    elif any(sheet.cell(rownum, col).data_type == "f" for col in columns):
                        parent = label.rstrip(":：")
                for col, (period, basis, measure, side, scope) in columns.items():
                    cell = sheet.cell(rownum, col)
                    if cell.__class__.__name__ == "MergedCell":
                        continue
                    actual_measure = "profit_loss" if section_profit and measure == "balance" else measure
                    fields.append(TargetField(sheet.title, cell.coordinate, label, period, basis,
                        parent if parent != label else "", index, cell.value, cell.data_type == "f", actual_measure, side, UNITS[unit], scope))
        if not fields:
            warnings.append("未找到所选期间的输入字段：请检查项目名称、科目名称以及期初/期末余额等表头文字。")
    finally:
        workbook.close()
    if file_digest(path) != before:
        raise ValueError("识别期间模板发生变化，请重新导入。")
    return TemplateScan(path, before, fields, warnings, unit)
