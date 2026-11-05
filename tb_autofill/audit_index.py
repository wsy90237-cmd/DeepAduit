"""一级科目到财务报表审计底稿索引的映射。

数据来自客户提供的《审计业务索引号及底稿名称清单》「索引号一览」页：
只取「财务报表审计 / 实质性底稿」，标黄行（银行询证函、每股收益表、往来函证程序等
资料性与程序性表格）不属于报表科目，映射时一律跳过。
清单本体随安装包内置一份快照，允许运行期改选其他清单文件。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BUSINESS = "财务报表审计"
CATEGORY = "实质性底稿"
SHEET = "索引号一览"
EXCLUDED_NOTE = "标黄行不属于财务报表审计报表科目"
ASSET = Path(__file__).resolve().parent / "assets" / "audit-index.json"


@dataclass(frozen=True)
class Paper:
    """一条底稿索引：索引号 + 底稿名称 + 对应模板文件名。"""

    index_no: str
    name: str
    templates: str = ""

    @property
    def label(self):
        return f"{self.index_no} {self.name}"


class IndexError_(ValueError):
    """清单文件不可用（缺页、无有效行）。"""


def _cell_text(value):
    return "" if value is None else str(value).strip()


def _is_yellow(cell):
    """标黄行：清单用它标记“不属于报表科目”的资料性 / 程序性表格。"""
    fill = getattr(cell, "fill", None)
    color = getattr(fill, "start_color", None) if fill is not None else None
    rgb = _cell_text(getattr(color, "rgb", "")) if color is not None else ""
    rgb = rgb.upper()
    if len(rgb) == 8:                     # ARGB：忽略 alpha 差异
        rgb = rgb[2:]
    return rgb == "FFFF00"


def _read_rows(sheet):
    """定位表头，按列名取值，避免清单列序调整后整表错位。"""
    header, header_row = {}, 0
    for row in sheet.iter_rows(min_row=1, max_row=min(6, sheet.max_row)):
        names = {_cell_text(cell.value): cell.column for cell in row if _cell_text(cell.value)}
        if {"业务类型", "底稿类型", "索引号", "底稿名称"} <= set(names):
            header, header_row = names, row[0].row
            break
    if not header:
        raise IndexError_("清单缺少「业务类型 / 底稿类型 / 索引号 / 底稿名称」表头。")
    for row in sheet.iter_rows(min_row=header_row + 1, max_row=sheet.max_row):
        values = {name: row[column - 1] for name, column in header.items() if column <= len(row)}
        yield row, values


def load(path):
    """读取清单 xlsx，返回已剔除标黄行的报表科目列表。"""
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # 打包环境缺少依赖时给出可读提示
        raise IndexError_("缺少 openpyxl，无法读取底稿索引清单。") from exc
    target = Path(path)
    if not target.exists():
        raise IndexError_(f"未找到底稿索引清单：{target.name}")
    try:
        book = load_workbook(target, read_only=False, data_only=True)
    except Exception as exc:
        raise IndexError_(f"无法读取底稿索引清单：{exc}") from exc
    sheet = book[SHEET] if SHEET in book.sheetnames else book.worksheets[0]
    papers, seen = [], set()
    for row, values in _read_rows(sheet):
        business = _cell_text(values.get("业务类型").value if values.get("业务类型") else "")
        category = _cell_text(values.get("底稿类型").value if values.get("底稿类型") else "")
        index_no = _cell_text(values.get("索引号").value if values.get("索引号") else "")
        name = _cell_text(values.get("底稿名称").value if values.get("底稿名称") else "")
        if business != BUSINESS or category != CATEGORY or not index_no or not name:
            continue
        if any(_is_yellow(cell) for cell in row):
            continue
        if index_no in seen:
            continue
        template_cell = values.get("模板文件名")
        papers.append(Paper(index_no, name, _cell_text(template_cell.value) if template_cell else ""))
        seen.add(index_no)
    if not papers:
        raise IndexError_(f"清单中没有「{BUSINESS} / {CATEGORY}」的有效科目行。")
    return papers


@lru_cache(maxsize=1)
def bundled():
    """内置快照：打包后无需用户提供清单也能识别报表科目。"""
    if not ASSET.exists():
        return ()
    try:
        data = json.loads(ASSET.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(Paper(str(item["index"]), str(item["name"]), str(item.get("templates", "")))
                 for item in data if item.get("index") and item.get("name"))


def resolve_papers(papers=None):
    return tuple(papers) if papers else bundled()


def find(papers, index_no):
    return next((paper for paper in resolve_papers(papers) if paper.index_no == index_no), None)


# 一级科目代码 → 索引号。按报表项目分组，取自《企业会计准则》与小企业准则常用科目。
_CODE_MAP = {
    # 货币资金
    "1001": "1-010", "1002": "1-010", "1003": "1-010", "1011": "1-010",
    "1012": "1-010", "1021": "1-010", "1031": "1-010",
    # 交易性金融资产与衍生工具
    "1101": "1-040", "1102": "1-040", "1103": "1-040", "1111": "1-040",
    # 应收款项
    "1121": "1-060", "1122": "1-070", "1123": "1-090", "1124": "1-160",
    "1131": "1-130", "1132": "1-130", "1221": "1-130", "1231": "1-070",
    # 存货类
    "1401": "1-150", "1402": "1-150", "1403": "1-150", "1404": "1-150",
    "1405": "1-150", "1406": "1-150", "1407": "1-150", "1408": "1-150",
    "1411": "1-150", "1421": "1-150", "1431": "1-150", "1441": "1-150",
    "1451": "1-150", "1461": "1-150", "1471": "1-150",
    "5001": "1-150", "5101": "1-150", "5201": "1-150",
    # 长期股权投资与金融资产
    "1501": "1-250", "1502": "1-250", "1503": "1-250",
    "1511": "1-240", "1512": "1-240",
    # 投资性房地产
    "1521": "1-270", "1522": "1-270", "1523": "1-270",
    # 长期应收款
    "1531": "1-380", "1532": "1-380",
    # 固定资产与在建工程
    "1601": "1-280", "1602": "1-280", "1603": "1-280", "1606": "1-280",
    "1604": "1-290", "1605": "1-290",
    # 无形资产、开发支出与商誉
    "1701": "1-330", "1702": "1-330", "1703": "1-330",
    "5301": "1-340",
    "1711": "1-350", "1712": "1-350",
    # 其他长期资产
    "1801": "1-360", "1811": "1-370", "1901": "1-190",
    # 负债
    "2001": "2-010", "2101": "2-040", "2111": "2-050",
    "2201": "2-060", "2202": "2-070", "2203": "2-080", "2204": "2-090",
    "2211": "2-140", "2221": "2-150",
    "2231": "2-160", "2232": "2-160", "2241": "2-160", "2245": "2-160",
    "2401": "2-290", "2501": "2-230", "2502": "2-240",
    "2701": "2-260", "2702": "2-260", "2711": "2-260",
    "2801": "2-280", "2901": "2-300",
    # 所有者权益
    "4001": "3-010", "4002": "3-030", "4101": "3-070", "4102": "3-070",
    "4103": "3-090", "4104": "3-090", "4201": "3-040",
    # 收入与成本费用
    "6001": "4-010", "6011": "4-010", "6051": "4-010",
    "6401": "5-010", "6402": "5-010", "5401": "5-010", "5402": "5-010",
    "6403": "5-090", "5403": "5-090",
    "6601": "5-100", "5601": "5-100",
    "6602": "5-110", "5602": "5-110",
    "6603": "5-130", "5603": "5-130", "6411": "5-130",
    "6605": "5-120",
    "6101": "5-180", "6111": "5-150", "6115": "5-210", "6117": "5-140",
    "6301": "5-220", "5711": "5-220",
    "6701": "5-200", "6702": "5-190",
    "6711": "5-230", "5721": "5-230",
    "6801": "5-240", "6901": "3-090",
}

# 科目名称关键词 → 索引号；代码未收录或账套自设科目时按名称兜底。
_NAME_MAP = (
    ("1-010", ("库存现金", "银行存款", "其他货币资金", "货币资金", "存放中央银行",
               "存放同业", "结算备付金", "存出保证金")),
    ("1-040", ("交易性金融资产", "衍生金融资产", "以公允价值计量")),
    ("1-060", ("应收票据",)),
    ("1-070", ("应收账款", "坏账准备", "应收款项")),
    ("1-080", ("应收款项融资",)),
    ("1-090", ("预付账款", "预付款项")),
    ("1-130", ("其他应收款", "应收股利", "应收利息", "备用金")),
    ("1-150", ("存货", "原材料", "库存商品", "材料采购", "在途物资", "周转材料",
               "委托加工物资", "发出商品", "跌价准备", "生产成本", "制造费用")),
    ("1-160", ("合同资产",)),
    ("1-170", ("持有待售资产",)),
    ("1-180", ("一年内到期的非流动资产",)),
    ("1-190", ("其他流动资产", "待处理财产损溢")),
    ("1-240", ("长期股权投资", "长期股权", "子公司", "合营", "联营")),
    ("1-250", ("其他权益工具投资", "可供出售金融资产", "持有至到期投资")),
    ("1-260", ("其他非流动金融资产",)),
    ("1-270", ("投资性房地产",)),
    ("1-280", ("固定资产", "累计折旧", "固定资产清理")),
    ("1-290", ("在建工程", "工程物资")),
    ("1-300", ("生产性生物资产",)),
    ("1-310", ("油气资产",)),
    ("1-320", ("使用权资产",)),
    ("1-330", ("无形资产", "累计摊销")),
    ("1-340", ("开发支出", "研发支出")),
    ("1-350", ("商誉",)),
    ("1-360", ("长期待摊费用",)),
    ("1-370", ("递延所得税资产",)),
    ("1-380", ("其他非流动资产", "长期应收款", "未实现融资收益")),
    ("2-010", ("短期借款",)),
    ("2-040", ("交易性金融负债",)),
    ("2-050", ("衍生金融负债",)),
    ("2-060", ("应付票据",)),
    ("2-070", ("应付账款",)),
    ("2-080", ("预收账款", "预收款项")),
    ("2-090", ("合同负债",)),
    ("2-140", ("应付职工薪酬", "应付工资", "应付福利费")),
    ("2-150", ("应交税费", "应交税金", "应交增值税")),
    ("2-160", ("其他应付款", "应付利息", "应付股利", "应付手续费")),
    ("2-190", ("持有待售负债",)),
    ("2-200", ("一年内到期的非流动负债",)),
    ("2-210", ("其他流动负债",)),
    ("2-230", ("长期借款",)),
    ("2-240", ("应付债券",)),
    ("2-250", ("租赁负债",)),
    ("2-260", ("长期应付款", "未确认融资费用", "专项应付款")),
    ("2-270", ("长期应付职工薪酬",)),
    ("2-280", ("预计负债",)),
    ("2-290", ("递延收益",)),
    ("2-300", ("递延所得税负债",)),
    ("3-010", ("实收资本", "股本", "注册资本")),
    ("3-020", ("其他权益工具", "优先股", "永续债")),
    ("3-030", ("资本公积",)),
    ("3-040", ("库存股",)),
    ("3-050", ("其他综合收益",)),
    ("3-060", ("专项储备",)),
    ("3-070", ("盈余公积", "法定公益金", "一般风险准备")),
    ("3-090", ("未分配利润", "本年利润", "利润分配", "以前年度损益调整")),
    ("3-100", ("少数股东权益",)),
    ("4-010", ("主营业务收入", "其他业务收入", "营业收入", "利息收入", "已赚保费")),
    ("5-010", ("主营业务成本", "其他业务成本", "营业成本")),
    ("5-090", ("税金及附加", "营业税金及附加")),
    ("5-100", ("销售费用", "营业费用")),
    ("5-110", ("管理费用",)),
    ("5-120", ("研发费用",)),
    ("5-130", ("财务费用", "利息费用", "利息支出", "汇兑损益")),
    ("5-140", ("其他收益",)),
    ("5-150", ("投资收益",)),
    ("5-160", ("汇兑收益",)),
    ("5-180", ("公允价值变动",)),
    ("5-190", ("信用减值损失",)),
    ("5-200", ("资产减值损失",)),
    ("5-210", ("资产处置",)),
    ("5-220", ("营业外收入",)),
    ("5-230", ("营业外支出",)),
    ("5-240", ("所得税费用", "所得税",)),
)


def _by_code(code, papers):
    """先精确匹配 4 位代码，再匹配去前缀后的代码（部分账套带级次后缀）。"""
    text = str(code or "").strip()
    for candidate in (text, text[:4]):
        if not candidate:
            continue
        index_no = _CODE_MAP.get(candidate)
        paper = find(papers, index_no) if index_no else None
        if paper:
            return paper
    return None


def _by_name(name, papers):
    """名称兜底：关键词优先，其次与底稿名称互为包含，最后取最长公共前缀。"""
    text = str(name or "").strip()
    if not text:
        return None
    for index_no, keywords in _NAME_MAP:
        if any(keyword in text for keyword in keywords):
            paper = find(papers, index_no)
            if paper:
                return paper
    available = resolve_papers(papers)
    contained = [paper for paper in available if paper.name and (paper.name in text or text in paper.name)]
    if contained:
        return max(contained, key=lambda item: len(item.name))
    return None


def resolve(code, name="", papers=None):
    """返回 ``(Paper, 依据说明)``；无法对应时返回 ``(None, 原因)``。"""
    available = resolve_papers(papers)
    if not available:
        return None, "未加载底稿索引清单"
    if "坏账准备" in name:
        matched = _reserve_owner(name, available)
        return (matched, "按明确应收类别对应备抵科目") if matched else (None, "共享坏账准备需按应收类别确认归属")
    paper = _by_code(code, available)
    if paper:
        return paper, "按科目代码对应"
    paper = _by_name(name, available)
    if paper:
        return paper, "按科目名称对应"
    return None, "清单中无对应报表科目，请人工确认索引号"


def _reserve_owner(name, papers=None):
    targets = {index for word, index in (("应收账款", "1-070"), ("其他应收款", "1-130"),
               ("应收票据", "1-060"), ("长期应收款", "1-380"), ("预付账款", "1-090")) if word in name}
    return find(papers, targets.pop()) if len(targets) == 1 else None


def reserve_components(rows, paper, library=()):
    """共享备抵只按明确子类分派；整根核对不符或归属未知时停止相关项目。"""
    from decimal import Decimal
    if paper.index_no not in {"1-070", "1-130", "1-060", "1-380", "1-090"}:
        return []
    result = []
    root_paths = {row.path for row in rows if row.level == 1 and "坏账准备" in row.name}
    for row in rows:
        parent = row.path.rpartition("/")[0]
        if row.level == 2 and parent not in root_paths and parent.rpartition("/")[2] == "坏账准备":
            owner = _reserve_owner(row.name, library)
            if owner and owner.index_no == paper.index_no:
                result.append(row)
    for root in rows:
        if root.level != 1 or "坏账准备" not in root.name or _reserve_owner(root.name, library):
            continue
        children = [row for row in rows if root.path and row.level == 2
                    and row.path.rpartition("/")[0] == root.path and row.code.startswith(root.code)]
        owners = [_reserve_owner(row.name, library) for row in children]
        if not children or any(owner is None for owner in owners):
            raise ValueError("共享坏账准备缺少明确的应收类别，不能将整根备抵或零额静默归入本项目，请先确认科目对应关系。")
        if any(row.direction not in {"借", "贷"} for row in (root, *children)) or len({
            (row.subject_id, row.ledger_id, row.year, row.month, row.basis) for row in (root, *children)}) != 1:
            raise ValueError("共享坏账准备方向或账套期间不一致，请先核对来源。")
        for key in ("opening", "debit", "credit", "ending"):
            values = [getattr(row, key) for row in (root, *children)]
            if any(value is None for value in values):
                raise ValueError("共享坏账准备金额不完整，不能分派至报表项目。")
            signs = [-1 if key in ("opening", "ending") and row.direction == "贷" else 1 for row in (root, *children)]
            gap = values[0] * signs[0] - sum((value * sign for value, sign in zip(values[1:], signs[1:])), Decimal("0"))
            if abs(gap) > Decimal("0.005"):
                raise ValueError("共享坏账准备子类与总科目不一致，请先复核对应关系。")
        result.extend(row for row, owner in zip(children, owners) if owner.index_no == paper.index_no)
    return result


def options(papers=None):
    """下拉框用：索引号升序，报表项目按资产负债表、利润表顺序排列。"""
    def sort_key(paper):
        head, _, tail = paper.index_no.partition("-")
        try:
            return (int(head), int(tail) if tail.isdigit() else 0, paper.index_no)
        except ValueError:
            return (99, 0, paper.index_no)
    return sorted(resolve_papers(papers), key=sort_key)


def match_papers(papers=None):
    """诊断用：返回代码映射里所有能被清单命中的索引号集合。"""
    available = resolve_papers(papers)
    known = {paper.index_no for paper in available}
    return {code: index_no for code, index_no in _CODE_MAP.items() if index_no in known}
