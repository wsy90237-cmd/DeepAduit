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

from . import audit_index, dxn_templates, paper_materials, paper_sheets, sampling
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
AUDIT_COLUMNS_PL = ("科目编码", "项目名称", "借/贷", "本期账面发生额", "本期账面调整数", "本期未审发生额",
                    "账项调整（借）", "账项调整（贷）", "重分类调整（借）", "重分类调整（贷）",
                    "本期审定发生额", "索引号", "上期账面发生额", "上期账面调整数", "上期未审发生额",
                    "上期调整数", "上期审定发生额", "变动额", "变动率", "本期审定与上期比较（变动额）",
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
        if money and (formula or isinstance(value, (int, float, Decimal))) and not isinstance(value, bool):
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
        self.ws.row_dimensions[2].height = 30
        self.ws.row_dimensions[3].height = 24
        self.ws.row_dimensions[4].height = 24
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
    if paper is not None:
        for component in audit_index.reserve_components(rows, paper):
            if component.code not in seen:
                picked.append(component)
                seen.add(component.code)
    picked.sort(key=lambda item: item.code)
    return picked


def detail_rows(rows, account):
    """只列直接子级，避免多层汇总与明细重复计数；无子级时列本科目。"""
    children = [row for row in rows if account.path and row.level == account.level + 1
                and row.path.rpartition("/")[0] == account.path]
    if children:
        return sorted(children, key=lambda item: item.code)
    return [account]


def consistency(accounts, rows, *, is_pl=None):
    """损益按实际导出的层级逐级核对，余额核对直接子级；缺失值不能充当零。"""
    gap = Decimal("0")
    missing = False
    for account in accounts:
        occurrence = bool(getattr(account, "is_profit_loss", False)) if is_pl is None else is_pl
        pending = [account]
        while pending:
            parent_row = pending.pop()
            if occurrence and parent_row.period_amount is None:
                missing = True
            lines = detail_rows(rows, parent_row)
            if len(lines) == 1 and lines[0] is parent_row:
                continue
            if occurrence:
                pending.extend(lines)
            keys = (("period_amount", "previous_period_amount") if parent_row.previous_period_amount is not None else
                    ("period_amount",)) if occurrence else ("opening", "debit", "credit", "ending")
            for key in keys:
                values = [_amount(getattr(line, key)) for line in [parent_row, *lines]]
                if any(value is None for value in values):
                    if occurrence:
                        missing = True
                        continue
                    raise ValueError(f"科目 {parent_row.code} 的 {key} 金额不完整，不能完成勾稽。")
                signed = key not in ("debit", "credit")
                parent = values[0] * (-1 if signed and parent_row.direction == "贷" else 1)
                child = sum((value * (-1 if signed and line.direction == "贷" else 1)
                             for line, value in zip(lines, values[1:])), Decimal("0"))
                gap += abs(parent - child)
    if missing and gap <= Decimal("0.005"):
        return None, None
    return abs(gap) <= Decimal("0.005"), gap


def presentation_direction(paper, accounts):
    """对应原模板“报表项目借贷”乘数，备抵科目不能改变资产项目的列示方向。"""
    index = str(getattr(paper, "index_no", ""))
    if index.startswith("1-"):
        return "借"
    if index.startswith(("2-", "4-")):
        return "贷"
    name = str(getattr(paper, "name", ""))
    if index.startswith("5-"):
        if any(word in name for word in ("费用", "成本", "支出", "损失")):
            return "借"
        if any(word in name for word in ("收益", "收入")):
            return "贷"
    directions = {account.direction for account in accounts}
    if len(directions) == 1 and directions <= {"借", "贷"}:
        return directions.pop()
    raise ValueError("报表项目缺少明确的列示方向，请先核对科目归属。")


def totals(accounts, direction="借"):
    """余额与发生额按报表方向净额合计；缺少任一来源值时保留未知。"""
    result = {}
    for key in ("opening", "debit", "credit", "ending", "period", "previous_period"):
        values = []
        for account in accounts:
            value = (period_amount(account) if key == "period" else
                     _amount(getattr(account, "previous_period_amount", None)) if key == "previous_period" else
                     _amount(getattr(account, key)))
            sign = -1 if key not in ("debit", "credit") and account.direction != direction else 1
            values.append(None if value is None else value * sign)
        result[key] = None if any(value is None for value in values) else sum(values, Decimal("0"))
    return result


def period_amount(row):
    """只取独立的发生额字段，不以借贷单边或结转后的余额代替。"""
    return _amount(getattr(row, "period_amount", None))


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
    widths = (12, 26, 6) + (20,) * 8 + (12,) + (20,) * 5 + (20, 12, 20, 12)
    sheet = Sheet(book, f"{index_no} 审定表", widths)
    sheet.header_block(meta, columns=columns, title=f"{meta['name']}审定表",
                       index_no=index_no, page=2)
    row = _audit_header(sheet, is_pl=is_pl)
    first_data = row
    for account in accounts:
        opening = _amount(account.opening)
        ending = _amount(account.ending)
        current = period_amount(account) if is_pl else ending
        previous = _amount(getattr(account, "previous_period_amount", None)) if is_pl else opening
        # 本期或上期任一方缺失即保留未知：不把缺数当零，也不由已知一方推算差额与变动率。
        change = None if current is None or previous is None else current - previous
        rate = None
        if change is not None and previous not in (None, 0):
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
    direction = meta["presentation_direction"]
    opposite = "贷" if direction == "借" else "借"
    for column in (4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 20):
        letter = get_column_letter(column)
        formula = (f'=SUM({letter}{first_data}:{letter}{last})' if column in (7, 8, 9, 10) else
                   f'=SUMIF($C${first_data}:$C${last},"{direction}",{letter}${first_data}:{letter}${last})'
                   f'-SUMIF($C${first_data}:$C${last},"{opposite}",{letter}${first_data}:{letter}${last})')
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
                f"衔接核对：审定数应等于未审数{'加借方调整、减贷方调整' if meta['presentation_direction'] == '借' else '减借方调整、加贷方调整'}（含重分类）。缺少审定或调整依据时保持待补。"
                + ("　损益类上期发生额仅使用独立的上期发生额来源，缺少时留空。"
                   if is_pl else ""),
                font=SMALL_FONT, height=20)
    row += 1
    sheet.merge(row, 1, 3, "审定数与调整衔接差异", font=SMALL_FONT, align=CENTER, fill=TOTAL_FILL)
    signs = "-G{t}+H{t}-I{t}+J{t}" if meta["presentation_direction"] == "借" else "+G{t}-H{t}+I{t}-J{t}"
    sheet.cell(row, 4, f'=IF(COUNT(F{total}:K{total})=6,ROUND(K{total}-F{total}{signs.format(t=total)},2),"")',
               align=RIGHT, money=True, fill=TOTAL_FILL, formula=True)
    sheet.cell(row, 5, f'=IF(ISNUMBER(D{row}),IF(ABS(D{row})<0.01,"T/B","错"),"待复核")',
               align=CENTER, fill=TOTAL_FILL, formula=True)
    sheet.merge(row, 6, 11, "T/B 仅表示审定数与未审数及已填调整衔接相符，不要求审定数必须等于未审数。",
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


def _leaf_rows(rows, account):
    children = detail_rows(rows, account)
    return [account] if children == [account] else [leaf for child in children for leaf in _leaf_rows(rows, child)]


def _book_detail_rows(meta, account):
    auxiliary = [row for row in meta.get("auxiliary", ()) if row.account_code == account.code]
    if auxiliary:
        return sorted(auxiliary, key=lambda row: (row.name, row.code))
    children = detail_rows(meta["rows"], account)
    if children == [account]:
        return children
    codes = {row.account_code for row in meta.get("auxiliary", ())}
    if any(line.code in codes for line in meta["rows"]
           if account.path and line.path.startswith(account.path + "/")):
        return [line for child in children for line in _book_detail_rows(meta, child)]
    return children


def _period_detail_sheet(book, meta, accounts, *, index_no, audit_total):
    titles = ("取数标记", "科目编号", "上级科目名称", "项目名称", "借/贷",
              *(f"{month}月发生额" for month in range(1, 13)),
              "本期账面发生额", "本期未审发生额", "本期调整", "本期审定发生额",
              "上期账面发生额", "上期未审发生额", "上期调整", "上期审定发生额",
              "变动额", "变动率", "索引号", "月度合计", "月度与本期差异", "月度核对")
    sheet = Sheet(book, f"{index_no} 明细表", (14, 16, 28, 28, 8) + (19,) * 26)
    row = sheet.header_block(meta, columns=len(titles), title=f"{meta['name']}发生额明细表",
                             index_no=f"{index_no}-1", page=3)
    for column, title in enumerate(titles, 1):
        sheet.cell(row, column, title, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    sheet.ws.row_dimensions[row].height = 42
    row += 1
    totals_rows = []
    cutoff = meta["rows"][0].month
    for account in accounts:
        first = row
        for line in _leaf_rows(meta["rows"], account):
            months = tuple(getattr(line, "monthly_amounts", ()))
            months = months if len(months) == 12 else (None,) * 12
            current, previous = period_amount(line), _amount(getattr(line, "previous_period_amount", None))
            values = (f"1:K{dxn_code(line.code, line.level)}", dxn_code(line.code, line.level),
                      line.path.rpartition("/")[0], line.name, line.direction, *months,
                      current, current, None, None, previous, previous, None, None,
                      None, None, f"{index_no}-1", None, None, None)
            for column, value in enumerate(values, 1):
                sheet.cell(row, column, value, money=6 <= column <= 30 and column not in (27, 28),
                           align=LEFT if column in (1, 2, 3, 4, 28, 31) else RIGHT, height=22)
            sheet.cell(row, 26, f'=IF(COUNT(R{row},V{row})=2,R{row}-V{row},"")', money=True, formula=True)
            sheet.cell(row, 27, f'=IF(AND(COUNT(Z{row},V{row})=2,V{row}<>0),Z{row}/ABS(V{row}),"")',
                       percent=True, formula=True)
            if 1 <= cutoff <= 12:
                last_month = get_column_letter(5 + cutoff)
                sheet.cell(row, 29, f'=IF(COUNT(F{row}:{last_month}{row})={cutoff},SUM(F{row}:{last_month}{row}),"")',
                           money=True, formula=True)
            sheet.cell(row, 30, f'=IF(COUNT(AC{row},R{row})=2,ROUND(AC{row}-R{row},2),"")', money=True, formula=True)
            sheet.cell(row, 31, f'=IF(ISNUMBER(AD{row}),IF(ABS(AD{row})<0.005,"相符","差异"),"待补发生额或月份")', formula=True)
            row += 1
        last = row - 1
        sheet.merge(row, 1, 4, f"{account.name}小计", font=HEAD_FONT, fill=TOTAL_FILL)
        sheet.cell(row, 5, account.direction, fill=TOTAL_FILL)
        opposite = "贷" if account.direction == "借" else "借"
        for column in (*range(6, 27), 29, 30):
            letter = get_column_letter(column)
            span = f"{letter}{first}:{letter}{last}"
            formula = (f'=IF(COUNT({span})={last-first+1},SUMIF($E${first}:$E${last},"{account.direction}",{span})'
                       f'-SUMIF($E${first}:$E${last},"{opposite}",{span}),"")')
            sheet.cell(row, column, formula, money=True, formula=True, fill=TOTAL_FILL)
        totals_rows.append(row)
        row += 1
    total = row
    sheet.merge(total, 1, 5, f"{meta['name']}合计", font=HEAD_FONT, fill=TOTAL_FILL)
    for column in (*range(6, 27), 29, 30):
        letter = get_column_letter(column)
        refs = [f"{letter}{number}" for number in totals_rows]
        terms = [("+" if account.direction == meta["presentation_direction"] else "-") + ref
                 for account, ref in zip(accounts, refs)]
        sheet.cell(total, column, f'=IF(COUNT({",".join(refs)})={len(refs)},0{"".join(terms)},"")',
                   money=True, formula=True, fill=TOTAL_FILL)
    sheet.cell(total, 27, f'=IF(AND(COUNT(Z{total},V{total})=2,V{total}<>0),Z{total}/ABS(V{total}),"")',
               percent=True, formula=True, fill=TOTAL_FILL)
    audit_name = f"{index_no} 审定表"
    for offset, (label, column, audit_column, pending) in enumerate((
            ("本期未审发生额", "S", "F", "待补发生额依据"),
            ("本期审定发生额", "U", "K", "待补审定依据")), 1):
        row = total + offset
        sheet.merge(row, 1, 4, f"与审定表核对（{label}）", fill=TOTAL_FILL)
        sheet.cell(row, 6, f'=IF(COUNT({column}{total},\'{audit_name}\'!{audit_column}{audit_total})=2,'
                   f'ROUND({column}{total}-\'{audit_name}\'!{audit_column}{audit_total},2),"")', money=True, formula=True)
        sheet.cell(row, 7, f'=IF(ISNUMBER(F{row}),IF(ABS(F{row})<0.005,"G","差异"),"{pending}")', formula=True)
    sheet.merge(total + 4, 1, len(titles),
                "月度金额与本期、上期发生额均取自账套独立发生额字段，不使用借贷累计额或结转余额代替。"
                "截止月份后的列保持空白，缺月不按零补齐；审定数与调整留待独立依据。",
                font=SMALL_FONT, height=34)
    sheet.page_setup(7)
    return sheet


def _detail_sheet(book, meta, accounts, *, is_pl, index_no, audit_total):
    if is_pl:
        return _period_detail_sheet(book, meta, accounts, index_no=index_no, audit_total=audit_total)
    columns = len(DETAIL_COLUMNS)
    widths = (13, 14, 20, 30, 6) + (20,) * 14 + (12,)
    sheet = Sheet(book, f"{index_no} 明细表", widths)
    row = sheet.header_block(meta, columns=columns, title=f"{meta['name']}账项明细表",
                             index_no=f"{index_no}-1", page=3)
    for column, text in enumerate(DETAIL_COLUMNS, 1):
        sheet.cell(row, column, text, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    sheet.ws.row_dimensions[row].height = 46
    row += 1
    subtotal_rows = []
    for account in accounts:
        lines = _book_detail_rows(meta, account)
        block = []
        for line in lines:
            opening, debit, credit, ending = (_amount(getattr(line, field)) for field in ("opening", "debit", "credit", "ending"))
            if hasattr(line, "account_code"):
                code = line.account_code
                source = next(item for item in meta["rows"] if item.code == code)
                marker = f"辅助核算:{code}/{line.type_code}/{line.code}"
                parent = f"{line.account_name} / {line.type_name}"
                display_code = f"{dxn_code(code, source.level)} / {line.code}"
            else:
                marker = f"1:K{dxn_code(line.code, line.level)}"
                parent = line.path.rpartition("/")[0]
                display_code = dxn_code(line.code, line.level)
            values = (marker, display_code,
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
            opposite = "贷" if account.direction == "借" else "借"
            amount = (f'SUMIF({directions},"{account.direction}",{span})-SUMIF({directions},"{opposite}",{span})'
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
        signed = column in (6, 8, 10, 15, 17, 19)
        terms = [("-" if signed and account.direction != meta["presentation_direction"] else "+") + reference
                 for account, reference in zip(accounts, references)]
        formula = f'=IF(COUNT({",".join(references)})={len(references)},0{"".join(terms)},"")'
        sheet.cell(total, column, formula, font=HEAD_FONT, align=RIGHT, money=True, fill=TOTAL_FILL,
                   formula=True)
    sheet.cell(total, 20, "—", align=CENTER, fill=TOTAL_FILL)
    row = total + 1
    sheet.merge(row, 1, 4, "与审定表核对（本期未审发生额）" if is_pl else "与审定表核对（期末未审数）",
                font=SMALL_FONT, fill=TOTAL_FILL)
    audit_name = f"{index_no} 审定表"
    if is_pl:
        terms = [("-" if account.direction != meta["presentation_direction"] else "+") +
                 f"{'L' if account.direction == '贷' else 'K'}{subtotal}"
                 for account, subtotal in zip(accounts, subtotal_rows)]
        book_value = "0" + "".join(terms)
        book_refs = [term[1:] for term in terms]
    else:
        book_value = f"Q{total}"
        book_refs = [book_value]
    sheet.cell(row, 6, f'=IF(COUNT({",".join(book_refs)},\'{audit_name}\'!F{audit_total})={len(book_refs)+1},'
               f'ROUND(({book_value})-\'{audit_name}\'!F{audit_total},2),"")',
               money=True, align=RIGHT, fill=TOTAL_FILL, formula=True)
    sheet.cell(row, 7, f'=IF(ISNUMBER(F{row}),IF(ABS(F{row})<0.005,"G","差异"),"待补来源")',
               fill=TOTAL_FILL, formula=True)
    sheet.merge(row, 8, 19, "同口径核对：资产负债比较期末未审余额；损益比较本期发生额，均不使用尚未取得的审定数。",
                font=SMALL_FONT)
    row += 1
    sheet.merge(row, 1, 4, "与审定表核对（本期审定发生额）" if is_pl else "与审定表核对（期末审定数）",
                font=SMALL_FONT, align=LEFT, fill=TOTAL_FILL)
    sheet.cell(row, 5, "—", align=CENTER, fill=TOTAL_FILL)
    audit_name = f"{index_no} 审定表"
    audited_terms = [("-" if account.direction != meta["presentation_direction"] else "+") +
                     f"{'N' if account.direction == '贷' else 'M'}{subtotal}"
                     for account, subtotal in zip(accounts, subtotal_rows)]
    audited_refs = [term[1:] for term in audited_terms]
    audited_value = "0" + "".join(audited_terms) if is_pl else f"S{total}"
    count_refs = ",".join(audited_refs) if is_pl else f"S{total}"
    needed = len(audited_refs) + 1 if is_pl else 2
    sheet.cell(row, 6, f'=IF(COUNT({count_refs},\'{audit_name}\'!K{audit_total})={needed},'
               f'ROUND(({audited_value})-\'{audit_name}\'!K{audit_total},2),"")',
               align=RIGHT, money=True, fill=TOTAL_FILL, formula=True)
    sheet.cell(row, 7, f'=IF(ISNUMBER(F{row}),IF(ABS(F{row})<0.005,"G","差异"),"待补审定依据")',
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
    columns = 9
    widths = (6, 62, 9, 9, 11, 11, 9, 12, 22)
    sheet = Sheet(book, "审计程序", widths)
    row = sheet.header_block(meta, columns=columns, title=f"{meta['name']}审计程序表",
                             index_no=f"{index_no}-0", page=4)
    for column, text in enumerate(("序号", "审计程序", "存在", "完整性", "权利和义务", "计价和分摊",
                                   "列报", "索引号", "备注"), 1):
        sheet.cell(row, column, text, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    sheet.ws.row_dimensions[row].height = 30
    row += 1
    last_section = ""
    for number, item in enumerate(procedures, 1):
        marks = {key: ("√" if key in item.assertions else "") for key in
                 ("存在", "完整性", "权利和义务", "计价和分摊", "列报")}
        target = f"{index_no}-2" if item.basis == "-2" else (
            f"{index_no}-{item.basis}" if item.basis else f"{index_no}-1")
        section = ""
        if item.section and item.section != last_section:
            last_section, section = item.section, item.section
        values = (number, item.text, marks["存在"], marks["完整性"], marks["权利和义务"],
                  marks["计价和分摊"], marks["列报"], target, section)
        rows_needed = len(str(item.text)) // 30 + 1
        for column, value in enumerate(values, 1):
            sheet.cell(row, column, value, align=CENTER if column not in (2, 9) else LEFT,
                       height=max(30, 14 * rows_needed + 6))
        row += 1
    row += 1
    sheet.merge(row, 1, columns,
                "程序清单按报表项目类别整理，与鼎信诺底稿的「审计程序」表同口径：认定栏勾选与本项目相关的"
                "认定；列示程序清单不代表已经执行或取得证据。", font=SMALL_FONT, height=30)
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
    for account_index, account in enumerate(accounts):
        current = period_amount(account) if is_pl else (_amount(account.ending) or Decimal("0"))
        previous = _amount(getattr(account, "previous_period_amount", None)) if is_pl else _amount(account.opening)
        note = mapped.get(account.name) or mapped.get(f"{meta['name']}_{account.name}") or WAIT
        values = (f"{meta['name']}_{account.name}", current, previous, None, note,
                  f"{index_no}-1")
        for column, value in enumerate(values, 1):
            sheet.cell(row, column, value, align=LEFT if column in (1, 5) else RIGHT,
                       money=column in (2, 3, 4), height=22)
        audit_ref = f"'{index_no} 审定表'!K{9 + account_index}"
        sheet.cell(row, 4, f'=IF(COUNT(B{row},{audit_ref})=2,ROUND(B{row}-{audit_ref},2),"")',
                   money=True, align=RIGHT, formula=True)
        row += 1
    sheet.cell(row, 1, "合　　计", font=HEAD_FONT, align=LEFT, fill=TOTAL_FILL, height=22)
    for column, value in ((2, total_row_values.get("current")), (3, total_row_values.get("previous"))):
        sheet.cell(row, column, value, font=HEAD_FONT, align=RIGHT, money=True, fill=TOTAL_FILL)
    audit_ref = f"'{index_no} 审定表'!K{9 + len(accounts)}"
    sheet.cell(row, 4, f'=IF(COUNT(B{row},{audit_ref})=2,ROUND(B{row}-{audit_ref},2),"")',
               money=True, align=RIGHT, formula=True, fill=TOTAL_FILL)
    sheet.cell(row, 5, "—", align=CENTER, fill=TOTAL_FILL)
    sheet.cell(row, 6, "—", align=CENTER, fill=TOTAL_FILL)
    sheet.merge(row + 2, 1, columns,
                "本表当前数为账面口径；审定差异仅在两项金额均已提供时计算，留空表示缺少审定依据，不表示无差异。",
                font=SMALL_FONT, height=30)
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


EXTRA_CALCULATIONS = {
    "银行账户清单": {8: (("G", "F"), "G{r}-F{r}")},
    "库存现金监盘表": {4: (("C", "B"), "C{r}-B{r}")},
    "坏账准备计算表": {4: (("B", "C"), "B{r}*C{r}"), 6: (("D", "E"), "D{r}-E{r}")},
    "存货盘点表": {5: (("D", "C"), "D{r}-C{r}"), 8: (("D", "F"), "D{r}*F{r}")},
    "存货计价测试表": {5: (("D", "C"), "D{r}-C{r}")},
    "存货跌价准备测试表": {4: (("B", "C"), "MAX(B{r}-C{r},0)"), 6: (("D", "E"), "D{r}-E{r}")},
    "资产减值测试表": {4: (("B", "C"), "MAX(B{r}-C{r},0)")},
    "借款及利息测算表": {8: (("G", "F"), "G{r}-F{r}")},
}


def _extra_sheet(book, meta, spec, *, index_no, page, rows_data=()):
    """按列式生成专项表：能推出来的先填，其余留空待现场。"""
    title, columns, note, blank_rows = spec[:4]
    sheet = Sheet(book, title[:31], tuple(width for _name, width, _kind in columns))
    heading = title if title.startswith(meta["name"]) else f"{meta['name']}{title}"
    row = sheet.header_block(meta, columns=len(columns), title=heading,
                             index_no=f"{index_no}-{page}", page=page)
    for column, (name, _width, _kind) in enumerate(columns, 1):
        sheet.cell(row, column, name, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    sheet.ws.row_dimensions[row].height = 30
    row += 1
    first_data = row
    for values in rows_data:
        lines = 1
        for column, (_name, width, kind) in enumerate(columns, 1):
            value = values[column - 1] if column - 1 < len(values) else None
            if kind == TEXT and value:
                lines = max(lines, len(str(value)) // max(6, int(width / 2)) + 1)
            sheet.cell(row, column, value,
                       align=RIGHT if kind in (MONEY, NUM) else
                       (LEFT if kind == TEXT and column in (2, 4) else CENTER),
                       money=kind == MONEY, percent=title in ("分析程序表", "费用明细及分析表") and column == 5,
                       height=max(22, 14 * lines + 6))
        row += 1
    for _ in range(max(blank_rows - len(rows_data), 0)):
        for column, (_name, _width, kind) in enumerate(columns, 1):
            sheet.cell(row, column, None,
                       align=RIGHT if kind in (MONEY, NUM) else CENTER,
                       money=kind == MONEY, height=22)
        row += 1
    for target_row in range(first_data, row):
        for column, (sources, expression) in EXTRA_CALCULATIONS.get(title, {}).items():
            refs = ",".join(f"{source}{target_row}" for source in sources)
            formula = f'=IF(COUNT({refs})={len(sources)},{expression.format(r=target_row)},"")'
            sheet.cell(target_row, column, formula, money=columns[column - 1][2] == MONEY,
                       align=RIGHT, formula=True)
    row += 1
    sheet.merge(row, 1, len(columns), f"填表说明：{note}", font=SMALL_FONT,
                height=max(36, (len(note) // 65 + 1) * 16))
    sheet.merge(row + 1, 1, len(columns),
                "本表已按科目余额表预填可得信息，空白处须依据取得的资料或原始凭证填写；"
                "缺少输入的差异保持空白，不能作为零差异结论。",
                font=SMALL_FONT, fill=TOTAL_FILL, height=22)
    sheet.page_setup(8)
    return sheet


def _self_checks(consistent, gap, picks, prepared_by, reviewed_by, *, has_detail=True, is_pl=False):
    """复核自检项：只看格式、索引与勾稽，不评价证据是否充分。"""
    return (
        ("底稿目录、导引表与各工作表名称一致", "已核对", "生成时按同一索引号命名"),
        ("明细发生额与汇总发生额一致" if is_pl else "明细表合计与科目余额表逐栏一致",
         ("待补发生额来源" if consistent is None else "已核对" if consistent else "不一致，待追查") if has_detail else "未提供二级明细",
         (f"{'发生额' if is_pl else '四栏'}绝对差异合计 {gap:,.2f} 元" if gap is not None else "来源不完整，尚未完成核对")
         if has_detail else "直接列示一级记录，不构成独立明细核对"),
        ("账面/未审与审定核对口径已区分", "待复核", "账面核对可先进行，审定比较须取得独立依据"),
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
    columns = 5
    sheet = Sheet(book, "底稿目录", (6, 14, 36, 30, 8))
    row = sheet.header_block(meta, columns=columns, title=f"{meta['name']}实质性底稿目录",
                             index_no=index_no, page=1)
    for column, text in enumerate(("序号", "索引号", "底稿名称", "工作表", "页次"), 1):
        sheet.cell(row, column, text, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    row += 1
    for number, (code, title, sheet_name, note) in enumerate(entries, 1):
        for column, value in enumerate((number, code, title, sheet_name, number + 1), 1):
            cell = sheet.cell(row, column, value, align=LEFT if column in (3, 4) else CENTER, height=22)
            if column == 4:
                escaped = sheet_name.replace("'", "''")
                cell.hyperlink = f"#'{escaped}'!A1"
        row += 1
    sheet.merge(row, 1, 2, "合计", font=HEAD_FONT, align=CENTER, fill=TOTAL_FILL, height=22)
    sheet.cell(row, 3, f"共 {len(entries)} 份底稿", align=LEFT, fill=TOTAL_FILL)
    for column in (4, 5):
        sheet.cell(row, column, "—", align=CENTER, fill=TOTAL_FILL)
    sheet.merge(row + 2, 1, columns, "科目与金额明细见账项明细表；本册为待复核草稿。", font=SMALL_FONT, height=22)
    sheet.page_setup()
    return sheet


def _auxiliary_check_sheet(book, meta, *, index_no):
    sheet = Sheet(book, "客商明细核对", (16, 25, 22, 20, 20, 18, 60))
    row = sheet.header_block(meta, columns=7, title=f"{meta['name']}客商明细核对",
                             index_no=f"{index_no}-KH", page=6)
    for column, text in enumerate(("科目编码", "科目名称", "核算维度", "期初差异", "期末差异", "核对状态", "范围与说明"), 1):
        sheet.cell(row, column, text, font=HEAD_FONT, align=CENTER, fill=HEAD_FILL)
    labels = {"matched": "相符", "difference": "差异，待核实", "missing": "待补来源", "ambiguous": "维度歧义，未取数"}
    for check in meta.get("auxiliary_checks", ()):
        row += 1
        code = check.get("account_code", "")
        account = next((item for item in meta["rows"] if item.code == code), None)
        values = (code, account.name if account else "", check.get("type_code", ""),
                  check.get("opening_difference"), check.get("ending_difference"),
                  labels.get(check.get("status"), "待核实"), check.get("note", ""))
        for column, value in enumerate(values, 1):
            sheet.cell(row, column, value, money=column in (4, 5), height=32)
    if not meta.get("auxiliary_checks"):
        row += 1
        sheet.merge(row, 1, 7, "未取得已明确选择的客商核算维度，客商明细与账龄不能由科目名称猜填。", height=32)
    sheet.merge(row + 2, 1, 7, "同一科目只使用已确认的客户、供应商或往来单位维度。客商明细不与其科目汇总重复计数；"
                "账龄、函证及交易真实性仍需独立证据。", font=SMALL_FONT, height=36)
    sheet.page_setup(7)
    return sheet


_PAPER_JOURNAL = object()


def build(path, *, paper, subject_label, rows, resolve, prepared_by="", reviewed_by="",
          when="", procedures=(), narrative=None, standard="企业会计准则（一般企业）",
          accounts=None, full=True, journal=_PAPER_JOURNAL, sampler=None,
          sheet_add=(), sheet_drop=(), paper_data=None):
    """生成鼎信诺式底稿册：目录、审定表、明细表、审计程序、披露表。

    ``resolve(code, name)`` 返回本项目匹配的 ``Paper``（可为 None）；``accounts`` 缺省时按报表项目自动归集。
    ``journal`` 未传入时继承 ``paper_data`` 的凭证，显式 ``None`` 表示排除凭证。
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
    auxiliary = ()
    if paper_data is not None:
        if any(row.code not in by_code or by_code[row.code] != row for row in paper_data.rows):
            raise ValueError("客商与发生额来源和本册科目快照不一致，请重新读取。")
        identity = (rows[0].subject_id, rows[0].ledger_id, rows[0].year, rows[0].month)
        unique_auxiliary = {}
        dimensions = {}
        for item in paper_data.auxiliary:
            if item.account_code not in by_code or (item.subject_id, item.ledger_id, item.year, item.month) != identity:
                raise ValueError("客商明细含其他科目、账套或期间，已停止导出。")
            if item.direction not in {"借", "贷"}:
                raise ValueError("客商明细缺少明确的借贷方向。")
            key = (item.account_code, item.type_code, item.code)
            if key in unique_auxiliary and unique_auxiliary[key] != item:
                raise ValueError("同一客商核算项目存在冲突，不能合并导出。")
            unique_auxiliary[key] = item
            dimensions.setdefault(item.account_code, set()).add(item.type_code)
        if any(len(types) > 1 for types in dimensions.values()):
            raise ValueError("同一科目选择多个客商维度，不能重复叠加。")
        auxiliary = tuple(unique_auxiliary.values())
    if journal is _PAPER_JOURNAL:
        journal = paper_data.journal if paper_data is not None else None
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
    is_pl = bool(getattr(grouped[0], "is_profit_loss", False)) or index_no.startswith(("4-", "5-"))
    for account in grouped:
        for line in [account, *detail_rows(rows, account)]:
            if line.direction not in ("借", "贷"):
                raise ValueError(f"科目 {line.code} 缺少明确的借贷方向。")
            if not is_pl and any(_amount(getattr(line, key)) is None for key in ("opening", "debit", "credit", "ending")):
                raise ValueError(f"科目 {line.code} 金额不完整，不能以零代替缺少的数据。")
    narrative = narrative or {}
    materials = paper_materials.for_paper(index_no, name)
    pending = paper_materials.pending(materials)
    consistent, gap = consistency(grouped, rows, is_pl=is_pl)
    has_detail = any(detail_rows(rows, account) != [account] for account in grouped)
    direction = presentation_direction(paper, grouped)
    total = totals(grouped, direction)
    notes = narrative.get("guide_notes") or baseline_notes(
        name="、".join(account.name for account in grouped), accounts=grouped, total=total, is_pl=is_pl, consistent=consistent,
        gap=gap, pending=pending, has_detail=has_detail)
    conclusion = ("待定：本期发生额来源不完整，尚未完成明细核对，不能据此判断无异常。"
                  if consistent is None else default_conclusion(consistent, pending, has_detail=has_detail))
    meta = {
        "project": project, "name": name, "period": period, "cutoff": cutoff,
        "prepared_by": prepared_by, "reviewed_by": reviewed_by,
        "date": when or WAIT, "standard": standard, "rows": rows,
        "accounts": grouped, "presentation_direction": direction, "is_pl": is_pl,
        "auxiliary": auxiliary,
        "auxiliary_checks": tuple(paper_data.auxiliary_checks) if paper_data is not None and not is_pl else (),
        "source": "金额来源：本机账套（" + ("独立发生额" if is_pl else "科目余额") + ("、已选客商辅助核算" if auxiliary else "") + "）",
        "audit_notes": notes,
        "procedure_notes": narrative.get("procedure_notes") or [],
        "disclosure_notes": narrative.get("disclosure_notes") or [],
        "conclusion": conclusion,
    }
    if accounts is not None and len(grouped) == 1:
        root = grouped[0]
        meta["primary_account"] = root
        meta["secondary_accounts"] = [line for line in detail_rows(rows, root) if line.level == 2]
        meta["source"] += f"；一级科目：{root.code} {root.name}"
    book = Workbook()
    book.remove(book.active)
    entries = [
        (index_no, f"{name}审定表", f"{index_no} 审定表", "账套取数，含上期数与变动分析"),
        (f"{index_no}-1", f"{name}账项明细表", f"{index_no} 明细表", "取数标记 1:K<科目编号>"),
        (f"{index_no}-0", f"{name}审计程序表", "审计程序", f"共 {len(procedures)} 条程序"),
        (f"{index_no}-15", f"{name}披露表", "披露表", "与审定数勾稽"),
    ]
    sheet_state = paper_sheets.facts(rows, journal)
    sheet_state.update(primary_accounts=grouped, rows=rows, report_total=total["period"] if is_pl else total["ending"], is_pl=is_pl,
                       auxiliary=auxiliary, presentation_direction=direction)
    leaves = [line for account in grouped for line in detail_rows(rows, account)]
    extras = list(paper_sheets.for_category(_category_of(index_no, name), sheet_state)) if full else []
    if sheet_drop:
        mandatory = {spec[0] for spec in paper_sheets.BASE}
        extras = [spec for spec in extras if spec[0] not in set(sheet_drop) or spec[0] in mandatory]
    for title in sheet_add or ():
        spec = paper_sheets.by_name(title) or dxn_templates.spec_for(title, name)
        if spec and all(spec[0] != item[0] for item in extras):
            extras.append(spec)
    extras = tuple(extras)
    analysis_accounts = list({row.code: row for row in [*grouped, *leaves]}.values())
    movements = paper_sheets.movement_rows(analysis_accounts)
    cutoff_picks = sampling.cutoff(journal, rows[0].year, rows[0].month) if journal and rows[0].year else []
    for offset, spec in enumerate(extras, 8):
        entries.append((f"{index_no}-{offset}", f"{name}{spec[0]}", spec[0][:31],
                        f"{len(spec[1])} 列 × {spec[3]} 行，待资料填写"))
    entries.append((f"9720-{index_no}", f"{name}复核自检清单",
                    f"9720-{index_no} 复核自检清单", "格式、索引与勾稽自检"))
    has_auxiliary_check = not is_pl and (bool(meta["auxiliary_checks"] or auxiliary) or (paper_data is not None and
                          ("应收" in name or "应付账款" in name or "其他应付款" in name or "预付款" in name or "预收" in name)))
    if has_auxiliary_check:
        entries.append((f"{index_no}-KH", f"{name}客商明细核对", "客商明细核对", "按已选客商维度核对，不叠加多个维度"))
    _metadata_sheet(book, meta, index_no)
    _catalog(book, meta, index_no, entries)
    audit, audit_total = _audit_sheet(book, meta, grouped, is_pl=is_pl, index_no=index_no)
    _detail_sheet(book, meta, grouped, is_pl=is_pl, index_no=index_no, audit_total=audit_total)
    if has_auxiliary_check:
        _auxiliary_check_sheet(book, meta, index_no=index_no)
    _program_sheet(book, meta, procedures, index_no=index_no)
    current_total = total["period"] if is_pl else total["ending"]
    previous_total = total["previous_period"] if is_pl else total["opening"]
    _disclosure_sheet(book, meta, grouped, is_pl=is_pl, index_no=index_no,
                      total_row_values={"current": current_total, "previous": previous_total})
    picks, pick_source = [], ""
    if journal:
        if callable(sampler):
            picks, pick_source = sampler(name, journal)
        else:
            picks, pick_source = sampling.pick(journal), "规则抽样"
        picks = [(row, reason) for row, reason in picks if not sampling.is_profit_closing(row)]
    for offset, spec in enumerate(extras, 8):
        title = spec[0]
        if title == "抽凭表":
            data = [(number, str(getattr(row, "date", "")), row.voucher, row.summary, row.code,
                     row.contra, row.debit, row.credit, reason, None)
                    for number, (row, reason) in enumerate(picks, 1)]
        elif title in ("分析程序表", "费用明细及分析表"):
            data = []
            for item in movements:
                explanation = (narrative.get("analysis_notes") or {}).get(item["code"]) or "AI 尚未填写本项分析；请生成 AI 分析或补充原因证据。"
                if item["previous"] is None:
                    explanation += " 未提供上期发生额，不能计算同比变动。"
                elif item["previous"] == 0:
                    explanation += " 年初余额为零，变动率不适用。"
                data.append((f"{item['code']} {item['name']}", item["current"], item["previous"], item["change"],
                             float(item["rate"]) if item["rate"] is not None else None, explanation))
            spec = (spec[0], (("项目", 30, TEXT), ("本期发生额" if is_pl else "期末账面数", 20, MONEY),
                             ("上期发生额" if is_pl else "年初账面数", 20, MONEY),
                             ("变动额", 20, MONEY), ("变动率", 14, TEXT), ("AI 变动分析与核查重点", 55, TEXT)),
                    "金额及变动由本地账套计算；年初账面数不冒充上期审定数。AI 仅填写观察、待核实原因与核查重点。",
                    spec[3], spec[4])
        elif title in paper_sheets.CUTOFF_SHEETS:
            data = [(row.date, row.voucher, row.line, row.code, row.summary, row.debit, row.credit, reason,
                     None, None, None) for row, reason in cutoff_picks]
            scope_note = ("未读取序时账，尚未抽样。" if journal is None else
                          "在已读取范围内未抽中候选，不表示不存在跨期事项。" if not cutoff_picks else
                          "样本仅用于筛选疑点，不代替业务归属期间的判断。")
            if journal is not None and not getattr(journal, "complete", True):
                scope_note += " 序时账已按最近记录截取，抽样总体不完整，需补取未覆盖记录。"
            spec = (spec[0], spec[1], spec[2] + " " + scope_note, spec[3], spec[4])
        else:
            data = paper_sheets.prefill(title, sheet_state, leaves)
            if title in {"账龄分析表", "函证信息汇总表", "往来账龄分析表"} and not auxiliary:
                spec = (spec[0], spec[1], spec[2] + " 未取得明确选择的客商核算项目，本表不以科目名称代填客商。", *spec[3:])
        _extra_sheet(book, meta, spec, index_no=index_no, page=offset, rows_data=data)
    if pick_source:
        meta["pick_source"] = pick_source
    meta["review_page"] = 8 + len(extras)
    checks = list(_self_checks(consistent, gap, picks, prepared_by, reviewed_by, has_detail=has_detail, is_pl=is_pl))
    for check in meta["auxiliary_checks"]:
        checks.append((f"客商明细与科目 {check.get('account_code', '')} 核对",
                       {"matched": "相符", "difference": "差异", "missing": "待补来源", "ambiguous": "维度歧义"}.get(check.get("status"), "待核实"),
                       str(check.get("note") or "见客商明细核对表")))
    if is_pl:
        checks.append(("本期及上期发生额来源完整",
                       "已读取" if total["period"] is not None and total["previous_period"] is not None else "待补发生额来源",
                       "损益明细按月份列示；借贷累计额和结转后余额不能代替独立的本期、上期发生额。"))
    if paper_data is not None:
        checks.extend(("取数范围与缺口", "待核实", str(warning)) for warning in paper_data.warnings)
    _review_sheet(book, meta, checks,
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
        total = totals(accounts, presentation_direction(paper, accounts))
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
