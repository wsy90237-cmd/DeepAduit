"""科目底稿包：按 4100 货币资金底稿的版式，为一个一级科目生成整册 Excel。

版式对齐客户提供的模板：
- 统一四行抬头（被审计单位 / 索引号 / 页次 / 项目 / 编制人 / 复核人 / 期间 / 日期）；
- 底稿目录 -> 导引表 -> 程序表 -> 明细表 -> 凭证抽查 -> 披露表 -> 复核自检清单。

金额只来自本机科目余额表与序时账；函数不写死任何金额，未取得的数据一律留空或写“待补”。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import dxn_programs, paper_materials
from .models import money

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
DRAFT_NOTICE = "本册由 DeepAudit 依据本机账套自动生成，未附原始凭证与外部证据；结论与“已执行”栏须经注册会计师复核后方可归档。"
SAMPLE_LIMIT = 10          # 凭证抽查默认样本量，口径为金额绝对值降序
UNDISCLOSED = "待补"


# ---------- 程序模板 ----------

@dataclass(frozen=True)
class Procedure:
    text: str
    assertions: str = ""        # 认定：存在/完整性/权利和义务/计价和分摊/列报
    basis: str = ""             # 依据的底稿索引后缀，如 "1"
    section: str = ""           # 鼎信诺程序表的分节标题，如「(二)银行存款」


_COMMON_TAIL = (
    Procedure("检查期后事项，关注报表日后发生的、可能影响本科目余额的调整事项。", "存在 列报", "-2"),
    Procedure("取得管理层书面声明，确认本科目余额完整、无未披露的担保或抵质押限制。", "完整性 列报", "15"),
    Procedure("评价本科目披露是否按企业会计准则列报，附注分类与账面明细是否一致。", "列报", "15"),
    Procedure("结合舞弊风险评估，关注本科目是否存在异常交易、关联方安排或期末突击调整。", "存在 计价和分摊"),
    Procedure("评价持续经营假设对本科目计量与列报的影响，关注是否存在重大不确定性。", "列报"),
)

_PROCEDURES = {
    "cash": (
        Procedure("核对总账、明细账与科目余额表，确认本科目期初、本期发生额与期末余额一致。", "完整性 计价和分摊", "1"),
        Procedure("获取或编制货币资金余额明细表，复核加计并与总账数、日记账合计数核对相符。", "完整性", "1"),
        Procedure("监盘库存现金，将盘点金额与现金日记账余额核对，充抵现金的借条与未提现支票应在盘点表注明。", "存在 权利和义务", "2"),
        Procedure("获取并检查银行存款余额调节表，追查重大未达账项的性质与期后实现情况。", "存在 计价和分摊", "6"),
        Procedure("对银行存款、借款及与金融机构往来的其他重要信息实施函证，函证由项目组控制发函与回函。", "存在 权利和义务", "3"),
        Procedure("抽查大额货币资金收支的原始凭证，核对记账凭证与原始凭证、审批手续与入账期间。", "发生 完整性", "14"),
        Procedure("实施截止性测试，检查资产负债表日前后各若干日的收付记录，确认无跨期入账。", "截止", "5"),
        Procedure("检查定期存单、保证金账户与其他受限资金，评估是否需在附注单独披露。", "权利和义务 列报", "8"),
        Procedure("重新计算外币账户折算金额，核对使用的汇率与折算方法。", "计价和分摊", "11"),
        Procedure("取得已开立银行账户清单，核对开户、销户信息的完整性与真实性。", "完整性", "13"),
    ),
    "receivable": (
        Procedure("核对总账、明细账与科目余额表，确认本科目期初、本期发生额与期末余额一致。", "完整性 计价和分摊", "1"),
        Procedure("获取或编制往来款项明细表，按客户／供应商列示账龄，复核加计并与总账核对。", "完整性", "1"),
        Procedure("对主要往来余额实施函证，函证由项目组控制发函与回函，未回函项目实施替代程序。", "存在 权利和义务", "3"),
        Procedure("检查账龄划分与坏账准备计提政策，重新计算预期信用损失并与账面计提数比较。", "计价和分摊", "1"),
        Procedure("抽查本期新增与核销的往来款项，检查合同、发票、验收单与审批手续。", "发生 完整性", "2"),
        Procedure("检查与关联方及持股 5% 以上股东的往来，关注资金占用与披露完整性。", "列报", "15"),
        Procedure("实施截止性测试，检查资产负债表日前后的销售与收款记录，确认收入与应收的期间归属。", "截止", "2"),
        Procedure("检查期后回款情况，评估期末余额的可收回性。", "计价和分摊", "-2"),
    ),
    "inventory": (
        Procedure("核对总账、明细账与科目余额表，确认本科目期初、本期发生额与期末余额一致。", "完整性 计价和分摊", "1"),
        Procedure("获取或编制存货明细表，按类别列示数量、单价与金额，复核加计并与总账核对。", "完整性", "1"),
        Procedure("参与存货监盘，记录盘点范围、抽盘比例与差异处理，关注呆滞与毁损存货。", "存在 权利和义务", "2"),
        Procedure("对存货实施计价测试，核对采购发票、入库单与成本计算单。", "计价和分摊", "1"),
        Procedure("检查存货跌价准备的计提与转回，复核可变现净值的确定依据。", "计价和分摊", "1"),
        Procedure("抽查本期大额采购与领用凭证，检查审批手续与入账期间。", "发生 完整性", "2"),
        Procedure("检查存货是否用于担保、质押，评估受限存货的披露。", "权利和义务 列报", "15"),
    ),
    "asset": (
        Procedure("核对总账、明细账与科目余额表，确认本科目期初、本期发生额与期末余额一致。", "完整性 计价和分摊", "1"),
        Procedure("获取或编制资产明细表，列示原值、累计折旧／摊销与减值准备，复核加计并与总账核对。", "完整性", "1"),
        Procedure("检查本期新增资产的合同、发票、验收与产权证明，确认权属与入账价值。", "存在 权利和义务", "1"),
        Procedure("复核折旧／摊销政策与本期计提金额，重新计算并与账面比较。", "计价和分摊", "1"),
        Procedure("实地查看主要资产，关注闲置、报废与减值迹象。", "存在 计价和分摊", "2"),
        Procedure("检查资产是否用于抵押、担保，评估受限资产的披露。", "权利和义务 列报", "15"),
        Procedure("抽查本期处置与报废的审批手续与账务处理。", "发生 完整性", "2"),
        Procedure("取得权属证明或权利凭证，确认本科目资产归被审计单位所有或控制，是否存在抵押、质押或查封。",
                  "权利和义务 存在", "-1"),
        Procedure("复核期末计价方法与减值迹象，检查减值测试所用关键假设是否有支持性证据。",
                  "计价和分摊", "-1"),
        Procedure("检查本科目在报表中的列示分类是否正确，是否存在应予重分类或抵销的款项。",
                  "列报", "15"),
    ),
    "liability": (
        Procedure("核对总账、明细账与科目余额表，确认本科目期初、本期发生额与期末余额一致。", "完整性 计价和分摊", "1"),
        Procedure("获取或编制负债明细表，按债权人或款项性质列示，复核加计并与总账核对。", "完整性", "1"),
        Procedure("向主要债权人函证期末余额，未回函项目实施替代程序。", "存在 完整性", "3"),
        Procedure("检查借款合同、担保合同与抵押清单，复核利息计提与费用归属期间。", "计价和分摊 列报", "1"),
        Procedure("抽查本期新增与偿还的负债凭证，检查审批手续与入账期间。", "发生 完整性", "2"),
        Procedure("检查未入账负债，关注期末后付款的发票与费用报销。", "完整性", "2"),
        Procedure("检查一年内到期的非流动负债重分类是否正确。", "列报", "15"),
        Procedure("核对借款合同、担保合同与授信额度，确认期末余额、利率与到期日，检查违约条款是否触发。", "完整性 计价和分摊", "-1"),
        Procedure("复核利息计提与资本化处理，重新测算本期利息费用并与账面数比较。", "计价和分摊", "-1"),
        Procedure("检查关联方资金往来与担保事项，评估是否需在附注单独披露。", "列报", "15"),
    ),
    "equity": (
        Procedure("核对总账、明细账与科目余额表，确认本科目期初、本期发生额与期末余额一致。", "完整性 计价和分摊", "1"),
        Procedure("检查公司章程、股东会决议、验资报告与工商登记资料，确认实收资本／股本的变动依据。", "存在 权利和义务", "1"),
        Procedure("复核本期利润分配与盈余公积计提比例，检查是否符合章程与公司法规定。", "计价和分摊", "1"),
        Procedure("检查资本公积、其他综合收益的形成依据与计量。", "计价和分摊 列报", "1"),
        Procedure("核对所有者权益变动表与各权益科目发生额，确认勾稽一致。", "列报", "15"),
        Procedure("检查本期增减变动的工商变更登记、验资报告与董事会/股东会决议，确认变动依据完整。", "存在 完整性", "-1"),
        Procedure("复核利润分配与公积计提是否符合公司章程及公司法规定，检查分配决议与支付凭证。", "计价和分摊", "-1"),
        Procedure("检查库存股、其他综合收益等科目的计量与结转依据，确认不存在跨期或错误分类。", "计价和分摊 列报", "-1"),
    ),
    "revenue": (
        Procedure("核对总账、明细账与科目余额表，确认本科目期初、本期发生额与期末余额一致。", "完整性 计价和分摊", "1"),
        Procedure("了解收入确认政策，选取样本检查合同、发货单、验收单与发票，评价确认时点与金额。", "发生 计价和分摊", "1"),
        Procedure("执行收入截止性测试，检查资产负债表日前后各若干日的发货与开票记录。", "截止", "2"),
        Procedure("对主要客户实施函证，核对本期交易额与期末余额。", "发生 完整性", "3"),
        Procedure("执行分析程序，比较本期与上期收入结构、毛利率变动，识别异常波动。", "计价和分摊", "1"),
        Procedure("检查销售退回、折让与折扣的审批与账务处理，关注期后大额退回。", "发生 计价和分摊", "-2"),
        Procedure("检查关联方交易与收入披露的完整性。", "列报", "15"),
        Procedure("识别合同中的履约义务与交易价格，评价收入确认时点（时段/时点）是否符合准则规定。", "存在 计价和分摊", "-1"),
        Procedure("检查可变对价、重大融资成分、主要责任人/代理人判断对收入金额的影响。", "计价和分摊 列报", "-1"),
        Procedure("复核收入按产品、地区或渠道的分部披露与账面明细是否一致。", "列报", "15"),
    ),
    "expense": (
        Procedure("核对总账、明细账与科目余额表，确认本科目期初、本期发生额与期末余额一致。", "完整性 计价和分摊", "1"),
        Procedure("获取或编制费用明细表，按费用性质与部门列示，执行分析程序识别异常波动。", "完整性 计价和分摊", "1"),
        Procedure("抽查大额费用凭证，检查合同、发票、审批手续与费用归属期间。", "发生 完整性", "2"),
        Procedure("检查费用截止性，关注跨期入账与预提、待摊项目的摊销。", "截止", "2"),
        Procedure("复核职工薪酬、折旧摊销等计算性费用的计提依据。", "计价和分摊", "1"),
        Procedure("检查费用披露的分类与附注说明是否与账面一致。", "列报", "15"),
        Procedure("检查本期资本化支出与费用化支出的划分依据，评价是否存在应资本化而费用化的情形。", "计价和分摊", "-1"),
        Procedure("复核预提费用、待摊费用的计提与摊销依据，检查期后实际支付与计提数是否一致。", "计价和分摊", "-2"),
        Procedure("检查政府补助、政策性补贴的分类与确认时点，评价计入本科目的依据是否充分。", "完整性 列报", "15"),
    ),
}

_CATEGORY_KEYWORDS = (
    ("cash", ("货币资金", "库存现金", "银行存款")),
    ("inventory", ("存货", "原材料", "库存商品", "在产品", "周转材料")),
    ("receivable", ("应收", "预付", "其他应收", "合同资产")),
    ("liability", ("应付", "预收", "借款", "应交", "合同负债", "预计负债", "递延收益", "租赁负债")),
    ("equity", ("股本", "实收资本", "资本公积", "盈余公积", "未分配利润", "库存股", "其他综合收益", "专项储备")),
    ("revenue", ("收入", "营业收入")),
    ("expense", ("成本", "费用", "税金及附加", "损失", "收益", "营业外")),
)


def default_conclusion(consistent=True, pending=(), *, has_detail=True):
    """无模型时的结论口径：只写勾稽事实与缺证据，不代替注册会计师判断。"""
    if not has_detail:
        return "尚未取得二级明细，无法完成明细合计与一级余额的核对。当前仅列示一级账面记录，需补充明细及外部证据后判断是否需调整或追加程序。"
    if not consistent:
        return "需进一步程序：明细与科目余额表存在差异，须追查差异后再确定结论。"
    names = [str(item) for item in pending if str(item).strip()][:3]
    missing = "、".join(names) if names else "外部证据"
    return (f"待定（无异常／需调整／需进一步程序）：账面数与科目余额表勾稽一致（差异 0.00 元）；"
            f"{missing}尚未取得，暂不支持无异常结论。")


def baseline_notes(*, name, accounts, total, is_pl, consistent, gap, pending=(), has_detail=True):
    """无模型时的基础审计说明：只陈述账套事实、勾稽结果与缺资料，不写程序结论。"""
    def amount(value):
        return f"{value:,.2f}" if isinstance(value, Decimal) else "—"

    current = total.get("period") if is_pl else total.get("ending")
    lines = []
    if is_pl:
        lines.append(f"{name}本期发生额 {amount(current)} 元，按账套记录的收入贷方或费用借方累计发生额列示。"
                     "目前未取得上期发生额，需补充上年账套或已复核的上期明细，暂不能据此评价同比变化。")
    else:
        change = (current - total["opening"]) if current is not None and total.get("opening") is not None else None
        lines.append(f"{name}年初账面余额为 {amount(total.get('opening'))} 元，期末为 {amount(current)} 元，"
                     f"较年初变动 {amount(change)} 元。本期借方累计 {amount(total.get('debit'))} 元，"
                     f"贷方累计 {amount(total.get('credit'))} 元。以上金额取自本机账套科目余额表。")
    if not has_detail:
        lines.append("本科目未提供二级明细，本册直接列示一级账面记录，尚不能执行明细合计与一级余额的核对。")
    elif consistent:
        lines.append("本次对明细合计与一级科目余额进行了本地复算，期初、借方、贷方及期末四栏逐栏核对一致。"
                     "这项核对只说明账内数据相符，不代表银行函证、监盘或原始凭证检查已经完成。")
    else:
        lines.append(f"明细与一级科目的四栏核对存在差异，绝对差异合计为 {amount(gap)} 元。"
                     "需先核实取数范围、账户分类及缺失明细，再判断是否需要调整。")
    if pending:
        preview = "、".join(pending[:4]) + ("等" if len(pending) > 4 else "")
        lines.append(f"尚需取得 {len(pending)} 项资料，包括{preview}。变动原因及余额的真实性应结合这些资料进一步核实，"
                     "目前不作无异常结论。")
    else:
        lines.append("现有资料尚不足以替代原始凭证和外部证据，审定结论留待项目组复核。")
    return lines


def category(index_no, name):
    """按索引号前缀与底稿名称判定科目类别，用于选程序模板。"""
    if index_no.startswith("4-") or "收入" in name and "收益" not in name:
        return "revenue"
    if index_no.startswith("5-"):
        return "expense"
    if index_no.startswith("3-"):
        return "equity"
    if index_no.startswith("2-"):
        return "liability"
    for key, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in name for keyword in keywords):
            return key
    return "asset"


def procedures(index_no, name):
    """返回该科目的审计程序清单：鼎信诺库优先，分类模板与通用尾部补齐。"""
    kind = category(index_no, name)
    supplements = [{"text": item.text, "assertions": item.assertions.split(),
                    "basis": item.basis, "section": item.section}
                   for item in _PROCEDURES.get(kind, _PROCEDURES["asset"])]
    tail = [{"text": item.text, "assertions": item.assertions.split(),
             "basis": item.basis, "section": item.section} for item in _COMMON_TAIL]
    merged = dxn_programs.merge(dxn_programs.library(index_no), supplements, tail)
    if not merged:
        return _COMMON_TAIL
    return tuple(Procedure(item["text"], " ".join(item.get("assertions") or []),
                           str(item.get("basis", "")), str(item.get("section", "")))
                 for item in merged)


# ---------- 数据整理 ----------

def _amount(value):
    try:
        return money(value)
    except (ValueError, TypeError):
        return None


def _period(subject_label):
    """“主体 / 2025-12” → “2025年12月31日”。"""
    tail = str(subject_label or "").split("/")[-1].strip()
    if "-" in tail:
        year, _, month = tail.partition("-")
        if year.strip().isdigit() and month.strip().isdigit():
            return f"{year.strip()}年{int(month):02d}月{31 if int(month) in (1, 3, 5, 7, 8, 10, 12) else 30}日"
    return tail or UNDISCLOSED


def _change_rate(opening, ending):
    if opening in (None, 0):
        return "不适用"
    return (ending - opening) / abs(opening)


def period_amount(row):
    """损益类科目的本期数：收入按贷方发生额、费用按借方发生额，不含结转方向。"""
    debit, credit = _amount(row.debit) or Decimal("0"), _amount(row.credit) or Decimal("0")
    return credit if str(getattr(row, "direction", "")) == "贷" else debit


def summary_rows(rows, code, paper):
    """导引表数据：一级科目下各明细科目（无明细时本科目一行）+ 合计。"""
    target = next((row for row in rows if row.code == code), None)
    if target is None:
        return [], None
    prefix = f"{target.path}/" if target.path else ""
    children = [row for row in rows if prefix and row.path.startswith(prefix)]
    depth = min((row.level for row in children if row.level), default=target.level + 1)
    lines = [row for row in children if row.level == depth] or []
    detail = [{
        "code": row.code, "name": row.name, "level": row.level,
        "opening": None if row.opening is None else _amount(row.opening) * (1 if row.direction == target.direction else -1),
        "debit": _amount(row.debit), "credit": _amount(row.credit),
        "ending": None if row.ending is None else _amount(row.ending) * (1 if row.direction == target.direction else -1),
        "period": period_amount(row), "index": f"{paper.index_no}-1",
    } for row in lines]
    if not detail:
        detail = [{"code": target.code, "name": target.name, "level": target.level,
                   "opening": _amount(target.opening), "debit": _amount(target.debit),
                   "credit": _amount(target.credit), "ending": _amount(target.ending),
                   "period": period_amount(target), "index": f"{paper.index_no}-1"}]
    if any(item[key] is None for item in detail for key in ("opening", "debit", "credit", "ending")):
        raise ValueError("科目明细金额不完整，不能以零代替缺少的数据。")
    total = {
        "opening": sum((item["opening"] or Decimal("0")) for item in detail),
        "debit": sum((item["debit"] or Decimal("0")) for item in detail),
        "credit": sum((item["credit"] or Decimal("0")) for item in detail),
        "ending": sum((item["ending"] or Decimal("0")) for item in detail),
        "period": sum((item["period"] or Decimal("0")) for item in detail),
    }
    return detail, total


def reconcile(total, subject_row):
    """明细合计与科目余额表的勾稽：返回各栏差异与是否一致。"""
    ledger = {key: _amount(getattr(subject_row, key, None))
              for key in ("opening", "debit", "credit", "ending")}
    if any(ledger[key] is None or total.get(key) is None for key in ledger):
        raise ValueError("科目金额不完整，不能以零代替缺少的数据。")
    diff = {key: ledger[key] - (total.get(key) or Decimal("0")) for key in ledger}
    consistent = all(abs(value) <= Decimal("0.005") for value in diff.values())
    return {"ledger": ledger, "diff": diff, "consistent": consistent}


def sample_journal(journal, limit=SAMPLE_LIMIT):
    """凭证抽查样本：金额绝对值降序，口径固定为“大额优先”。"""
    if not journal:
        return []
    ordered = sorted(journal, key=lambda row: (-abs(row.amount or Decimal("0")), str(row.date), row.voucher))
    return ordered[:limit]


# ---------- 版式工具 ----------

class Sheet:
    """包装一张工作表，统一表头、边框与列宽。"""

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

    def header(self, title, *, index_no, page, project, prepared_by, reviewed_by, when, period,
               note="", last_column=8):
        """四行抬头：与企业底稿模板一致的字段顺序。"""
        self.merge(1, 1, last_column, title, font=TITLE_FONT, align=CENTER, height=28)
        split = max(2, last_column // 2)
        right = max(split + 1, last_column - 2)
        self.merge(2, 1, split, f"被审计单位：{project}", border=False)
        self.merge(2, split + 1, right, f"索引号：{index_no}", border=False)
        self.merge(2, right + 1, last_column, f"页次：{page}", border=False)
        self.merge(3, 1, split, f"项目：{title}", border=False)
        self.merge(3, split + 1, right, f"编制人：{prepared_by or '________'}", border=False)
        self.merge(3, right + 1, last_column, f"日期：{when}", border=False)
        self.merge(4, 1, split, f"财务报表截止日/期间：{period}", border=False)
        self.merge(4, split + 1, right, f"复核人：{reviewed_by or '________'}", border=False)
        self.merge(4, right + 1, last_column, f"日期：{when}", border=False)
        if note:
            lines = max(1, (len(note) + 88) // 89)
            self.merge(6, 1, last_column, note, font=SMALL_FONT, height=max(24, 16 * lines + 8))
        return 7

    def table(self, row, headers):
        for index, text in enumerate(headers, 1):
            cell = self.ws.cell(row=row, column=index, value=text)
            cell.font, cell.alignment, cell.border, cell.fill = HEAD_FONT, CENTER, BOX, HEAD_FILL
        self.ws.row_dimensions[row].height = 30
        return row + 1

    def line(self, row, values, *, money_columns=(), percent_columns=(), bold=False, fill=None,
             align=None, height=None):
        for index, value in enumerate(values, 1):
            cell = self.ws.cell(row=row, column=index, value=value)
            if isinstance(value, str):
                cell.data_type = "s"
            cell.font = HEAD_FONT if bold else BODY_FONT
            cell.border = BOX
            if fill is not None:
                cell.fill = fill
            if index in money_columns:
                cell.alignment = Alignment(horizontal="right", vertical="center")
                if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
                    cell.number_format = MONEY_FORMAT
            elif index in percent_columns:
                cell.alignment = Alignment(horizontal="right", vertical="center")
                cell.number_format = PERCENT_FORMAT
            else:
                cell.alignment = align or (LEFT if index in (2, 3, 4) else CENTER)
        if height:
            self.ws.row_dimensions[row].height = height
        return row + 1

    def page_setup(self, header_row=None):
        ws = self.ws
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        if header_row:
            ws.print_title_rows = f"{header_row}:{header_row}"


# ---------- 各工作表 ----------

def _write_catalog(book, state, entries):
    sheet = Sheet(book, "底稿目录", (6, 14, 34, 30, 8, 26))
    row = sheet.header(f"{state['name']}实质性底稿目录", index_no=state["index_no"], page=1,
                       project=state["project"], prepared_by=state["prepared_by"],
                       reviewed_by=state["reviewed_by"], when=state["date"],
                       period=state["period"], last_column=6,
                       note="使用说明：本目录列示本套底稿的全部工作表，索引号、页次与各工作表表头一致。")
    row = sheet.table(row, ("序号", "索引号", "底稿名称", "对应工作表名称", "页次", "备注"))
    for number, (index_no, title, sheet_name, note) in enumerate(entries, 1):
        row = sheet.line(row, (number, index_no, title, sheet_name, number + 1, note), height=22)
    row = sheet.line(row, ("合计", f"共 {len(entries)} 份底稿", "—", "—", "—", "—"), bold=True,
                     fill=TOTAL_FILL, height=22)
    row += 1
    sheet.merge(row, 1, 6, "勾稽说明", font=HEAD_FONT, height=22)
    for text in (
            "1、本目录所列底稿与工作簿中同名工作表一一对应，可按“对应工作表名称”直接查阅；",
            "2、索引号取自《审计业务索引号及底稿名称清单》，标黄行（资料性与程序性表格）未纳入报表科目；",
            "3、页次为工作簿内统一编号，打印装订调整顺序时请同步更新本目录与各表表头；",
            "4、金额单位为人民币元，取数口径为鼎信诺科目余额表与序时账，未取得的数据写“待补”；",
            DRAFT_NOTICE):
        row += 1
        sheet.merge(row, 1, 6, text, font=SMALL_FONT, height=22)
    row += 2
    sheet.merge(row, 1, 6, "审计结论（无异常／需调整／需进一步程序）", font=HEAD_FONT, height=22)
    sheet.merge(row + 1, 1, 6, "待补：由项目组根据已执行程序与证据情况填写。", font=SMALL_FONT, height=26)
    sheet.page_setup()
    return sheet


def _write_guide(book, state, detail, total, draft_body, notes=(), check=None, conclusion=""):
    is_pl = state["is_pl"]
    widths = (30, 18, 16, 18, 12, 14, 30) if is_pl else (26, 16, 18, 14, 18, 12, 14, 30)
    sheet = Sheet(book, f"{state['index_no']} 导引表", widths)
    columns = 7 if is_pl else 8
    row = sheet.header(f"{state['name']}导引表", index_no=state["index_no"], page=2,
                       project=state["project"], prepared_by=state["prepared_by"],
                       reviewed_by=state["reviewed_by"], when=state["date"],
                       period=state["period"], last_column=columns,
                       note=f"科目：{state['code']} {state['subject_name']}　｜　"
                            f"底稿名称：{state['name']}　｜　{state['direction']}方科目　｜　"
                            f"级次：{state['level_label']}　｜　{state['amount_source']}"
                            + ("　｜　损益类按本期发生额列示（收入取贷方、费用取借方，不含结转）"
                               if is_pl else ""))
    if is_pl:
        row = sheet.table(row, ("项目", "本期未审数", "审计调整", "本期审定数", "占比", "索引号", "备注"))
        base = total["period"]
        for item in detail:
            share = (item["period"] / base) if base else None
            row = sheet.line(row, (f"{item['code']} {item['name']}", item["period"], None, None,
                                   share, item["index"], ""),
                             money_columns=(2, 3, 4), percent_columns=(5,), height=22)
        row = sheet.line(row, ("合计", base, None, None, 1 if base else None, "—",
                               "审计调整栏由项目组填列"),
                         money_columns=(2, 3, 4), percent_columns=(5,), bold=True,
                         fill=TOTAL_FILL, height=22)
    else:
        row = sheet.table(row, ("项目", "期初余额", "期末余额（调整前）", "审计调整", "期末余额（调整后）",
                                "变动比例", "索引号", "备注"))
        for item in detail:
            opening, ending = item["opening"], item["ending"]
            row = sheet.line(row, (f"{item['code']} {item['name']}", opening, ending, None, None,
                                   _change_rate(opening, ending) if opening not in (None, 0) else "不适用",
                                   item["index"], ""),
                             money_columns=(2, 3, 4, 5), percent_columns=(6,), height=22)
        row = sheet.line(row, ("合计", total["opening"], total["ending"], None, None,
                               _change_rate(total["opening"], total["ending"])
                               if total["opening"] not in (None, 0) else "不适用",
                               "—", "审计调整栏由项目组填列"), money_columns=(2, 3, 4, 5),
                         percent_columns=(6,), bold=True, fill=TOTAL_FILL, height=22)
    row += 1
    if check:
        verdict = "一致" if check["consistent"] else "不一致，需追查"
        figure = total["period"] if is_pl else total["ending"]
        ledger = check["ledger"]["credit"] if is_pl else check["ledger"]["ending"]
        gap = check["diff"]["credit"] if is_pl else check["diff"]["ending"]
        label = "本期发生额" if is_pl else "期末余额"
        sheet.merge(row, 1, columns,
                    f"勾稽：明细合计{label} {figure:,.2f} 元，科目余额表 {ledger:,.2f} 元，"
                    f"差异 {gap:,.2f} 元（{verdict}）。",
                    font=SMALL_FONT, height=22)
        row += 1
    sheet.merge(row, 1, columns, "审计说明（模型草稿，需复核）", font=HEAD_FONT, height=22)
    body_lines = [str(text).strip() for text in notes if str(text).strip()]
    if not body_lines:
        body_lines = [text.strip() for text in str(draft_body or "").splitlines() if text.strip()]
    if not body_lines:
        body_lines = ["待补：未生成文字说明。"]
    for text in body_lines[:40]:
        row += 1
        sheet.merge(row, 1, columns, text.strip(), font=SMALL_FONT,
                    height=max(20, 15 * (len(text) // 70 + 1)))
    row += 2
    sheet.merge(row, 1, columns, "审计结论（无异常／需调整／需进一步程序）", font=HEAD_FONT, height=22)
    row += 1
    conclusion = (str(conclusion).strip()
                  or default_conclusion(True if check is None else check["consistent"]))
    sheet.merge(row, 1, columns, conclusion, font=SMALL_FONT, height=30)
    row += 2
    sheet.merge(row, 1, columns, DRAFT_NOTICE, font=SMALL_FONT,
                fill=PatternFill("solid", fgColor="FFF8E6"), height=28)
    sheet.page_setup()
    return sheet


def _write_procedures(book, state, rows_procedures, notes=()):
    sheet = Sheet(book, f"{state['index_no']}-0 程序表", (20, 6, 58, 12, 9, 9, 11, 11, 9, 20))
    row = sheet.header(f"{state['name']}审计程序表", index_no=f"{state['index_no']}-0", page=3,
                       project=state["project"], prepared_by=state["prepared_by"],
                       reviewed_by=state["reviewed_by"], when=state["date"],
                       period=state["period"], last_column=10,
                       note="口径：程序清单按底稿模板的实质性程序框架整理，列示程序不表示已经执行；"
                            "同一程序在不同风险类别下是否必做，仍按事务所质量控制制度判断。")
    row = sheet.table(row, ("科目", "序号", "审计程序", "索引号", "存在", "完整性",
                            "权利和义务", "计价和分摊", "列报", "备注"))
    last_section = ""
    for number, item in enumerate(rows_procedures, 1):
        target = "—"
        if item.basis == "-2":          # 期后事项：索引到凭证抽查表
            target = f"{state['index_no']}-2"
        elif item.basis:
            target = f"{state['index_no']}-{item.basis}"
        marks = {key: ("√" if key in item.assertions else "") for key in
                 ("存在", "完整性", "权利和义务", "计价和分摊", "列报")}
        if item.section and item.section != last_section:
            last_section, group = item.section, item.section
        else:
            group = state["name"] if number == 1 else ""
        rows_needed = len(str(item.text)) // 30 + 1
        row = sheet.line(row, (group, number, item.text,
                               target, marks["存在"], marks["完整性"],
                               marks["权利和义务"], marks["计价和分摊"], marks["列报"], ""),
                         align=LEFT, height=max(30, 14 * rows_needed + 6))
    sheet.page_setup(7)
    return sheet


def _write_detail(book, state, detail, total, check):
    sheet = Sheet(book, f"{state['index_no']}-1 {state['name']}明细表", (7, 16, 26, 16, 16, 16, 16, 10, 22))
    row = sheet.header(f"{state['name']}明细表", index_no=f"{state['index_no']}-1", page=4,
                       project=state["project"], prepared_by=state["prepared_by"],
                       reviewed_by=state["reviewed_by"], when=state["date"],
                       period=state["period"], last_column=9,
                       note="审计标识：G 与总分类账核对一致｜S 与明细账核对一致｜∧ 纵加核对｜＜ 横加核对。"
                            "明细项目取自科目余额表的下级科目；需要按往来单位列示的，应结合辅助核算或序时账补充。")
    row = sheet.table(row, ("序号", "科目编码", "明细科目", "期初余额", "本期借方发生", "本期贷方发生",
                            "期末余额", "占比", "审计标识"))
    key = "period" if state["is_pl"] else "ending"
    base = sum((abs(item[key]) for item in detail if item[key] is not None), Decimal("0"))
    for number, item in enumerate(detail, 1):
        share = (abs(item[key]) / base) if base else None
        row = sheet.line(row, (number, item["code"], item["name"], item["opening"], item["debit"],
                               item["credit"], item["ending"], share, "G S ∧"),
                         money_columns=(4, 5, 6, 7), align=LEFT, height=22)
        # 占比列单独设百分比格式
        sheet.ws.cell(row=row - 1, column=8).number_format = PERCENT_FORMAT
        sheet.ws.cell(row=row - 1, column=8).alignment = Alignment(horizontal="right", vertical="center")
    row = sheet.line(row, ("合计", "—", "—", total["opening"], total["debit"], total["credit"],
                           total["ending"], 1 if base else None, "∧ ＜"),
                     money_columns=(4, 5, 6, 7), bold=True, fill=TOTAL_FILL, height=22)
    sheet.ws.cell(row=row - 1, column=8).number_format = PERCENT_FORMAT
    ledger, diff = check["ledger"], check["diff"]
    sheet.merge(row, 1, 3, f"科目余额表（{state['code']} {state['subject_name']}）",
                font=BODY_FONT, align=LEFT, height=22)
    for column, value in zip((4, 5, 6, 7),
                             (ledger["opening"], ledger["debit"], ledger["credit"], ledger["ending"])):
        cell = sheet.ws.cell(row=row, column=column, value=value)
        cell.font = BODY_FONT
        cell.alignment = Alignment(horizontal="right", vertical="center")
        cell.number_format, cell.border = MONEY_FORMAT, BOX
    sheet.ws.cell(row=row, column=9, value="G").alignment = CENTER
    sheet.ws.cell(row=row, column=9).border = BOX
    row += 1
    sheet.merge(row, 1, 3, "差异（明细合计－科目余额表）", font=HEAD_FONT, align=LEFT, height=22,
                fill=None if check["consistent"] else PatternFill("solid", fgColor="FDE8E8"))
    for column, value in zip((4, 5, 6, 7),
                             (diff["opening"], diff["debit"], diff["credit"], diff["ending"])):
        cell = sheet.ws.cell(row=row, column=column, value=value)
        cell.font, cell.border = HEAD_FONT, BOX
        cell.alignment = Alignment(horizontal="right", vertical="center")
        cell.number_format = MONEY_FORMAT
        if not check["consistent"]:
            cell.fill = PatternFill("solid", fgColor="FDE8E8")
    verdict = sheet.ws.cell(row=row, column=9,
                            value="一致" if check["consistent"] else "不一致，需追查")
    verdict.font, verdict.border, verdict.alignment = HEAD_FONT, BOX, CENTER
    row += 1
    row += 1
    sheet.merge(row, 1, 9, "勾稽校验", font=HEAD_FONT, height=22)
    row += 1
    sheet.merge(row, 1, 9, "明细合计应与科目余额表本科目发生额、期末余额一致；不一致时须追查差异，"
                           "不得直接调整明细。", font=SMALL_FONT, height=24)
    sheet.page_setup(7)
    return sheet


def _write_journal(book, state, samples, journal):
    sheet = Sheet(book, f"{state['index_no']}-2 凭证抽查表", (7, 13, 14, 30, 12, 20, 15, 15, 20))
    total = len(journal or [])
    row = sheet.header(f"{state['name']}凭证抽查表", index_no=f"{state['index_no']}-2", page=5,
                       project=state["project"], prepared_by=state["prepared_by"],
                       reviewed_by=state["reviewed_by"], when=state["date"],
                       period=state["period"], last_column=9,
                       note=f"抽样口径：序时账共 {total} 笔，按金额绝对值降序取前 {len(samples)} 笔，属初步样本；"
                            "项目组应按重要性水平与风险调整样本量，并补充凭证附件检查结论。")
    row = sheet.table(row, ("序号", "日期", "凭证号", "摘要", "科目编码", "对方科目", "借方金额",
                            "贷方金额", "检查结论"))
    for number, item in enumerate(samples, 1):
        row = sheet.line(row, (number, str(item.date), item.voucher, item.summary, item.code,
                               item.contra, item.debit, item.credit, "待补（未核对凭证附件）"),
                         money_columns=(7, 8), align=LEFT, height=22)
    if not samples:
        row = sheet.line(row, ("—", "—", "—", "本期无序时账记录或未读取序时账", "—", "—", None, None, "—"),
                         align=LEFT, height=24)
    sheet.page_setup(7)
    return sheet


def _write_disclosure(book, state, detail, total, notes=()):
    sheet = Sheet(book, f"{state['index_no']}-15 披露表", (34, 18, 18, 16, 42, 12))
    row = sheet.header(f"{state['name']}披露表", index_no=f"{state['index_no']}-15", page=6,
                       project=state["project"], prepared_by=state["prepared_by"],
                       reviewed_by=state["reviewed_by"], when=state["date"],
                       period=state["period"], last_column=6,
                       note="程序依据：检查附注是否按报表项目分别列示，并单独说明受限、境外及存在回收风险的款项。")
    is_pl = state["is_pl"]
    row = sheet.table(row, ("项目", "本期发生额", "审计调整", "本期审定数", "附注披露说明", "索引号")
                      if is_pl else
                      ("项目", "期末余额", "年初余额", "与审定数差异", "附注披露说明", "索引号"))
    mapped = {}
    for entry in notes:
        name = str(entry.get("item", "")).replace(state["name"] + "_", "").strip()
        if name:
            mapped[name] = entry.get("note", UNDISCLOSED)
    default_note = mapped.get(state["subject_name"]) or mapped.get(state["name"])
    for item in detail:
        note = (mapped.get(item["name"]) or mapped.get(f"{state['name']}_{item['name']}")
                or default_note or UNDISCLOSED)
        values = ((item["period"], None, None) if is_pl
                  else (item["ending"], item["opening"], None))
        row = sheet.line(row, (f"{state['name']}_{item['name']}", *values, note,
                               f"{state['index_no']}-1"),
                         money_columns=(2, 3, 4), align=LEFT, height=22)
    total_values = ((total["period"], None, None) if is_pl
                    else (total["ending"], total["opening"], None))
    row = sheet.line(row, ("合　　计", *total_values, "—", "—"),
                     money_columns=(2, 3, 4), bold=True, fill=TOTAL_FILL, align=LEFT, height=22)
    sheet.page_setup(7)
    return sheet


def _write_materials(book, state, items):
    """资料需求清单：先出表格，缺什么一目了然。"""
    sheet = Sheet(book, f"{state['index_no']}-9 资料需求清单", (6, 40, 32, 16, 12, 12, 24))
    row = sheet.header(f"{state['name']}资料需求清单", index_no=f"{state['index_no']}-9", page=6,
                       project=state["project"], prepared_by=state["prepared_by"],
                       reviewed_by=state["reviewed_by"], when=state["date"],
                       period=state["period"], last_column=7,
                       note="待补资料未到齐前，相关程序只能登记为「待补」，不得出具无异常结论。")
    row = sheet.table(row, ("序号", "资料名称", "用途／对应程序", "提供方", "是否取得", "索引号", "备注"))
    missing = 0
    for number, item in enumerate(items, 1):
        ready = "已取得" if item["ready"] else "待补"
        missing += 0 if item["ready"] else 1
        row = sheet.line(row, (number, item["name"], item["usage"], item["source"], ready,
                               f"{state['index_no']}-9",
                               "账套直接取数" if item["ready"] else "取得后归档"),
                         align=LEFT, height=22)
    row += 1
    sheet.merge(row, 1, 7, f"共 {len(items)} 项，已取得 {len(items) - missing} 项，待补 {missing} 项。",
                font=SMALL_FONT, fill=TOTAL_FILL, height=24)
    sheet.page_setup(7)
    return sheet


def _write_review(book, state, checks, conclusion=""):
    sheet = Sheet(book, f"9720-{state['index_no']} 复核自检清单", (7, 46, 16, 40))
    row = sheet.header(f"{state['name']}底稿项目经理一级复核自检清单",
                       index_no=f"9720-{state['index_no']}", page=7,
                       project=state["project"], prepared_by=state["prepared_by"],
                       reviewed_by=state["reviewed_by"], when=state["date"],
                       period=state["period"], last_column=4,
                       note="复核自检只覆盖格式、索引与勾稽，不替代项目经理对审计证据充分适当性的判断。")
    row = sheet.table(row, ("序号", "复核要点", "自检结果", "说明"))
    for number, (text, result, note) in enumerate(checks, 1):
        row = sheet.line(row, (number, text, result, note), align=LEFT,
                         height=max(24, 15 * (len(text) // 40 + 1)))
    row += 1
    sheet.merge(row, 1, 4, f"复核结论：{conclusion or '待项目合伙人复核后填写。'}", font=HEAD_FONT, height=24)
    sheet.page_setup(7)
    return sheet


# ---------- 入口 ----------

def build(path, *, paper, subject_label, subject_row, rows, journal=None, draft_body="",
          prepared_by="", reviewed_by="", when="", export_time="", narrative=None):
    """生成一册 Excel 底稿；返回保存路径。"""
    if subject_row is None:
        raise ValueError("未找到该科目，请重新读取科目余额。")
    if paper is None:
        raise ValueError("未确认报表项目索引，不能归集生成底稿。")
    if len({(row.subject_id, row.year, row.month, row.ledger_id, row.basis) for row in rows}) != 1:
        raise ValueError("科目数据包含不同账套、主体、期间或来源口径，不能混合导出。")
    by_code = {}
    for row in rows:
        if row.code in by_code and by_code[row.code] != row:
            raise ValueError(f"科目编码 {row.code} 存在冲突，需人工确认来源。")
        if row.direction not in ("借", "贷"):
            raise ValueError(f"科目 {row.code} 缺少明确的借贷方向。")
        by_code[row.code] = row
    if by_code.get(subject_row.code) != subject_row:
        raise ValueError("指定科目不属于当前账套来源。")
    rows = list(by_code.values())
    project = str(subject_label or "").split("/")[0].strip() or UNDISCLOSED
    period = _period(subject_label)
    index_no = paper.index_no if paper else UNDISCLOSED
    name = paper.name if paper else subject_row.name
    state = {
        "index_no": index_no, "name": name, "project": project, "period": period,
        "prepared_by": prepared_by, "reviewed_by": reviewed_by,
        "date": when or export_time or UNDISCLOSED,
        "code": subject_row.code, "subject_name": subject_row.name, "level_label": subject_row.level and f"{subject_row.level}级科目" or "—",
        "direction": subject_row.direction or "—",
        "amount_source": "金额来源：本机账套" + ("与序时账" if journal else ""),
        "is_pl": bool(getattr(subject_row, "is_profit_loss", False)) or index_no.startswith(("4-", "5-")),
    }
    detail, total = summary_rows(rows, subject_row.code, paper)
    if total is None:
        raise ValueError("该科目没有可列示的余额数据。")
    check = reconcile(total, subject_row)
    materials = paper_materials.for_paper(index_no, name)
    pending = paper_materials.pending(materials)
    if not (narrative or {}).get("guide_notes"):
        draft_body = draft_body or ""
        state_notes = baseline_notes(name=name, accounts=detail, total=total, is_pl=state["is_pl"],
                                     consistent=check["consistent"], gap=check["diff"]["ending"],
                                     pending=pending)
    else:
        state_notes = ()
    samples = sample_journal(journal)
    book = Workbook()
    book.remove(book.active)

    entries = [
        (index_no, f"{name}导引表", f"{index_no} 导引表", "含审计说明与索引勾稽"),
        (f"{index_no}-0", f"{name}审计程序表", f"{index_no}-0 程序表", f"共 {len(procedures(index_no, name))} 条程序"),
        (f"{index_no}-1", f"{name}明细表", f"{index_no}-1 {name}明细表", "取自科目余额表"),
        (f"{index_no}-2", f"{name}凭证抽查表", f"{index_no}-2 凭证抽查表",
         f"序时账 {len(journal or [])} 笔，抽 {len(samples)} 笔"),
        (f"{index_no}-15", f"{name}披露表", f"{index_no}-15 披露表", "与审定数勾稽"),
        (f"{index_no}-9", f"{name}资料需求清单", f"{index_no}-9 资料需求清单", "待补资料提醒"),
        (f"9720-{index_no}", "本底稿项目经理一级复核自检清单",
         f"9720-{index_no} 复核自检清单", "格式、索引与勾稽自检"),
    ]
    narrative = narrative or {}
    _write_catalog(book, state, entries)
    _write_guide(book, state, detail, total, draft_body,
                 narrative.get("guide_notes") or state_notes, check,
                 narrative.get("conclusion") or default_conclusion(check["consistent"], pending))
    _write_procedures(book, state, procedures(index_no, name), narrative.get("procedure_notes", ()))
    _write_detail(book, state, detail, total, check)
    _write_journal(book, state, samples, journal)
    _write_disclosure(book, state, detail, total, narrative.get("disclosure_notes", ()))

    checks = [
        ("底稿目录、索引导引表与各工作表名称一致", "已核对", "自动生成时按同一索引号命名"),
        ("明细表合计与科目余额表本科目期末余额一致",
         "已核对" if check["consistent"] else "不一致，待追查",
         f"明细合计 {total['ending']:,.2f}，差异 {check['diff']['ending']:,.2f}"),
        ("导引表期末余额与明细表合计一致", "已核对", "同一取数口径"),
        ("凭证抽查表样本与序时账一致", "已核对" if samples else "不适用",
         f"样本 {len(samples)} 笔，仅按金额降序抽取"),
        ("编制人、复核人签字齐全", "已填列" if prepared_by and reviewed_by else "待补",
         "由工作台维护的签字人自动带入"),
        ("审计调整仅影响导引表“审计调整”栏", "待复核", "自动生成不填调整"),
        ("所有“待补”栏已补充证据与结论", "待复核", "模型草稿不得直接归档"),
        ("披露表与附注勾稽一致", "待复核", "附注定稿后复核"),
    ]
    _write_materials(book, state, materials)
    _write_review(book, state, checks, narrative.get("review_conclusion", ""))
    target = Path(path)
    book.save(target)
    return target
