"""生成合成测试模板（全部虚构数据，不含任何客户信息）。

用途：tests/test_fields.py 与界面演示。
运行：python tests/fixtures/make_template.py
"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

HEADERS = ["报表项目", "索引号", "期初余额", "期末余额", "调整分录", "期末审定数"]

# (项目名称, 期初余额, 期末余额, 说明)
ROWS = [
    ("库存现金", None, None, ""),
    ("银行存款", None, None, ""),
    ("应收账款", None, None, ""),
    ("存货", None, None, ""),
    ("其他应收款", None, None, ""),
    ("固定资产", None, None, ""),
    ("应付账款", None, None, ""),
    ("预收款项", None, None, ""),
    ("应交税费", None, None, ""),
    ("实收资本", None, None, ""),
    ("未分配利润", None, 700000, "已有值，用于演示差额与覆盖确认"),
]


def build(path: Path) -> Path:
    book = Workbook()
    sheet = book.active
    sheet.title = "试算平衡表"
    sheet["A1"] = "单位：元"
    sheet["A2"] = "试算平衡表（合成样例）"
    sheet["A2"].font = Font(bold=True, size=13)
    for column, title in enumerate(HEADERS, start=1):
        cell = sheet.cell(3, column, title)
        cell.font = Font(bold=True)
    for offset, (label, opening, ending, _note) in enumerate(ROWS):
        row = 4 + offset
        sheet.cell(row, 1, label)
        if opening is not None:
            sheet.cell(row, 3, opening)
        if ending is not None:
            sheet.cell(row, 4, ending)
    total_row = 4 + len(ROWS)
    sheet.cell(total_row, 1, "资产总计")
    sheet.cell(total_row, 4, f"=SUM(D4:D{total_row - 1})")
    for column, width in zip("ABCDEF", (22, 12, 16, 16, 16, 16)):
        sheet.column_dimensions[column].width = width
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    return path


if __name__ == "__main__":
    print(build(Path(__file__).with_name("试算平衡表-合成.xlsx")))
