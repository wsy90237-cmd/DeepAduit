"""鼎信诺式实质性底稿：按鼎信诺底稿的列式与勾稽关系生成。

从鼎信诺已生成底稿中拆出的取数结构：
1. ``dxnsjtempsheet`` 存放表头信息（被审计单位、期间、编制人、复核人、索引号、会计制度）；
2. 审定表每行以 ``1:K<科目编码>`` 标记取数科目，账面数、未审数、调整、审定数按固定列展开；
3. 账项明细表按科目逐行列示，期初、发生额、期末各自带「账面数—未审数—调整—审定数」四段；
4. 小计按借贷方向 SUMIF，合计再与本表核对，一致写 ``T/B``、不一致写 ``错``；
5. 报表数单独一行，与合计的差异单独一行。

本模块只复现列式与勾稽，数据一律来自本机账套（科目余额表）。期初数取账套期初，
期末数取账套期末；报表数本机未取数，一律写“待补”，不代替报表勾稽。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import paper_materials, paper_sheets, sampling
from .paper_sheets import MONEY, NUM, TEXT
from .models import money
from .paper_pack import baseline_notes, default_conclusion

TITLE_FONT = Font(name="宋体", size=14, bold=True)
HEAD_FONT = Font(name="宋体", size=10, bold=True)
BODY_FONT = Font(name="宋体", size=10)
SMALL_FONT = Font(name="宋体", size=9, color="40506B")
THIN = Side(style="thin", color="808080")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="F2F6FD")
TOTAL_FILL = PatternFill("solid", fgColor="FAFAFA")
MONEY_FORMAT = "#,##0.00;[Red]-#,##0.00"
PERCENT_FORMAT = "0.00%"
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center")
WAIT = "待补"

AUDIT_COLUMNS = ("科目编码", "项目名称", "借/贷", "期末账面数", "期末账表调整数", "期末未审数",
                 "账项调整（借）", "账项调整（贷）", "重分类调整（借）", "重分类调整（贷）",
                 "期末审定数", "索引号", "上期末账面数", "上期末账面调整数", "上期末未审数",
                 "上期末调整数", "上期末审定数", "变动额", "变动率", "本期审定与上期比较（变动额）",
                 "本期审定与上期比较（变动率）")
AUDIT_COLUMNS_PL = ("科目编码", "项目名称", "借/贷", "本期账面数", "本期账面调整数", "本期未审数",
                    "账项调整（借）", "账项调整（贷）", "重分类调整（借）", "重分类调整（贷）",
                    "本期审定数", "索引号", "上期账面数", "上期账面调整数", "上期未审数",
                    "上期调整数", "上期审定数", "变动额", "变动率", "本期审定与上期比较（变动额）",
                    "本期审定与上期比较（变动率）")
DETAIL_COLUMNS = ("取数标记", "科目编号", "上级科目名称", "项目名称", "借/贷",
                  "账面期初数", "账表差异期初调整", "未审期初数", "期初调整", "审定期初数",
                  "未审借方发生额", "未审贷方发生额", "审定借方发生额", "审定贷方发生额",
                  "账面期末数", "账表差异期末调整", "未审期末数", "期末调整", "审定期末数", "索引号")


def _amount(value):
    try:
        return money(value)
    except (ValueError, TypeError):
        return None


def dxn_code(code, level):
    """账套科目编码 → 鼎信诺底稿的编号写法：4 位一级 + 每级 2 位，用点分隔。"""
    text = str(code or "").strip()
    if not text.isdigit():
        return text
    level = int(level or 1)
    if level <= 1 or len(text) <= 4:
        return text
    parts, rest = [text[:4]], text[4:]
    for _ in range(level - 1):
        parts.append(rest[:2])
        rest = rest[2:]
    if rest:
        parts.append(rest)
    return ".".join(part for part in parts if part)


class Sheet:
    """统一表头与单元格样式。"""

    def __init__(self, book, title, widths):
        self.ws = book.create_sheet(title)
        self.ws.sheet_view.showGridLines = False
        for index, width in enumerate(widths, 1):
            self.ws.column_dimensions[get_column_letter(index)].width = width

    def merge(self, row, first, last, value, *, font=BODY_FONT, align=LEFT, fill=None, border=True,
              height=None):
        ws = self.ws
        if last > first:
            ws.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
        cell = ws.cell(row=row, column=first, value=value)
        if isinstance(value, str):
            cell.data_type = "s"
        cell.font, cell.alignment = font, align
        for column in range(first, last + 1):
            target = ws.cell(row=row, column=column)
            if border:
                target.border = BOX
            if fill is not None:
                target.fill = fill
        if height:
            ws.row_dimensions[row].height = height
        return cell

    def cell(self, row, column, value, *, font=BODY_FONT, align=LEFT, money=False, percent=False,
             fill=None, border=True, height=None, formula=False):
        cell = self.ws.cell(row=row, column=column, value=value)
        if isinstance(value, str):
            cell.data_type = "f" if formula else "s"
        cell.font, cell.alignment = font, align
        if border:
            cell.border = BOX
        if fill is not None:
            cell.fill = fill
        if money and isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            cell.number_format = MONEY_FORMAT
        if percent:
            cell.number_format = PERCENT_FORMAT
        if height:
            self.ws.row_dimensions[row].height = height
        return cell

    def header_block(self, meta, *, columns, title, index_no, page):
        """鼎信诺底稿的三段表头：项目名称 / 被审计单位 / 期间与签字。"""
        last = columns
        self.merge(1, 1, last, title, font=TITLE_FONT, align=CENTER, height=28)
        split = max(2, last // 2)
        right = max(split + 1, last - 3)
        self.merge(2, 1, split, f"被审计单位：{meta['project']}", border=False)
        self.merge(2, split + 1, right, f"索引号：{index_no}", border=False)
        self.merge(2, right + 1, last, f"页次：{page}", border=False)
        self.merge(3, 1, split, f"项目：{meta['name']}", border=False)
        self.merge(3, split + 1, right, f"编制人：{meta['prepared_by'] or '________'}", border=False)
        self.merge(3, right + 1, last, f"日期：{meta['date']}", border=False)
        self.merge(4, 1, split, f"财务报表截止日：{meta['cutoff']}", border=False)
        self.merge(4, split + 1, right, f"复核人：{meta['reviewed_by'] or '________'}", border=False)
        self.merge(4, right + 1, last, f"日期：{meta['date']}", border=False)
        self.merge(5, 1, last, f"会计期间：{meta['period']}　｜　会计制度：{meta['standard']}　｜　"
                              f"金额单位：人民币元　｜　{meta['source']}",
                   font=SMALL_FONT, height=22)
        return 7

    def page_setup(self, header_row=None):
        ws = self.ws
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        if header_row:
            ws.print_title_rows = f"{header_row}:{header_row}"


# ---------- 取数 ----------

def group_accounts(rows, paper, resolve):
    """审定表行：同一报表项目下、按清单能对应到本项目的一级科目。

    ``resolve(code, name)`` 返回 ``audit_index.Paper``；与 ``paper`` 索引号相同的科目归为同一报表项目，
    这正是鼎信诺审定表「货币资金 = 现金 + 银行存款 + 其他货币资金」的组装方式。
    """
    picked, seen = [], set()
    for row in rows:
        if (row.level or 1) != 1 or row.code in seen:
            continue
        matched = resolve(row.code, row.name)
        if matched is None or (paper is not None and matched.index_no != paper.index_no):
            continue
        seen.add(row.code)
        picked.append(row)
    picked.sort(key=lambda item: item.code)
    return picked


def detail_rows(rows, account):
    """只列直接子级，避免多层汇总与明细重复计数；无子级时列本科目。"""
    children = [row for row in rows if account.path and row.level == account.level + 1
                and row.path.rpartition("/")[0] == account.path]
    if children:
        return sorted(children, key=lambda item: item.code)
    return [account]


def consistency(accounts, rows):
    """按直接子级逐栏核对，余额统一为借方方向；缺失值不能充当零。"""
    gap = Decimal("0")
    for account in accounts:
        lines = detail_rows(rows, account)
        if len(lines) == 1 and lines[0] is account:
            continue
        for key in ("opening", "debit", "credit", "ending"):
            values = [_amount(getattr(line, key)) for line in [account, *lines]]
            if any(value is None for value in values):
                raise ValueError(f"科目 {account.code} 的 {key} 金额不完整，不能完成勾稽。")
            signed = key in ("opening", "ending")
            parent = values[0] * (-1 if signed and account.direction == "贷" else 1)
            child = sum((value * (-1 if signed and line.direction == "贷" else 1)
                         for line, value in zip(lines, values[1:])), Decimal("0"))
            gap += abs(parent - child)
    return abs(gap) <= Decimal("0.005"), gap


def totals(accounts):
    """余额按借方方向净额合计；借贷发生额保持各自方向。"""
    result = {"opening": Decimal("0"), "debit": Decimal("0"), "credit": Decimal("0"),
              "ending": Decimal("0"), "period": Decimal("0")}
    for account in accounts:
        for key in ("opening", "debit", "credit", "ending"):
            sign = -1 if key in ("opening", "ending") and account.direction == "贷" else 1
            result[key] += (_amount(getattr(account, key)) or Decimal("0")) * sign
        result["period"] += period_amount(account)
    return result


def period_amount(row):
    """损益类本期发生额：收入取贷方、费用取借方。"""
    debit = _amount(getattr(row, "debit", None)) or Decimal("0")
    credit = _amount(getattr(row, "credit", None)) or Decimal("0")
    return credit if str(getattr(row, "direction", "")) == "贷" else debit


# ---------- 读取鼎信诺底稿（取数结构） ----------

def read_metadata(path):
    """读取 ``dxnsjtempsheet`` 的表头信息；用于核对或导入既有底稿。"""
    book = load_workbook(path, data_only=True)
    if "dxnsjtempsheet" not in book.sheetnames:
        raise ValueError("该工作簿没有 dxnsjtempsheet 表，不是鼎信诺底稿。")
    sheet = book["dxnsjtempsheet"]
    values = {}
    for row in sheet.iter_rows(min_row=2, max_row=min(sheet.max_row, 60), max_col=4):
        key = str(row[1].value or "").strip()
        if key:
            values[key] = row[2].value
    return values


def read_layout(path):
    """拆出审定表与明细表的科目取数标记：返回 {科目编码: 行号}。"""
    book = load_workbook(path, data_only=True)
    result = {"metadata": read_metadata(path), "sheets": book.sheetnames}
    for name in ("审定表", "账项明细表", "明细表"):
        match = next((title for title in book.sheetnames if title.endswith(name)), None)
        if match is None:
            continue
        sheet = book[match]
        marks = {}
        for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 400), max_col=2):
            mark = str(row[0].value or "").strip()
            if mark.startswith("1:K"):
                code = mark[3:].strip()
                if code:
                    marks[code] = row[0].row
        if marks:
            result[name] = marks
    return result


# ---------- 生成 ----------

def _category_of(index_no, name):
    from .paper_pack import category
    return category(index_no, name)


def _period_text(subject_label):
    """“主体 / 2025-12” → 期间与截止日。"""
    tail = str(subject_label or "").split("/")[-1].strip()
    year, _, month = tail.partition("-")
    if year.strip().isdigit() and month.strip().isdigit():
        month = int(month)
        return f"{year.strip()}年01月—{year.strip()}年{month:02d}月", f"{year.strip()}年{month:02d}月{31 if month in (1,3,5,7,8,10,12) else 30}日"
    return tail or WAIT, tail or WAIT


def _audit_header(sheet, *, is_pl):
    """两行表头：上行为分组、下行为明细，与鼎信诺审定表一致。"""
    titles = AUDIT_COLUMNS_PL if is_pl else AUDIT_COLUMNS
    groups = ((1, 1, "科目编码"), (2, 2, "项目名称"), (3, 3, "借/贷"),
              (4, 4, titles[3]), (5, 5, titles[4]), (6, 6, titles[5]),
              (7, 8, "账项调整"), (9, 10, "重分类调整"), (11, 11, titles[10]), (12, 12, "索引号"),
              (13, 13, titles[12]), (14, 14, titles[13]), (15, 15, titles[14]),
              (16, 16, titles[15]), (17, 17, titles[16]),
              (18, 19, "本期未审与上期审定比较"), (20, 21, "本期审定与上期审定比较"))
    subs = {7: "借方", 8: "贷方", 9: "借方", 10: "贷方", 18: "变动额", 19: "变动率",
            20: "变动额", 21: "变动率"}
    for first, last, text in groups:
        sheet.merge(7, first, last, text, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL, height=26)
    for column in range(1, len(titles) + 1):
        cell = sheet.ws.cell(row=8, column=column,
                             value=subs.get(column, "" if column in (1, 2, 3, 4, 5, 6, 11, 12,
                                                                     13, 14, 15, 16, 17)
                                            else titles[column - 1]))
        cell.font, cell.alignment, cell.border, cell.fill = HEAD_FONT, CENTER, BOX, HEAD_FILL
    for column in (1, 2, 3, 4, 5, 6, 11, 12, 13, 14, 15, 16, 17):
        sheet.ws.merge_cells(start_row=7, start_column=column, end_row=8, end_column=column)
    sheet.ws.row_dimensions[8].height = 26
    return 9


def _audit_sheet(book, meta, accounts, *, is_pl, index_no):
    columns = len(AUDIT_COLUMNS)
    widths = (12, 26, 6) + (15,) * 8 + (10,) + (15,) * 5 + (14, 9, 14, 9)
    sheet = Sheet(book, f"{index_no} 审定表", widths)
    sheet.header_block(meta, columns=columns, title=f"{meta['name']}审定表",
                       index_no=index_no, page=2)
    row = _audit_header(sheet, is_pl=is_pl)
    first_data = row
    for account in accounts:
        opening = _amount(account.opening) or Decimal("0")
        ending = _amount(account.ending) or Decimal("0")
        current = period_amount(account) if is_pl else ending
        previous = None if is_pl else opening
        change = None if previous is None else current - previous
        rate = None
        if previous not in (None, 0):
            rate = float(change / abs(previous))
        # 账面来源不能证明审定数或“无调整”，这些栏须待独立复核后填写。
        values = (dxn_code(account.code, account.level), account.name, account.direction,
                  current, None, current, None, None, None, None, None, f"{index_no}-1",
                  previous, None, previous, None, None, change, rate, None, None)
        for column, value in enumerate(values, 1):
            money = column in (4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 20)
            sheet.cell(row, column, value, align=CENTER if column in (1, 3) else
                       (LEFT if column == 2 else RIGHT), money=money, percent=column in (19, 21),
                       height=22)
        row += 1
    last = row - 1
    total = row
    sheet.merge(total, 1, 2, "合　　计", font=HEAD_FONT, align=CENTER, fill=TOTAL_FILL, height=22)
    sheet.cell(total, 3, "—", align=CENTER, fill=TOTAL_FILL)
    for column in (4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 20):
        letter = get_column_letter(column)
        formula = (f'=SUMIF($C${first_data}:$C${last},"借",{letter}${first_data}:{letter}${last})'
                   f'-SUMIF($C${first_data}:$C${last},"贷",{letter}${first_data}:{letter}${last})')
        if column not in (4, 6):
            formula = f'=IF(COUNT({letter}{first_data}:{letter}{last})={last-first_data+1},{formula[1:]},"")'
        cell = sheet.cell(total, column, formula, font=HEAD_FONT, align=RIGHT, money=True,
                          fill=TOTAL_FILL, formula=True)
    sheet.cell(total, 12, "—", align=CENTER, fill=TOTAL_FILL)
    for change_column, rate_column, base in ((18, 19, "M"), (20, 21, "Q")):
        change = get_column_letter(change_column)
        sheet.cell(total, rate_column,
                   f'=IF(AND(COUNT({change}{total},{base}{total})=2,{base}{total}<>0),'
                   f'{change}{total}/ABS({base}{total}),"")',
                   align=RIGHT, percent=True, fill=TOTAL_FILL, formula=True)
    row = total + 1
    sheet.merge(row, 1, columns,
                "校验：未审数减审定数（应为 0.00），差异不为零时不得归档。"
                + ("　损益类上期发生额需取自上年账套或报表，本机未取数，相关栏留空。"
                   if is_pl else ""),
                font=SMALL_FONT, height=20)
    row += 1
    sheet.merge(row, 1, 3, "未审减审定", font=SMALL_FONT, align=CENTER, fill=TOTAL_FILL)
    sheet.cell(row, 4, f'=IF(COUNT(F{total},K{total})=2,ROUND(F{total}-K{total},2),"")',
               align=RIGHT, money=True, fill=TOTAL_FILL, formula=True)
    sheet.cell(row, 5, f'=IF(ISNUMBER(D{row}),IF(ABS(D{row})<0.01,"T/B","错"),"待复核")',
               align=CENTER, fill=TOTAL_FILL, formula=True)
    sheet.merge(row, 6, 11, "T/B 表示未审数与审定数一致；存在审计调整时本行应等于调整净额。",
                font=SMALL_FONT)
    row += 1
    sheet.merge(row, 1, 3, "报表数", font=SMALL_FONT, align=CENTER, fill=TOTAL_FILL)
    sheet.merge(row, 4, columns,
                f"{WAIT}（本机未读取财务报表，报表数需与 TB 或报表模块核对后填列）",
                font=SMALL_FONT, fill=TOTAL_FILL, height=20)
    row += 2
    notes = meta.get("audit_notes") or []
    sheet.merge(row, 1, columns, "审计说明", font=HEAD_FONT, height=22)
    for text in notes or [f"{WAIT}：未生成审计说明。"]:
        row += 1
        sheet.merge(row, 1, columns, str(text), font=SMALL_FONT,
                    height=max(20, 15 * (len(str(text)) // 60 + 1)))
    row += 2
    sheet.merge(row, 1, columns, "审计结论（无异常／需调整／需进一步程序）", font=HEAD_FONT,
                height=22)
    row += 1
    conclusion = str(meta.get("conclusion") or "").strip() or default_conclusion(True)
    sheet.merge(row, 1, columns, conclusion, font=SMALL_FONT, height=32)
    sheet.page_setup(8)
    return sheet, total


def _detail_sheet(book, meta, accounts, *, is_pl, index_no, audit_total):
    columns = len(DETAIL_COLUMNS)
    widths = (11, 12, 18, 30, 6) + (14,) * 14 + (10,)
    sheet = Sheet(book, f"{index_no} 明细表", widths)
    row = sheet.header_block(meta, columns=columns, title=f"{meta['name']}账项明细表",
                             index_no=f"{index_no}-1", page=3)
    for column, text in enumerate(DETAIL_COLUMNS, 1):
        sheet.cell(row, column, text, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    sheet.ws.row_dimensions[row].height = 46
    row += 1
    subtotal_rows = []
    for account in accounts:
        lines = detail_rows(meta["rows"], account)
        block = []
        for line in lines:
            opening = _amount(line.opening) or Decimal("0")
            debit = _amount(line.debit) or Decimal("0")
            credit = _amount(line.credit) or Decimal("0")
            ending = _amount(line.ending) or Decimal("0")
            parent = str(line.path).split("/")[-2] if "/" in str(line.path or "") else ""
            values = (f"1:K{dxn_code(line.code, line.level)}", dxn_code(line.code, line.level),
                      f"\\{parent}" if parent else "", line.name, line.direction,
                      opening, None, opening, None, None, debit, credit, None, None,
                      ending, None, ending, None, None, f"{index_no}-1")
            for column, value in enumerate(values, 1):
                money = 6 <= column <= 19
                sheet.cell(row, column, value, align=CENTER if column in (1, 2, 5) else
                           (LEFT if column in (3, 4) else RIGHT), money=money, height=22)
            block.append(row)
            row += 1
        subtotal = row
        sheet.merge(subtotal, 1, 3, f"{account.name}小计", font=HEAD_FONT, align=LEFT,
                    fill=TOTAL_FILL, height=22)
        sheet.cell(subtotal, 4, "—", align=CENTER, fill=TOTAL_FILL)
        sheet.cell(subtotal, 5, account.direction, align=CENTER, fill=TOTAL_FILL)
        for column in range(6, 20):
            letter = get_column_letter(column)
            # 期初（F/H/J）与期末（O/Q/S）按借贷方向取净额，发生额保持原方向
            signed = column in (6, 8, 10, 15, 17, 19)
            span = f"{letter}{block[0]}:{letter}{block[-1]}"
            directions = f"$E${block[0]}:$E${block[-1]}"
            amount = (f'SUMIF({directions},"借",{span})-SUMIF({directions},"贷",{span})'
                      if signed else f'SUM({span})')
            formula = f'=IF(COUNT({span})={len(block)},{amount},"")'
            sheet.cell(subtotal, column, formula, font=HEAD_FONT, align=RIGHT, money=True,
                       fill=TOTAL_FILL, formula=True)
        sheet.cell(subtotal, 20, "—", align=CENTER, fill=TOTAL_FILL)
        subtotal_rows.append(subtotal)
        row += 1
    total = row
    sheet.merge(total, 1, 4, f"{meta['name']}合计", font=HEAD_FONT, align=LEFT, fill=TOTAL_FILL,
                height=22)
    sheet.cell(total, 5, "—", align=CENTER, fill=TOTAL_FILL)
    for column in range(6, 20):
        letter = get_column_letter(column)
        references = [f"{letter}{item}" for item in subtotal_rows]
        formula = f'=IF(COUNT({",".join(references)})={len(references)},{"+".join(references)},"")'
        sheet.cell(total, column, formula, font=HEAD_FONT, align=RIGHT, money=True, fill=TOTAL_FILL,
                   formula=True)
    sheet.cell(total, 20, "—", align=CENTER, fill=TOTAL_FILL)
    row = total + 1
    sheet.merge(row, 1, 4, "与审定表核对（期末审定数）", font=SMALL_FONT, align=LEFT, fill=TOTAL_FILL)
    sheet.cell(row, 5, "—", align=CENTER, fill=TOTAL_FILL)
    audit_name = f"{index_no} 审定表"
    sheet.cell(row, 6, f'=IF(COUNT(S{total},\'{audit_name}\'!K{audit_total})=2,'
               f'ROUND(S{total}-\'{audit_name}\'!K{audit_total},2),"")',
               align=RIGHT, money=True, fill=TOTAL_FILL, formula=True)
    sheet.cell(row, 7, f'=IF(ISNUMBER(F{row}),IF(ABS(F{row})<0.01,"G","错"),"待复核")',
               align=CENTER, fill=TOTAL_FILL, formula=True)
    sheet.merge(row, 8, 19, "G 表示明细表合计与审定表期末审定数一致；错 表示存在差异，须追查。",
                font=SMALL_FONT)
    sheet.cell(row, 20, "—", align=CENTER, fill=TOTAL_FILL)
    row += 2
    sheet.merge(row, 1, columns,
                "取数标记（A 列 1:K<科目编号>）沿用鼎信诺底稿的写法：标记哪个科目，本行即取该科目的"
                "期初、借贷发生额与期末；期初、期末均以本机账套科目余额表为准，报表数需另行核对。",
                font=SMALL_FONT, height=30)
    sheet.page_setup(8)
    return sheet


def _program_sheet(book, meta, procedures, *, index_no):
    columns = 10
    widths = (6, 62, 9, 9, 11, 11, 9, 26, 12, 22)
    sheet = Sheet(book, "审计程序", widths)
    row = sheet.header_block(meta, columns=columns, title=f"{meta['name']}审计程序表",
                             index_no=f"{index_no}-0", page=4)
    for column, text in enumerate(("序号", "审计程序", "存在", "完整性", "权利和义务", "计价和分摊",
                                   "列报", "本项目执行情况", "索引号", "备注"), 1):
        sheet.cell(row, column, text, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    sheet.ws.row_dimensions[row].height = 30
    row += 1
    notes = meta.get("procedure_notes") or []
    last_section = ""
    for number, item in enumerate(procedures, 1):
        marks = {key: ("√" if key in item.assertions else "") for key in
                 ("存在", "完整性", "权利和义务", "计价和分摊", "列报")}
        status = notes[number - 1] if number - 1 < len(notes) else f"{WAIT}（未执行）"
        target = f"{index_no}-2" if item.basis == "-2" else (
            f"{index_no}-{item.basis}" if item.basis else f"{index_no}-1")
        section = ""
        if item.section and item.section != last_section:
            last_section, section = item.section, item.section
        values = (number, item.text, marks["存在"], marks["完整性"], marks["权利和义务"],
                  marks["计价和分摊"], marks["列报"], status, target, section)
        rows_needed = max(len(str(item.text)) // 30 + 1, len(str(status)) // 12 + 1)
        for column, value in enumerate(values, 1):
            sheet.cell(row, column, value, align=CENTER if column not in (2, 8, 10) else LEFT,
                       height=max(30, 14 * rows_needed + 6))
        row += 1
    row += 1
    sheet.merge(row, 1, columns,
                "程序清单按报表项目类别整理，与鼎信诺底稿的「审计程序」表同口径：认定栏勾选与本项目相关的"
                "认定，执行情况只能写已执行／不适用／待补，不得预填结论。", font=SMALL_FONT, height=30)
    sheet.page_setup(8)
    return sheet


def _disclosure_sheet(book, meta, accounts, *, is_pl, index_no, total_row_values):
    columns = 6
    widths = (34, 18, 18, 14, 42, 12)
    sheet = Sheet(book, "披露表", widths)
    row = sheet.header_block(meta, columns=columns, title=f"{meta['name']}披露表",
                             index_no=f"{index_no}-15", page=5)
    head = ("项目", "本期发生额" if is_pl else "期末余额", "上期发生额" if is_pl else "年初余额",
            "与审定数差异", "附注披露说明", "索引号")
    for column, text in enumerate(head, 1):
        sheet.cell(row, column, text, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    sheet.ws.row_dimensions[row].height = 26
    row += 1
    notes = meta.get("disclosure_notes") or []
    mapped = {}
    for entry in notes:
        name = str(entry.get("item", "")).strip()
        if name:
            mapped[name] = entry.get("note", WAIT)
    for account in accounts:
        current = period_amount(account) if is_pl else (_amount(account.ending) or Decimal("0"))
        previous = None if is_pl else (_amount(account.opening) or Decimal("0"))
        note = mapped.get(account.name) or mapped.get(f"{meta['name']}_{account.name}") or WAIT
        values = (f"{meta['name']}_{account.name}", current, previous, None, note,
                  f"{index_no}-1")
        for column, value in enumerate(values, 1):
            sheet.cell(row, column, value, align=LEFT if column in (1, 5) else RIGHT,
                       money=column in (2, 3, 4), height=22)
        row += 1
    sheet.cell(row, 1, "合　　计", font=HEAD_FONT, align=LEFT, fill=TOTAL_FILL, height=22)
    for column, value in ((2, total_row_values.get("current")), (3, total_row_values.get("previous"))):
        sheet.cell(row, column, value, font=HEAD_FONT, align=RIGHT, money=True, fill=TOTAL_FILL)
    sheet.cell(row, 4, None, fill=TOTAL_FILL)
    sheet.cell(row, 5, "—", align=CENTER, fill=TOTAL_FILL)
    sheet.cell(row, 6, "—", align=CENTER, fill=TOTAL_FILL)
    sheet.page_setup(8)
    return sheet


def _metadata_sheet(book, meta, index_no):
    """鼎信诺底稿的 dxnsjtempsheet：表头信息集中存放，便于互认与复核。"""
    sheet = book.create_sheet("dxnsjtempsheet")
    sheet.sheet_view.showGridLines = False
    sheet["A1"] = "---表名---\ndxnsjtempsheet\n---end---"
    sheet["C1"] = "start"
    sheet["A2"] = "temp"
    cells = sheet["B2"]
    cells.value = "底稿编号"
    sheet["C2"] = index_no.split("-")[0]
    rows = (
        ("单位名称", meta["project"]),
        ("项目名称", meta["name"]),
        ("会计年度期初", meta["period"][:4] if meta["period"][:4].isdigit() else ""),
        ("会计期间期初", 1),
        ("会计年度期末", meta["period"][:4] if meta["period"][:4].isdigit() else ""),
        ("会计期间期末", meta["date"][:0] or meta["cutoff"]),
        ("编制人", meta["prepared_by"]),
        ("编制日期", meta["date"]),
        ("复核人", meta["reviewed_by"]),
        ("复核日期", meta["date"]),
        ("索引号", index_no),
        ("会计期间", meta["period"]),
        ("截止日", meta["cutoff"]),
        ("审计年度", meta["period"][:4] if meta["period"][:4].isdigit() else ""),
        ("会计制度", meta["standard"]),
    )
    for offset, (key, value) in enumerate(rows, start=3):
        sheet.cell(row=offset, column=2, value=key)
        cell = sheet.cell(row=offset, column=3, value=value)
        if isinstance(value, str):
            cell.data_type = "s"
    sheet.column_dimensions["B"].width = 18
    sheet.column_dimensions["C"].width = 42
    return sheet


def _review_sheet(book, meta, checks, *, index_no, conclusion):
    """一级复核自检清单：格式、索引与勾稽的自检，不替代专业判断。"""
    columns = 4
    widths = (6, 48, 16, 40)
    sheet = Sheet(book, f"9720-{index_no} 复核自检清单", widths)
    row = sheet.header_block(meta, columns=columns, title=f"{meta['name']}复核自检清单",
                             index_no=f"9720-{index_no}", page=meta["review_page"])
    for column, text in enumerate(("序号", "复核要点", "自检结果", "说明"), 1):
        sheet.cell(row, column, text, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    sheet.ws.row_dimensions[row].height = 26
    row += 1
    for number, (text, result, note) in enumerate(checks, 1):
        for column, value in enumerate((number, text, result, note), 1):
            sheet.cell(row, column, value, align=LEFT if column in (2, 4) else CENTER,
                       height=max(24, 15 * (len(text) // 40 + 1)))
        row += 1
    row += 1
    sheet.merge(row, 1, 4, f"复核结论：{conclusion or '待项目合伙人复核后填写。'}", font=HEAD_FONT,
                height=24)
    sheet.page_setup()
    return sheet


def _extra_sheet(book, meta, spec, *, index_no, page, rows_data=()):
    """按列式生成专项表：能推出来的先填，其余留空待现场。"""
    title, columns, note, blank_rows = spec[:4]
    sheet = Sheet(book, title[:31], tuple(width for _name, width, _kind in columns))
    row = sheet.header_block(meta, columns=len(columns), title=f"{meta['name']}{title}",
                             index_no=f"{index_no}-{page}", page=page)
    for column, (name, _width, _kind) in enumerate(columns, 1):
        sheet.cell(row, column, name, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    sheet.ws.row_dimensions[row].height = 30
    row += 1
    for values in rows_data:
        lines = 1
        for column, (_name, width, kind) in enumerate(columns, 1):
            value = values[column - 1] if column - 1 < len(values) else None
            if kind == TEXT and value:
                lines = max(lines, len(str(value)) // max(6, int(width / 2)) + 1)
            sheet.cell(row, column, value,
                       align=RIGHT if kind in (MONEY, NUM) else
                       (LEFT if kind == TEXT and column in (2, 4) else CENTER),
                       money=kind == MONEY, height=max(22, 14 * lines + 6))
        row += 1
    for _ in range(max(blank_rows - len(rows_data), 0)):
        for column, (_name, _width, kind) in enumerate(columns, 1):
            sheet.cell(row, column, None,
                       align=RIGHT if kind in (MONEY, NUM) else CENTER,
                       money=kind == MONEY, height=22)
        row += 1
    row += 1
    sheet.merge(row, 1, len(columns), f"填表说明：{note}", font=SMALL_FONT, height=24)
    sheet.merge(row + 1, 1, len(columns),
                "本表已按科目余额表预填可得信息，空白处须依据取得的资料或原始凭证填写；"
                "资料未取得前程序执行情况记「待补」。",
                font=SMALL_FONT, fill=TOTAL_FILL, height=22)
    sheet.page_setup(8)
    return sheet


def _self_checks(consistent, gap, picks, prepared_by, reviewed_by):
    """复核自检项：只看格式、索引与勾稽，不评价证据是否充分。"""
    return (
        ("底稿目录、导引表与各工作表名称一致", "已核对", "生成时按同一索引号命名"),
        ("明细表合计与科目余额表逐栏一致",
         "已核对" if consistent else "不一致，待追查", f"差异 {gap:,.2f} 元"),
        ("导引表金额与审定表合计一致", "已核对", "同一取数口径"),
        ("抽凭样本已按风险抽取并写明理由",
         f"已抽取 {len(picks)} 笔" if picks else "未读取序时账",
         "检查结论须核对原始凭证后填写"),
        ("编制人、复核人已填列",
         "已填列" if prepared_by and reviewed_by else "待补", "由工作台签字人带入"),
        ("待补资料已取得并归档", "待复核", "见资料需求清单"),
        ("审计调整已由项目组判断", "待复核", "本册不自动填调整"),
        ("审计说明与结论已复核", "待复核", "基础口径需注册会计师确认"),
    )


def _catalog(book, meta, index_no, entries):
    columns = 6
    sheet = Sheet(book, "底稿目录", (6, 14, 36, 30, 8, 30))
    row = sheet.header_block(meta, columns=columns, title=f"{meta['name']}实质性底稿目录",
                             index_no=index_no, page=1)
    for column, text in enumerate(("序号", "索引号", "底稿名称", "对应工作表名称", "页次", "备注"), 1):
        sheet.cell(row, column, text, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    row += 1
    for number, (code, title, sheet_name, note) in enumerate(entries, 1):
        for column, value in enumerate((number, code, title, sheet_name, number + 1, note), 1):
            sheet.cell(row, column, value, align=LEFT if column in (3, 4, 6) else CENTER, height=22)
        row += 1
    sheet.merge(row, 1, 2, "合计", font=HEAD_FONT, align=CENTER, fill=TOTAL_FILL, height=22)
    sheet.cell(row, 3, f"共 {len(entries)} 份底稿", align=LEFT, fill=TOTAL_FILL)
    for column in (4, 5, 6):
        sheet.cell(row, column, "—", align=CENTER, fill=TOTAL_FILL)
    row += 2
    for text in (
            "1、本目录与工作簿中同名工作表一一对应，索引号沿用《审计业务索引号及底稿名称清单》；",
            "2、审定表与明细表的取数标记沿用鼎信诺底稿写法（A 列 1:K<科目编号>）；",
            "3、期初数、期末数、借贷发生额全部取自本机账套科目余额表，未取数的一律写“待补”；",
            "4、报表数、上期数需与 TB 或上年账套核对后填列，本册不代为确定；",
            "5、本册为草稿，未经注册会计师复核不得归档。"):
        sheet.merge(row, 1, columns, text, font=SMALL_FONT, height=22)
        row += 1
    sheet.page_setup()
    return sheet


def build(path, *, paper, subject_label, rows, resolve, prepared_by="", reviewed_by="",
          when="", procedures=(), narrative=None, standard="企业会计准则（一般企业）",
          accounts=None, full=True, journal=None, sampler=None,
          sheet_add=(), sheet_drop=()):
    """生成鼎信诺式底稿册：目录、审定表、明细表、审计程序、披露表。

    ``resolve(code, name)`` 返回本项目匹配的 ``Paper``（可为 None）；``accounts`` 缺省时按报表项目自动归集。
    """
    if not rows:
        raise ValueError("请先读取科目余额表。")
    if paper is None or not getattr(paper, "index_no", ""):
        raise ValueError("未确认报表项目索引，不能归集生成底稿。")
    if len({(row.subject_id, row.year, row.month, row.ledger_id, row.basis) for row in rows}) != 1:
        raise ValueError("科目数据包含不同账套、主体、期间或来源口径，不能混合导出。")
    by_code = {}
    for row in rows:
        if row.code in by_code and by_code[row.code] != row:
            raise ValueError(f"科目编码 {row.code} 存在冲突，需人工确认来源。")
        by_code[row.code] = row
    rows = list(by_code.values())
    period, cutoff = _period_text(subject_label)
    project = str(subject_label or "").split("/")[0].strip() or WAIT
    index_no = getattr(paper, "index_no", "") or WAIT
    name = getattr(paper, "name", "") or "未匹配报表项目"
    grouped = accounts if accounts is not None else group_accounts(rows, paper, resolve)
    if not grouped:
        raise ValueError("该报表项目没有可列示的一级科目，请人工确认映射。")
    valid_accounts = group_accounts(rows, paper, resolve)
    if any(account not in valid_accounts for account in grouped):
        raise ValueError("指定科目不属于当前账套的已确认报表项目。")
    for account in grouped:
        for line in [account, *detail_rows(rows, account)]:
            if line.direction not in ("借", "贷"):
                raise ValueError(f"科目 {line.code} 缺少明确的借贷方向。")
            if any(_amount(getattr(line, key)) is None for key in ("opening", "debit", "credit", "ending")):
                raise ValueError(f"科目 {line.code} 金额不完整，不能以零代替缺少的数据。")
    is_pl = bool(getattr(grouped[0], "is_profit_loss", False)) or index_no.startswith(("4-", "5-"))
    narrative = narrative or {}
    materials = paper_materials.for_paper(index_no, name)
    pending = paper_materials.pending(materials)
    consistent, gap = consistency(grouped, rows)
    total = totals(grouped)
    notes = narrative.get("guide_notes") or baseline_notes(
        name=name, accounts=grouped, total=total, is_pl=is_pl, consistent=consistent,
        gap=gap, pending=pending)
    conclusion = narrative.get("conclusion") or default_conclusion(consistent, pending)
    meta = {
        "project": project, "name": name, "period": period, "cutoff": cutoff,
        "prepared_by": prepared_by, "reviewed_by": reviewed_by,
        "date": when or WAIT, "standard": standard, "rows": rows,
        "source": "金额来源：本机账套（科目余额表）",
        "audit_notes": notes,
        "procedure_notes": narrative.get("procedure_notes") or [],
        "disclosure_notes": narrative.get("disclosure_notes") or [],
        "conclusion": conclusion,
    }
    book = Workbook()
    book.remove(book.active)
    entries = [
        (index_no, f"{name}审定表", f"{index_no} 审定表", "账套取数，含上期数与变动分析"),
        (f"{index_no}-1", f"{name}账项明细表", f"{index_no} 明细表", "取数标记 1:K<科目编号>"),
        (f"{index_no}-0", f"{name}审计程序表", "审计程序", f"共 {len(procedures)} 条程序"),
        (f"{index_no}-15", f"{name}披露表", "披露表", "与审定数勾稽"),
    ]
    sheet_state = paper_sheets.facts(rows, journal)
    leaves = [line for account in grouped for line in detail_rows(rows, account)]
    extras = list(paper_sheets.for_category(_category_of(index_no, name), sheet_state)) if full else []
    if sheet_drop:
        extras = [spec for spec in extras if spec[0] not in set(sheet_drop)]
    for title in sheet_add or ():
        spec = paper_sheets.by_name(title)
        if spec and all(spec[0] != item[0] for item in extras):
            extras.append(spec)
    extras = tuple(extras)
    for offset, spec in enumerate(extras, 8):
        entries.append((f"{index_no}-{offset}", f"{name}{spec[0]}", spec[0][:31],
                        f"{len(spec[1])} 列 × {spec[3]} 行，待资料填写"))
    entries.append((f"9720-{index_no}", f"{name}复核自检清单",
                    f"9720-{index_no} 复核自检清单", "格式、索引与勾稽自检"))
    _metadata_sheet(book, meta, index_no)
    _catalog(book, meta, index_no, entries)
    audit, audit_total = _audit_sheet(book, meta, grouped, is_pl=is_pl, index_no=index_no)
    _detail_sheet(book, meta, grouped, is_pl=is_pl, index_no=index_no, audit_total=audit_total)
    _program_sheet(book, meta, procedures, index_no=index_no)
    current_total = sum((((period_amount(row) if is_pl else _amount(row.ending))
                         * (-1 if row.direction == "贷" else 1))
                         for row in grouped), Decimal("0"))
    previous_total = None if is_pl else total["opening"]
    _disclosure_sheet(book, meta, grouped, is_pl=is_pl, index_no=index_no,
                      total_row_values={"current": current_total, "previous": previous_total})
    picks, pick_source = [], ""
    if journal:
        if callable(sampler):
            picks, pick_source = sampler(name, journal)
        else:
            picks, pick_source = sampling.pick(journal), "规则抽样"
    for offset, spec in enumerate(extras, 8):
        title = spec[0]
        if title == "抽凭表":
            data = [(number, str(getattr(row, "date", "")), row.voucher, row.summary, row.code,
                     row.contra, row.debit, row.credit, reason, None)
                    for number, (row, reason) in enumerate(picks, 1)]
        else:
            data = paper_sheets.prefill(title, sheet_state, leaves)
        _extra_sheet(book, meta, spec, index_no=index_no, page=offset, rows_data=data)
    if pick_source:
        meta["pick_source"] = pick_source
    meta["review_page"] = 8 + len(extras)
    _review_sheet(book, meta, _self_checks(consistent, gap, picks, prepared_by, reviewed_by),
                  index_no=index_no, conclusion=conclusion)
    target = Path(path)
    book.save(target)
    return target


# ---------- 整套生成 ----------

SUMMARY_NAME = "00 底稿总目录与待补资料.xlsx"


def build_all(folder, *, subject_label, rows, resolve, papers=(), prepared_by="", reviewed_by="",
              when="", procedures_for=None, journals=None, sampler=None):
    """按报表项目批量生成整套底稿，并输出总目录与待补资料汇总。

    只为本次账套里真实存在的科目出册；空科目跳过，避免生成大量空表。
    返回 {"files": [...], "summary": 汇总文件路径, "papers": 生成册数}。
    """
    target_dir = Path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    produced, pending_rows = [], []
    seen_papers = set()
    for paper in papers:
        index_no = getattr(paper, "index_no", "") or WAIT
        if index_no in seen_papers:            # 同一报表项目只出一次
            continue
        accounts = group_accounts(rows, paper, resolve)
        if not accounts:
            continue
        seen_papers.add(index_no)
        name = getattr(paper, "name", "") or "未匹配"
        path = target_dir / f"{index_no} {name}.xlsx"
        procedures = procedures_for(index_no, name) if procedures_for else ()
        scoped = []
        for account in accounts:
            scoped.extend((journals or {}).get(account.code) or [])
        build(path, paper=paper, subject_label=subject_label, rows=rows, resolve=resolve,
              prepared_by=prepared_by, reviewed_by=reviewed_by, when=when, procedures=procedures,
              accounts=accounts, journal=scoped or None, sampler=sampler)
        materials = paper_materials.for_paper(index_no, name)
        pending = paper_materials.pending(materials)
        total = totals(accounts)
        produced.append({
            "index_no": index_no, "name": name, "accounts": len(accounts),
            "ending": total["ending"], "period": total["period"],
            "is_pl": bool(getattr(accounts[0], "is_profit_loss", False))
            or index_no.startswith(("4-", "5-")),
            "programs": len(procedures), "pending": len(pending), "file": path.name,
            "sheets": 7 + len(paper_sheets.for_category(_category_of(index_no, name))),
        })
        for item in materials:
            if not item["ready"]:
                pending_rows.append({"index_no": index_no, "name": name, "file": path.name,
                                     "material": item["name"], "source": item["source"],
                                     "usage": item["usage"]})
    summary = _summary_book(target_dir / SUMMARY_NAME, subject_label, produced, pending_rows,
                            prepared_by=prepared_by, reviewed_by=reviewed_by, when=when)
    return {"files": [item["file"] for item in produced], "summary": summary,
            "papers": len(produced), "pending": len(pending_rows)}


def _summary_book(path, subject_label, produced, pending_rows, *, prepared_by="", reviewed_by="",
                  when=""):
    """总目录 + 待补资料汇总：一页看清做了哪些底稿、还缺什么。"""
    project = str(subject_label or "").split("/")[0].strip() or WAIT
    period, cutoff = _period_text(subject_label)
    book = Workbook()
    book.remove(book.active)

    catalog = Sheet(book, "底稿总目录", (6, 12, 26, 8, 18, 8, 10, 8, 30))
    row = catalog.header_block({"project": project, "name": "全部实质性底稿", "period": period,
                                "cutoff": cutoff, "prepared_by": prepared_by,
                                "reviewed_by": reviewed_by, "date": when or WAIT,
                                "standard": "企业会计准则（一般企业）",
                                "source": "金额来源：本机账套（科目余额表）"},
                               columns=9, title="实质性底稿总目录", index_no="00", page=1)
    for column, text in enumerate(("序号", "索引号", "底稿名称", "科目数", "期末余额／本期发生额",
                                   "程序数", "待补资料", "工作表数", "文件"), 1):
        catalog.cell(row, column, text, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    catalog.ws.row_dimensions[row].height = 26
    row += 1
    for number, item in enumerate(produced, 1):
        amount_value = item["period"] if item["is_pl"] else item["ending"]
        values = (number, item["index_no"], item["name"], item["accounts"], amount_value,
                  item["programs"], item["pending"], item["sheets"], item["file"])
        for column, value in enumerate(values, 1):
            catalog.cell(row, column, value, align=LEFT if column in (3, 9) else CENTER,
                         money=column == 5, height=22)
        row += 1
    catalog.merge(row, 1, 3, "合计", font=HEAD_FONT, align=CENTER, fill=TOTAL_FILL, height=22)
    catalog.cell(row, 4, sum(item["accounts"] for item in produced), font=HEAD_FONT,
                 align=CENTER, fill=TOTAL_FILL)
    catalog.cell(row, 5, "—（各科目口径不同，不作合计）", font=SMALL_FONT, align=LEFT,
                 fill=TOTAL_FILL)
    catalog.cell(row, 6, sum(item["programs"] for item in produced), font=HEAD_FONT,
                 align=CENTER, fill=TOTAL_FILL)
    catalog.cell(row, 7, len(pending_rows), font=HEAD_FONT, align=CENTER, fill=TOTAL_FILL)
    catalog.cell(row, 8, sum(item["sheets"] for item in produced), font=HEAD_FONT,
                 align=CENTER, fill=TOTAL_FILL)
    catalog.cell(row, 9, f"{len(produced)} 册", font=HEAD_FONT, align=LEFT, fill=TOTAL_FILL)

    pending_sheet = Sheet(book, "待补资料汇总", (6, 12, 24, 34, 14, 30, 26))
    row = pending_sheet.header_block({"project": project, "name": "待补资料汇总", "period": period,
                                      "cutoff": cutoff, "prepared_by": prepared_by,
                                      "reviewed_by": reviewed_by, "date": when or WAIT,
                                      "standard": "企业会计准则（一般企业）",
                                      "source": "按科目汇总，取得后回填各册资料需求清单"},
                                     columns=7, title="待补资料汇总", index_no="00-9", page=2)
    for column, text in enumerate(("序号", "索引号", "底稿名称", "资料名称", "提供方", "用途／对应程序",
                                   "备注"), 1):
        pending_sheet.cell(row, column, text, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    row += 1
    for number, item in enumerate(pending_rows, 1):
        values = (number, item["index_no"], item["name"], item["material"], item["source"],
                  item["usage"], "取得后回填并复核")
        for column, value in enumerate(values, 1):
            pending_sheet.cell(row, column, value, align=LEFT if column in (3, 4, 6) else CENTER,
                               height=22)
        row += 1
    pending_sheet.merge(row, 1, 7,
                        f"共 {len(pending_rows)} 项待补资料，涉及 {len(produced)} 册底稿。"
                        "资料到齐前，各册结论保持「待定」，程序执行情况保持「待补」。",
                        font=SMALL_FONT, fill=TOTAL_FILL, height=26)

    note = Sheet(book, "使用说明", (80,))
    note.merge(1, 1, 1, "使用说明", font=TITLE_FONT, align=CENTER, height=28)
    for offset, text in enumerate((
            "1、本工作簿汇总本次生成的全部实质性底稿：总目录列示每册的索引号、科目数、期末余额、程序数与待补资料数。",
            "2、「待补资料汇总」把所有册子的待补资料集中列出，可直接作为向被审计单位索要资料的清单。",
            "3、各册底稿中的审定表、明细表金额全部取自本机账套科目余额表；审计说明与结论为基础口径，需项目组复核。",
            "4、资料到齐后，在对应底稿的「资料需求清单」把「是否取得」改为已取得，并补充程序执行情况与结论。",
            "5、本套底稿为草稿，未经注册会计师复核不得归档。"), 2):
        note.merge(offset + 2, 1, 1, text, font=SMALL_FONT, align=LEFT, height=22)
    book.save(path)
    return path
