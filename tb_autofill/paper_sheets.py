"""各科目底稿的专项工作表清单与预填数据。

原则：**账套里有什么才出什么**。
- 货币资金只有银行存款时，不出库存现金监盘表；
- 有银行账户就出「银行函证信息汇总表」并预填开户行、账号、函证金额，其余栏留给现场填；
- 每个科目都出「抽凭表」，凭证由序时账按风险挑选（接模型时由模型挑并写理由）。

通用四表（审定表、账项明细表、程序表、披露表）与资料清单由 dxn_paper 生成，不在此声明。
"""

from __future__ import annotations

from decimal import Decimal
import re

from .models import money

TEXT, MONEY, DATE, NUM = "text", "money", "date", "num"
CUTOFF_SHEETS = {"货币资金截止性测试", "收入截止性测试表", "费用截止性测试表"}
CUTOFF_COLUMNS = (("记账日期", 14, DATE), ("凭证号", 16, TEXT), ("分录号", 10, TEXT),
                  ("科目编码", 14, TEXT), ("摘要", 32, TEXT), ("借方", 20, MONEY),
                  ("贷方", 20, MONEY), ("抽取理由", 40, TEXT), ("单据/验收日期（人工）", 22, DATE),
                  ("业务归属期间（人工）", 22, TEXT), ("是否跨期（人工）", 20, TEXT))

# (表名, 列定义, 说明, 预留行数, 生成条件)
# 生成条件为 facts 的键；None 表示无条件生成。
BASE = (
    ("分析程序表", (("项目", 26, TEXT), ("本期数", 16, MONEY), ("上期数", 16, MONEY),
                    ("变动额", 16, MONEY), ("变动率", 12, TEXT), ("异常说明", 36, TEXT)),
     "对本期与上期数进行比较分析，异常波动须在说明栏记录原因与进一步程序。", 12, None),
    ("抽凭表", (("序号", 6, NUM), ("日期", 13, DATE), ("凭证号", 14, TEXT), ("摘要", 28, TEXT),
                ("科目编码", 12, TEXT), ("对方科目", 18, TEXT), ("借方", 15, MONEY),
                ("贷方", 15, MONEY), ("抽取理由", 26, TEXT), ("检查结论", 20, TEXT)),
     "按金额、整数金额、期末前后、异常摘要等风险特征从序时账抽取 10 笔；接入模型时由模型挑选并说明理由。"
     "检查结论须核对原始凭证后填写。", 10, None),
)

SPECS = {
    "cash": (
        ("银行账户清单", (("序号", 6, NUM), ("开户银行", 26, TEXT), ("银行账号", 24, TEXT),
                          ("账户性质", 14, TEXT), ("币种", 10, TEXT), ("期末余额", 16, MONEY),
                          ("对账单金额", 16, MONEY), ("差异", 14, MONEY), ("是否函证", 10, TEXT)),
         "账户行名与账号取自科目余额表的下级科目名称，需与已开立银行账户清单核对；"
         "对账单金额与差异待取得对账单后填列。", 12, "bank"),
        ("银行函证信息汇总表", (("序号", 6, NUM), ("开户银行", 26, TEXT), ("账号", 22, TEXT),
                                ("函证金额", 16, MONEY), ("发函日期", 13, DATE),
                                ("回函日期", 13, DATE), ("回函金额", 16, MONEY),
                                ("是否相符", 10, TEXT), ("替代程序", 24, TEXT)),
         "开户行、账号与函证金额已按账面预填，发函与回函信息由项目组现场登记；"
         "函证须由项目组控制发函与回函，未回函执行替代程序。", 10, "bank"),
        ("银行存款余额调节表", (("项目", 34, TEXT), ("银行对账单", 18, MONEY), ("企业账面", 18, MONEY)),
         "按账户分别编制；调节后余额应相等，未达账项须注明性质与期后实现情况。", 16, "bank"),
        ("库存现金监盘表", (("项目", 30, TEXT), ("账面数", 16, MONEY), ("盘点数", 16, MONEY),
                            ("差异", 14, MONEY), ("说明", 34, TEXT)),
         "记录监盘时间、参加人员、盘点过程与倒轧结果；白条抵库、未提现支票须单独列示。", 10, "cash"),
        ("货币资金截止性测试", (("日期", 13, DATE), ("凭证号", 14, TEXT), ("摘要", 28, TEXT),
                                ("借方", 16, MONEY), ("贷方", 16, MONEY), ("跨期判断", 16, TEXT)),
         "选取资产负债表日前后各若干日的收付凭证，检查是否存在跨期入账。", 12, "any_cash"),
    ),
    "receivable": (
        ("账龄分析表", (("单位名称", 30, TEXT), ("期末余额", 16, MONEY), ("1年以内", 15, MONEY),
                        ("1-2年", 15, MONEY), ("2-3年", 15, MONEY), ("3年以上", 15, MONEY),
                        ("计提比例", 10, TEXT), ("坏账准备", 15, MONEY)),
         "客户名称与期末余额已按明细预填，账龄与计提比例待取得账龄表后填列。", 16, None),
        ("函证信息汇总表", (("序号", 6, NUM), ("单位名称", 28, TEXT), ("期末余额", 16, MONEY),
                            ("发函金额", 16, MONEY), ("回函金额", 16, MONEY),
                            ("是否相符", 10, TEXT), ("替代程序", 28, TEXT)),
         "按期末余额由大到小预填前 10 户，发函与回函情况现场登记；未回函执行替代程序。", 12, None),
        ("替代测试表", (("单位名称", 26, TEXT), ("合同/订单", 22, TEXT), ("发票", 18, TEXT),
                        ("发运或验收单", 20, TEXT), ("回款记录", 22, TEXT), ("结论", 16, TEXT)),
         "对未函证或未回函的往来余额，检查合同、发票、发运与回款等支持性文件。", 12, None),
        ("坏账准备计算表", (("账龄区间", 16, TEXT), ("余额", 16, MONEY), ("计提比例", 12, TEXT),
                            ("应计提", 16, MONEY), ("账面计提", 16, MONEY), ("差异", 14, MONEY)),
         "按预期信用损失模型重新计算，与账面计提数比较并说明差异。", 8, None),
    ),
    "inventory": (
        ("存货盘点表", (("存货类别", 24, TEXT), ("规格型号", 18, TEXT), ("账面数量", 12, NUM),
                        ("盘点数量", 12, NUM), ("差异", 10, NUM), ("单价", 12, MONEY),
                        ("账面金额", 16, MONEY), ("盘点金额", 16, MONEY)),
         "类别与账面金额已按明细预填，盘点数量与差异待监盘后填列；呆滞毁损单独列示。", 16, None),
        ("存货计价测试表", (("存货类别", 24, TEXT), ("数量", 12, NUM), ("账面单价", 13, MONEY),
                            ("测试单价", 13, MONEY), ("差异", 14, MONEY), ("测试依据", 30, TEXT)),
         "选取样本核对采购发票、入库单与成本计算单。", 12, None),
        ("存货跌价准备测试表", (("存货类别", 24, TEXT), ("账面成本", 16, MONEY),
                                ("可变现净值", 16, MONEY), ("应计提", 16, MONEY),
                                ("已计提", 16, MONEY), ("差异", 14, MONEY)),
         "可变现净值以估计售价减至完工时估计成本、销售费用与税费确定，须有支持证据。", 10, None),
    ),
    "asset": (
        ("资产明细及折旧摊销表", (("资产名称", 26, TEXT), ("原值", 16, MONEY),
                                  ("累计折旧/摊销", 16, MONEY), ("减值准备", 15, MONEY),
                                  ("账面价值", 16, MONEY), ("本期折旧/摊销", 16, MONEY),
                                  ("权属证书", 22, TEXT)),
         "按本册原值、累计折旧/摊销及减值科目填列，只有明细名称唯一对应时才计算分类净额；"
         "不能对应的项目保留空白，权属及本期折旧摊销仍需独立来源。", 16, None),
        ("权属证明检查表", (("资产名称", 26, TEXT), ("权属证书编号", 24, TEXT),
                            ("权利人", 24, TEXT), ("抵押/质押情况", 24, TEXT),
                            ("检查结论", 18, TEXT)),
         "核对不动产权证、车辆登记证、专利证书等原件，确认权属与受限情况。", 10, None),
        ("资产减值测试表", (("资产组", 24, TEXT), ("账面价值", 16, MONEY),
                            ("可收回金额", 16, MONEY), ("减值金额", 16, MONEY),
                            ("关键假设", 34, TEXT)),
         "可收回金额取公允价值减处置费用与预计未来现金流量现值孰高，关键假设须有依据。", 10, None),
    ),
    "liability": (
        ("借款及利息测算表", (("贷款机构", 24, TEXT), ("借款金额", 16, MONEY), ("利率", 10, TEXT),
                              ("起止日期", 20, TEXT), ("期末余额", 16, MONEY),
                              ("本期利息", 15, MONEY), ("测算利息", 15, MONEY), ("差异", 13, MONEY)),
         "核对借款合同、授信协议与还款计划，重新测算利息并检查资本化处理。", 12, None),
        ("未入账负债检查表", (("期后付款项目", 30, TEXT), ("金额", 16, MONEY), ("发票日期", 13, DATE),
                              ("所属期间", 14, TEXT), ("是否应入账", 12, TEXT), ("处理意见", 24, TEXT)),
         "检查资产负债表日后付款的发票与费用报销，识别跨期未入账负债。", 12, None),
        ("往来账龄分析表", (("单位名称", 30, TEXT), ("期末余额", 16, MONEY), ("1年以内", 15, MONEY),
                            ("1-2年", 15, MONEY), ("2-3年", 15, MONEY), ("3年以上", 15, MONEY),
                            ("备注", 26, TEXT)),
         "按债权人列示账龄，关注长期挂账与异常余额。", 16, None),
    ),
    "equity": (
        ("所有者权益变动检查表", (("项目", 26, TEXT), ("期初", 16, MONEY), ("本期增加", 16, MONEY),
                                  ("本期减少", 16, MONEY), ("期末", 16, MONEY), ("依据", 30, TEXT)),
         "与所有者权益变动表、各权益科目发生额核对一致。", 10, None),
        ("验资与决议检查表", (("事项", 26, TEXT), ("文件名称", 30, TEXT), ("文号/日期", 20, TEXT),
                              ("检查结论", 20, TEXT)),
         "检查公司章程、验资报告、股东会决议与工商登记资料。", 10, None),
    ),
    "revenue": (
        ("收入明细及毛利率分析表", (("产品/服务类别", 26, TEXT), ("本期收入", 16, MONEY),
                                    ("本期成本", 16, MONEY), ("毛利率", 12, TEXT),
                                    ("上期收入", 16, MONEY), ("上期毛利率", 12, TEXT),
                                    ("变动分析", 34, TEXT)),
         "按类别列示收入、成本与毛利率，与上期及同行业对比，异常波动须说明原因。", 14, None),
        ("收入截止性测试表", (("日期", 13, DATE), ("凭证号", 14, TEXT), ("客户", 24, TEXT),
                              ("金额", 16, MONEY), ("发货/验收日期", 14, DATE),
                              ("收入确认期间", 14, TEXT), ("是否跨期", 12, TEXT)),
         "选取资产负债表日前后各若干日的发货与开票记录，检查收入确认期间。", 14, None),
        ("函证信息汇总表", (("序号", 6, NUM), ("客户名称", 28, TEXT), ("本期交易额", 16, MONEY),
                            ("期末余额", 16, MONEY), ("回函金额", 16, MONEY),
                            ("是否相符", 10, TEXT), ("替代程序", 26, TEXT)),
         "对大客户实施函证，核对本期交易额与期末余额。", 12, None),
    ),
    "expense": (
        ("费用明细及分析表", (("费用项目", 24, TEXT), ("本期金额", 16, MONEY), ("上期金额", 16, MONEY),
                              ("变动额", 16, MONEY), ("变动率", 12, TEXT), ("分析说明", 34, TEXT)),
         "按性质与部门列示费用，执行分析程序识别异常波动。", 16, None),
        ("费用截止性测试表", (("日期", 13, DATE), ("凭证号", 14, TEXT), ("摘要", 26, TEXT),
                              ("金额", 16, MONEY), ("发票日期", 14, DATE), ("所属期间", 14, TEXT),
                              ("是否跨期", 12, TEXT)),
         "检查资产负债表日前后费用凭证与发票，确认费用归属期间。", 14, None),
        ("预提待摊检查表", (("项目", 24, TEXT), ("期初余额", 16, MONEY), ("本期计提", 16, MONEY),
                            ("本期摊销/支付", 16, MONEY), ("期末余额", 16, MONEY),
                            ("计算依据", 30, TEXT)),
         "复核预提与待摊的计提、摊销依据，检查期后实际支付与计提数是否一致。", 12, None),
    ),
}


_BY_NAME = {spec[0]: spec for spec in BASE}
for _category, _specs in SPECS.items():
    SPECS[_category] = tuple((title, CUTOFF_COLUMNS,
                             "按截止日前后七天、日期与账期不一致及异常摘要抽取可疑分录；"
                             "单据日期、业务归属与是否跨期由项目组核实。期后月份或下一年度记录未读取时须另行补取。", 12, need)
                            if title in CUTOFF_SHEETS else (title, columns, note, count, need)
                            for title, columns, note, count, need in _specs)
for _specs in SPECS.values():
    for _spec in _specs:
        _BY_NAME.setdefault(_spec[0], _spec)


def by_name(title):
    """按表名取列式定义，供模型建议增表时校验。"""
    return _BY_NAME.get(str(title or "").strip())


def names(category=None):
    """可选专项表名称清单：类别为空时返回全部。"""
    if not category:
        return sorted(_BY_NAME)
    return [spec[0] for spec in BASE] + [spec[0] for spec in SPECS.get(category, ())]


def movement_rows(accounts):
    """分析表数值在本地计算；资产负债用年初与期末，损益缺上期发生额时不编造。"""
    output = []
    for row in accounts:
        is_pl = row.is_profit_loss
        current = money(getattr(row, "period_amount", None) if is_pl else row.ending)
        previous = money(getattr(row, "previous_period_amount", None) if is_pl else row.opening)
        change = None if current is None or previous is None else current - previous
        rate = None if change is None or previous == 0 else change / abs(previous)
        output.append({"code": row.code, "name": row.name, "current": current, "previous": previous,
                       "change": change, "rate": rate,
                       "basis": ("本期发生额与上期发生额" if previous is not None else "本期发生额；上期发生额未提供")
                       if is_pl else "期末账面余额与年初账面余额"})
    return output


def facts(accounts, journal=None):
    """从一级科目判断账套里到底有什么，决定出哪些表、预填什么。"""
    codes = {str(getattr(row, "code", "") or "") for row in accounts}
    prefixes = {code[:4] for code in codes if len(code) >= 4}
    banks = []
    all_codes = [str(getattr(row, "code", "") or "") for row in accounts]
    for row in accounts:
        code = str(getattr(row, "code", "") or "")
        if not code.startswith(("1002", "1009", "1012")):
            continue
        # 有下级时一级科目本身不再单独函证，避免与明细账户重复
        if any(other.startswith(code) and other != code for other in all_codes):
            continue
        name = str(getattr(row, "name", "") or "")
        match = re.search(r"\d{6,}", name)
        number = match.group(0) if match else ""
        bank = (name[:match.start()] if match else name).strip(" -_—（）()")
        banks.append({"name": bank or name, "number": number, "account": name,
                      "balance": getattr(row, "ending", None)})
    return {
        "cash": "1001" in prefixes,
        "bank": bool({"1002", "1009", "1012"} & prefixes),
        "any_cash": bool({"1001", "1002", "1009", "1012"} & prefixes),
        "bank_accounts": banks,
        "journal": journal or [],
    }


def for_category(category, state=None):
    """按类别与账套实际情况返回要生成的专项表（含通用两张）。"""
    state = state or {}
    selected = list(BASE)
    for spec in SPECS.get(category, ()):
        need = spec[4]
        if need and not state.get(need):
            continue
        selected.append(spec)
    return tuple(selected)


def asset_component_rows(roots, rows, report_total=None):
    """原值、累计折旧/摊销和减值按相同明细名衔接；名称歧义时不分摊备抵金额。"""
    components = {}
    root_roles = set()
    totals = {role: [] for role in ("cost", "accumulated", "impairment")}
    for root in roots:
        name = str(root.name)
        # 备抵按名称识别；资产项目自身科目即原值科目，不按科目代码写死类别。
        role = ("accumulated" if any(word in name for word in ("累计折旧", "累计摊销")) else
                "impairment" if "减值准备" in name else "cost")
        root_roles.add(role)
        amount = money(root.ending)
        natural = "借" if role == "cost" else "贷"
        totals[role].append(None if amount is None else amount * (1 if root.direction == natural else -1))
        children = [line for line in rows if root.path and line.level == root.level + 1
                    and line.path.rpartition("/")[0] == root.path]
        for line in children or [root]:
            label = line.name if children else "整体"
            components.setdefault(label, {}).setdefault(role, []).append(line)
    output = []
    for label, roles in sorted(components.items()):
        values = {}
        for role in totals:
            candidates = roles.get(role, [])
            amount = money(candidates[0].ending) if len(candidates) == 1 else None
            natural = "借" if role == "cost" else "贷"
            values[role] = None if amount is None else amount * (1 if candidates[0].direction == natural else -1)
        cost, accumulated, impairment = (values[role] for role in totals)
        # 账套里没有某类备抵科目时该栏留空、净额按零计；科目存在但金额未知则净额留空。
        known = [Decimal("0") if role not in root_roles else values[role] for role in totals]
        net = None if any(value is None for value in known) else known[0] - known[1] - known[2]
        output.append((label, cost, accumulated, impairment, net, None, None))
    if output:
        amounts = [None if not values or any(value is None for value in values) else sum(values)
                   for values in totals.values()]
        output.append(("报表项目合计", *amounts, report_total, None, None))
    return output


def prefill(name, state, accounts=()):
    """能由科目余额表直接推出的行，先填好；其余留空给现场。"""
    if name == "银行函证信息汇总表":
        return [(index, item["name"], item["number"], item["balance"], None, None, None, None, None)
                for index, item in enumerate(state.get("bank_accounts") or [], 1)]
    if name == "银行账户清单":
        return [(index, item["name"], item["number"], None, None, item["balance"], None, None, None)
                for index, item in enumerate(state.get("bank_accounts") or [], 1)]
    if name == "资产明细及折旧摊销表":
        return asset_component_rows(state.get("primary_accounts", ()), state.get("rows", ()), state.get("report_total"))
    if name in {"账龄分析表", "函证信息汇总表", "往来账龄分析表", "存货盘点表"}:
        sources = state.get("auxiliary", ()) if name != "存货盘点表" else accounts
        ordered = sorted(sources, key=lambda row: -abs(getattr(row, "ending", 0) or 0))
        if name == "函证信息汇总表":
            ordered = ordered[:10]
        rows = []
        for index, row in enumerate(ordered, 1):
            balance = money(getattr(row, "ending", None))
            if balance is not None and getattr(row, "direction", "") != state.get("presentation_direction", "借"):
                balance = -balance
            if name == "账龄分析表":
                rows.append((getattr(row, "name", ""), balance,
                             None, None, None, None, None, None))
            elif name == "函证信息汇总表":
                rows.append((index, getattr(row, "name", ""), balance,
                             balance, None, None, None))
            elif name == "往来账龄分析表":
                rows.append((getattr(row, "name", ""), balance,
                             None, None, None, None, None))
            elif name == "存货盘点表":
                rows.append((getattr(row, "name", ""), None, None, None, None, None,
                             getattr(row, "ending", None), None))
        return rows
    return []
