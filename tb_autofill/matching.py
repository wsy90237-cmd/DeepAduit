"""基于文字和科目层级的匹配；所有金额均由本地规则计算。"""

import re
import unicodedata
from decimal import Decimal
from dataclasses import replace
from functools import lru_cache

from .models import LedgerRow, MatchResult, TargetField, money


@lru_cache(maxsize=20000)
def _normalize_cached(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).strip().lower()
    text = re.sub(r"^(?:(?:[一二三四五六七八九十百]+[、.．]|\([一二三四五六七八九十]+\)|\d+[、.．]|(?:加|减|其中)[:：])\s*)+", "", text)
    return re.sub(r"[\s\u3000]+", "", text)


def normalize(text) -> str:
    return _normalize_cached(str(text or ""))


_ALIAS_GROUPS = (
    ("库存现金", "现金", "现金(库存现金)"),
    ("库存商品", "产成品", "库存商品(产成品)"),
    ("材料采购", "物资采购"),
    ("委托加工物资", "委托加工材料"),
    ("预付账款", "预付款项"),
    ("预收账款", "预收款项"),
    ("应付职工薪酬", "应付薪酬"),
    ("应交税费", "应交税金"),
    ("实收资本", "股本", "实收资本(或股本)"),
    ("固定资产", "固定资产原价", "固定资产原值"),
    ("交易性金融资产", "以公允价值计量且其变动计入当期损益的金融资产"),
    ("销售费用", "营业费用"),
    ("其他业务成本", "其他业务支出"),
    ("研发费用", "研究与开发费"),
    ("所得税费用", "所得税"),
    ("手续费支出", "手续费"),
    ("累计摊销", "无形资产累计摊销"),
    ("资产处置收益", "资产处置损益"),
    ("税金及附加", "营业税金及附加"),
    ("工资", "职工工资", "工资薪金"),
    ("福利费", "职工福利", "职工福利费"),
    ("职工教育经费", "职工教育费", "教育经费"),
    ("基本养老保险费", "基本养老保险", "养老保险费", "养老保险"),
    ("失业保险费", "失业保险"),
    ("工伤保险费", "工伤保险"),
    ("生育保险费", "生育保险"),
    ("医疗保险费", "医疗保险"),
    ("住房公积金", "公积金"),
    ("城市维护建设税", "城建税", "城市建设维护税"),
)
_ALIASES = {normalize(name): normalize(group[0]) for group in _ALIAS_GROUPS for name in group}
_NOTE_PAREN = re.compile(r"^(?:注\d*|附注|备注|说明|详见|见附注)[:：、.\s\d]*")
_SIGN_PAREN = re.compile(r'\((?:[^()]*(?:损失|亏损|收益)[^()]*以[“”"\'＋+－−-]+号填列|以[“”"\'＋+－−-]+(?:号|值)填列)\)$')
_PATH_SEP = re.compile(r"[/\\>＞—–－-]+")
_PROPERTY_ROOTS = {"投资性房地产", "投资性房地产(公允计价)", "投资性房地产(公允价值计量)", "投资性房地产(成本计量)"}
_INVENTORY_ROOTS = {"原材料", "库存商品", "低值易耗品", "周转材料", "包装物", "自制半成品", "在产品",
                    "委托加工物资", "材料采购", "材料成本差异", "开发产品", "出租开发产品", "开发成本",
                    "生产成本", "制造费用", "发出商品", "合同履约成本", "工程结算", "工程施工", "开发间接费"}
_PROFIT_ROLLUPS = {
    "营业总收入": {"主营业务收入", "其他业务收入"},
    "营业总成本": {"主营业务成本", "其他业务成本", "税金及附加", "销售费用", "管理费用", "研发费用", "财务费用"},
    "主营业务利润": {"销售费用", "管理费用", "研发费用", "财务费用"},
    "营业利润": {"营业外收入", "营业外支出"},
    "利润总额": {"所得税费用"},
}


@lru_cache(maxsize=20000)
def _canonical_cached(name: str) -> str:
    if name in _ALIASES:
        return _ALIASES[name]
    match = re.search(r"\(([^()]*)\)$", name)
    if match and _NOTE_PAREN.match(match.group(1)):
        name = name[:match.start()]
    name = _SIGN_PAREN.sub("", name)
    return _ALIASES.get(name, name)


def _canonical(text):
    return _canonical_cached(normalize(text))


def _leaf(text):
    return _PATH_SEP.split(str(text or ""))[-1].strip()


class _Index:
    """一次构建、多次复用的科目索引。

    匹配曾对每个目标重复规范化并排序全部科目（800 目标 × 2000 科目约 60 秒），
    把派生键收敛到本索引后，逐目标只做字典查找。
    """

    __slots__ = ("rows", "all_set", "by_name", "by_name_path", "by_any", "by_norm_exact", "prefix_candidates")

    def __init__(self, rows):
        self.rows = rows
        self.all_set = set(rows)
        self.by_name = {}
        self.by_name_path = {}
        self.by_any = {}
        self.by_norm_exact = {}
        for row in rows:
            name_key, path_key = _canonical(row.name), _canonical(row.path)
            leaf_key = _canonical(_leaf(row.path))
            exact_keys = {normalize(row.name), normalize(row.path)}
            for key in (name_key,):
                if key:
                    self.by_name.setdefault(key, []).append(row)
            for key in {name_key, path_key}:
                if key:
                    self.by_name_path.setdefault(key, []).append(row)
            for key in {name_key, path_key, leaf_key}:
                if key:
                    self.by_any.setdefault(key, []).append(row)
            for key in exact_keys:
                if key:
                    self.by_norm_exact.setdefault(key, []).append(row)
        self.prefix_candidates = sorted(((normalize(row.name), row) for row in rows),
                                        key=lambda item: len(item[0]), reverse=True)

    def scoped(self, roots):
        if not roots:
            return self.rows, self.all_set
        kept = [row for row in self.rows if any(_in_root(row, root) for root in roots)]
        return kept, set(kept)


def _validate_rows(rows):
    contexts = {(str(row.subject_id), row.year, row.month, row.ledger_id) for row in rows}
    if len(contexts) > 1:
        raise ValueError("科目数据包含不同账套版本、主体、年度或期间，请分别处理")
    codes = [str(row.code).strip() for row in rows]
    if any(not code for code in codes):
        raise ValueError("科目编码不能为空")
    if len(codes) != len(set(codes)):
        raise ValueError("同一主体期间存在重复科目编码，需要先核对原始数据")


def _root_codes(parent, index):
    key = _canonical(parent)
    if not key:
        return []
    roots = [row.code for row in index.by_name_path.get(key, [])]
    if roots:
        return roots
    if key == "投资性房地产":
        return [row.code for name in _PROPERTY_ROOTS for row in index.by_name.get(name, [])
                if row.level == 1 and _canonical(row.path or row.name) in _PROPERTY_ROOTS]
    if key == "货币资金":
        return [row.code for rows in (index.by_name.get(_canonical(name), [])
                                      for name in ("库存现金", "银行存款", "其他货币资金"))
                for row in rows]
    return []


def _in_root(row, root):
    return str(row.code) == str(root) or str(row.code).startswith(str(root))


def _detail_label(target, index):
    label = _canonical(target.label)
    parts = _PATH_SEP.split(label)
    if len(parts) > 1 and _root_codes(parts[0], index):
        return parts[0], parts[-1]
    impairment = re.fullmatch(r"(应收账款|其他应收款|应收票据|应收利息|应收股利|预付账款)(?:坏账准备|减值准备)", label)
    if impairment and _root_codes("坏账准备", index):
        return "坏账准备", impairment[1]
    if target.parent and _root_codes(target.parent, index):
        return target.parent, target.label
    if _canonical(target.label) in index.by_name:
        return "", target.label
    # 只有真正存在的父科目名称才可把“应交税费增值税”拆为父项和叶子。
    for root, row in index.prefix_candidates:
        if len(root) < len(label) and len(root) >= 3 and label.startswith(root):
            return row.name, label[len(root):].lstrip("-—/\\:：")
    return "", target.label


def _component_matches(target, row):
    if row.is_profit_loss:
        return False
    name = _canonical(row.name)
    root = _canonical(str(row.path).split("/")[0])
    property_root = root in _PROPERTY_ROOTS
    if target.component == "cost":
        return name in {"投资性房地产-成本", "投资性房地产成本"} or (property_root and name == "成本")
    return name in {"投资性房地产-公允价值变动", "投资性房地产公允价值变动"} or (property_root and name == "公允价值变动")


def _account_total(target):
    if target.statement != "balance_sheet":
        return False
    label,parent = _canonical(target.label),_canonical(target.parent)
    return ((label in {"库存现金", "银行存款"} and parent in {"", "货币资金"})
            or (label in {"原材料", "库存商品", "发出商品", "委托加工物资", "材料采购", "周转材料"}
                and parent in {"", "存货"})
            or (label in {"待摊费用", "长期待摊费用"} and not parent))


def _asset_contra(target):
    if target.statement != "balance_sheet":
        return set()
    label,parent = _canonical(target.label),_canonical(target.parent)
    if label in {"累计折旧", "固定资产累计折旧"} and parent in {"", "固定资产"}:
        return {"累计折旧", "固定资产累计折旧"}
    if label == "累计摊销" and parent in {"", "无形资产"}:
        return {"累计摊销"}
    if label == "使用权资产累计折旧" and parent in {"", "使用权资产"}:
        return {label}
    impairment = {"固定资产减值准备":"固定资产", "无形资产减值准备":"无形资产", "在建工程减值准备":"在建工程",
                  "合同资产减值准备":"合同资产", "债权投资减值准备":"债权投资"}
    if label in impairment and parent in {"",impairment[label],impairment[label]+"净值",impairment[label]+"净额",impairment[label]+"成本"}:
        return {label}
    return set()


def _loan_component(target):
    if target.statement != "balance_sheet":
        return None
    parts = _PATH_SEP.split(normalize(target.label))
    if len(parts) == 2 and parts[0] in {"短期借款", "长期借款"} and parts[1] in {"借款本金", "应计利息"}:
        return parts[0],parts[1]
    return None


def _loan_matches(component,row):
    if row.is_profit_loss:
        return False
    category,kind = component
    names = {"本金", "借款本金"} if kind == "借款本金" else {"应计利息"}
    name = _canonical(row.name)
    root = _canonical(str(row.path).split("/")[0])
    return root == category and row.level > 1 and name in names


def _allowance_subject(target):
    if target.statement != "balance_sheet":
        return ""
    label = _canonical(target.label)
    for subject in ("其他应收款", "应收账款", "应收票据", "应收利息", "应收股利"):
        if label in {subject + "坏账准备", subject + "减值准备"}:
            return subject
    return ""


def _allowance_matches(subject, row):
    if row.is_profit_loss:
        return False
    name = _PATH_SEP.sub("-", _canonical(row.name))
    variants = {subject + "坏账准备", subject + "减值准备", subject + "-坏账准备",
                "坏账准备-" + subject, "坏账准备" + subject}
    if name in variants:
        return True
    root = _canonical(str(row.path).split("/")[0])
    return root == "坏账准备" and row.level > 1 and name in {subject, subject + "坏账损失", subject + "-坏账损失"}


def _tax_name(name):
    name = normalize(name)
    name = re.sub(r"^(?:应交|应缴)", "", name)
    return {"城建税": "城市维护建设税", "城市建设维护税": "城市维护建设税",
            "土增税": "土地增值税"}.get(name, name)


def _vat_summary(target):
    if target.statement != "balance_sheet":
        return False
    parts = _PATH_SEP.split(normalize(target.label))
    return ((len(parts) == 2 and _canonical(parts[0]) == "应交税费" and parts[1] == "增值税")
            or (_canonical(target.parent) == "应交税费" and normalize(target.label) == "增值税"))


def _tax_subject(target):
    if target.statement != "balance_sheet":
        return ""
    parts = _PATH_SEP.split(normalize(target.label))
    if len(parts) == 2 and (_canonical(parts[0]) == "应交税费"
            or (parts[0] == "其他应交" and _canonical(target.parent) == "应交税费")):
        name = _tax_name(parts[1])
    elif _canonical(target.parent) == "应交税费":
        name = _tax_name(target.label)
    else:
        return ""
    return name if name in {"城市维护建设税", "土地增值税", "个人所得税", "印花税", "教育费附加",
                           "地方教育费附加", "企业所得税", "房产税", "土地使用税", "车船使用税",
                           "营业税", "资源税", "消费税", "烟叶税", "防洪费"} else ""


def _tax_matches(subject, row):
    parts = str(row.path).split("/")
    direct_category = len(parts) == 2 or (len(parts) == 3 and normalize(parts[1]) in {"其他应交税费", "其他应缴税费"})
    return (not row.is_profit_loss and row.level == len(parts) and direct_category
            and _canonical(parts[0]) == "应交税费"
            and _tax_name(parts[-1]) == _tax_name(row.name) == subject)


def _welfare_detail(target):
    if target.statement != "balance_sheet":
        return False
    parts = _PATH_SEP.split(normalize(target.label))
    if len(parts) == 2 and _canonical(parts[0]) == "应付职工薪酬":
        return _canonical(re.sub(r"^应付", "", parts[1])) == "福利费"
    return (_canonical(target.parent) == "应付职工薪酬"
            and _canonical(re.sub(r"^应付", "", normalize(target.label))) == "福利费")


def _opening_profit(target):
    return (target.measure == "opening_balance" and target.period == "ending" and target.basis == "book"
            and target.statement == "profit_loss" and _canonical(target.label) == "年初未分配利润"
            and target.occurrence_scope == "year_to_date" and not target.presentation_direction
            and not target.balance_side and not target.component)


def _opening_profit_source(row):
    if row.is_profit_loss or _canonical(row.name) != "未分配利润":
        return False
    path = [_canonical(part) for part in str(row.path or row.name).split("/")]
    return ((row.level == 1 and path == ["未分配利润"])
            or (row.level == 2 and path == ["利润分配", "未分配利润"]))


def _candidate_rows(target, index):
    if target.measure == "opening_balance":
        if not _opening_profit(target):
            return [], "opening_balance"
        parents = [row for row in index.rows if not row.is_profit_loss and row.level == 1
                   and _canonical(row.name) == _canonical(row.path or row.name) == "利润分配"]
        candidates = [row for row in index.rows if _opening_profit_source(row)
                      and (row.level == 1 or (len(parents) == 1 and row.code != parents[0].code
                           and _in_root(row, parents[0].code)))]
        return candidates, "ambiguous_parent" if len(parents) > 1 else "opening_balance"
    if target.component:
        candidates = [row for row in index.rows if _component_matches(target, row)]
        if target.component == "cost" and any(row.level == 1 and not row.is_profit_loss
                and _canonical(row.name) in {"投资性房地产", "投资性房地产(成本计量)"} for row in index.rows):
            return candidates, "ambiguous_parent"
        return candidates, "template_component"
    contra = _asset_contra(target)
    if contra:
        candidates = [row for row in index.rows if not row.is_profit_loss and row.level == 1
                      and _canonical(row.name) in contra and _canonical(row.path or row.name) in contra]
        return candidates, "asset_contra"
    loan = _loan_component(target)
    if loan:
        if _canonical(target.parent) not in {"",loan[0]}:
            return [],"ambiguous_parent"
        candidates = [row for row in index.rows if _loan_matches(loan,row)]
        parents = [row for row in index.rows if row.level == 1 and _canonical(row.name) == loan[0]]
        if len(parents) > 1:
            return candidates,"ambiguous_parent"
        roots = [row for row in candidates if not any(other != row and other.level < row.level
                 and _in_root(row,other.code) for other in candidates)]
        if len(roots) == 1 and all(_in_root(row,roots[0].code) for row in candidates):
            return roots,"loan_component"
        return candidates,"ambiguous_parent" if candidates else "loan_component"
    if _vat_summary(target):
        candidates = [row for row in index.rows if row.level == 2 and not row.is_profit_loss
                      and _canonical(str(row.path).split("/")[0]) == "应交税费"
                      and "增值税" in normalize(row.name) and "土地增值税" not in normalize(row.name)]
        return candidates, "ambiguous_parent"
    subject = _allowance_subject(target)
    if subject:
        candidates = [row for row in index.rows if _allowance_matches(subject, row)]
        roots = [row for row in candidates if not any(other != row and other.level < row.level
                 and _in_root(row, other.code) for other in candidates)]
        if len(roots) == 1 and all(_in_root(row, roots[0].code) for row in candidates):
            return roots, "allowance_component"
        return candidates, "ambiguous_parent" if candidates else "allowance_component"
    tax = _tax_subject(target)
    if tax:
        if _canonical(target.parent) not in {"", "应交税费"}:
            return [], "ambiguous_parent"
        parents = [row for row in index.rows if row.level == 1 and _canonical(row.name) == "应交税费"]
        candidates = [row for row in index.rows if _tax_matches(tax, row)]
        if tax == "防洪费":
            related = [row for row in index.rows if any(_tax_matches(name, row)
                       for name in ("水利建设基金", "水利基金", "地方水利基金"))]
            if related:
                return candidates + related, "ambiguous_parent"
        if len(parents) != 1 or any(not _in_root(row, parents[0].code) for row in candidates):
            return candidates, "ambiguous_parent" if candidates else "tax_detail"
        roots = [row for row in candidates if not any(other != row and other.level < row.level
                 and _in_root(row, other.code) for other in candidates)]
        if len(roots) == 1 and all(_in_root(row, roots[0].code) for row in candidates):
            return roots, "tax_detail"
        return candidates, "ambiguous_parent" if candidates else "tax_detail"
    if _welfare_detail(target):
        if _canonical(target.parent) not in {"", "应付职工薪酬"}:
            return [], "ambiguous_parent"
        parents = [row for row in index.rows if row.level == 1 and _canonical(row.name) == "应付职工薪酬"]
        candidates = [row for row in index.rows if not row.is_profit_loss and row.level == 2
                      and len(str(row.path).split("/")) == 2
                      and _canonical(str(row.path).split("/")[0]) == "应付职工薪酬"
                      and _canonical(re.sub(r"^应付", "", normalize(str(row.path).split("/")[-1])))
                      == _canonical(re.sub(r"^应付", "", normalize(row.name))) == "福利费"]
        if len(parents) != 1 or any(not _in_root(row, parents[0].code) for row in candidates):
            return candidates, "ambiguous_parent" if candidates else "payroll_detail"
        return candidates, "payroll_detail"
    if _account_total(target):
        key = _canonical(target.label)
        candidates = index.by_name.get(key, [])
        roots = [row for row in candidates if row.level == 1 and _canonical(row.path or row.name) == key]
        if len(roots) == 1 and all(row == roots[0] or (row.level > roots[0].level and _in_root(row, roots[0].code)) for row in candidates):
            return roots, "account_total"
        if candidates:
            return candidates, "ambiguous_parent"
    parent, leaf = _detail_label(target, index)
    roots = _root_codes(parent, index)
    inventory_rollup = False
    if target.statement and target.parent and not _root_codes(target.parent, index):
        parent_key,label_key = _canonical(target.parent),_canonical(target.label)
        if parent_key == "存货" and label_key in _INVENTORY_ROOTS:
            roots = [row.code for row in index.rows if row.level == 1 and _canonical(row.name) in _INVENTORY_ROOTS]
            inventory_rollup = True
        elif target.statement == "profit_loss" and parent_key in _PROFIT_ROLLUPS:
            roots = [code for name in _PROFIT_ROLLUPS[parent_key] if label_key == name or label_key.startswith(name+"-")
                     for code in _root_codes(name,index)]
        else:
            parts = _PATH_SEP.split(normalize(target.parent))
            prefix = _canonical(parts[0])
            roots = _root_codes(prefix,index) if len(parts)>1 and label_key.startswith(prefix) else []
        if not roots:
            return [],"ambiguous_parent"
    scoped, scoped_set = index.scoped(roots)
    if len(roots) > 1 and _canonical(parent) != "货币资金" and not inventory_rollup:
        key = _canonical(leaf)
        candidates = [row for row in index.by_any.get(key, []) if row in scoped_set]
        return candidates, "ambiguous_parent"
    if re.fullmatch(r"\d{4,}", str(target.index_code)):
        coded = [row for row in scoped if str(row.code) == str(target.index_code)]
        if coded:
            return coded, "code"
    exact = [row for row in index.by_norm_exact.get(normalize(target.label), []) if row in scoped_set]
    if exact:
        return exact, "exact"
    key = _canonical(leaf)
    candidates = [row for row in index.by_any.get(key, []) if row in scoped_set] if key else []
    return candidates, "parent_alias" if roots else "alias"


def _direction(row):
    value = normalize(row.direction)
    if value in {"借", "借方", "dr", "debit", "1"}:
        return 1
    if value in {"贷", "贷方", "cr", "credit", "-1"}:
        return -1
    raise ValueError(f"科目 {row.code} 的借贷方向不明确")


def _row_amount(target, row):
    measure = target.measure
    if target.statement not in {"", "balance_sheet", "profit_loss"}:
        raise ValueError("报表区段未识别")
    if target.presentation_direction not in {"", "借", "贷"}:
        raise ValueError("模板列示方向未识别")
    if target.component not in {"", "cost", "fair_value_change"}:
        raise ValueError("模板金额分项未识别")
    if measure == "opening_balance":
        if not _opening_profit(target) or not _opening_profit_source(row):
            return None
        amount = money(row.opening)
        return amount * -_direction(row) if amount is not None else None
    if target.component and not _component_matches(target, row):
        return None
    contra = _asset_contra(target)
    if contra and (row.is_profit_loss or row.level != 1 or _canonical(row.name) not in contra
                   or _canonical(row.path or row.name) not in contra):
        return None
    loan = _loan_component(target)
    if loan and (_canonical(target.parent) not in {"",loan[0]} or not _loan_matches(loan,row)):
        return None
    if _vat_summary(target):
        return None
    subject = _allowance_subject(target)
    if subject and not _allowance_matches(subject, row):
        return None
    tax = _tax_subject(target)
    if tax and not _tax_matches(tax, row):
        return None
    if _account_total(target) and (row.level != 1 or _canonical(row.path or row.name) != _canonical(target.label)):
        return None
    if target.statement == "balance_sheet" and row.is_profit_loss:
        return None
    if target.statement == "profit_loss" and measure == "balance":
        measure = "profit_loss"
    if target.period not in {"opening", "ending"}:
        raise ValueError("字段期间未识别")
    if measure not in {"balance", "debit", "credit", "movement", "profit_loss"}:
        raise ValueError("字段金额口径未识别")
    if measure == "balance" and row.is_profit_loss and target.period == "ending" and not target.balance_side:
        measure = "profit_loss"
    if measure != "balance" and target.occurrence_scope != "year_to_date":
        return None
    if measure == "balance":
        amount = money(row.opening if target.period == "opening" else row.ending)
        if amount is not None and target.balance_side:
            if target.balance_side not in {"借", "贷"}:
                raise ValueError("余额列的借贷侧未识别")
            signed = amount * _direction(row)
            amount = max(signed if target.balance_side == "借" else -signed, Decimal(0))
        elif amount is not None and target.presentation_direction:
            amount *= _direction(row) * (1 if target.presentation_direction == "借" else -1)
        elif amount is not None and _canonical(target.label) == "未确认融资费用":
            amount *= _direction(row)
        return amount
    if target.period == "opening":
        return None
    if target.balance_side:
        raise ValueError("发生额字段不能按余额借贷列拆分")
    if measure == "debit":
        return money(row.debit)
    if measure == "credit":
        return money(row.credit)
    if measure == "profit_loss":
        direction = _direction(row)
        if not row.is_profit_loss:
            return None
        amount = money(row.debit if direction == 1 else row.credit)
        presentation = normalize(target.label)
        if _canonical(target.parent) in {"信用减值损失", "资产减值损失"}:
            presentation += normalize(target.parent)
        income_positive = ("利息收入" in presentation and re.search(r'以[“”"\'＋+]+(?:号|值)填列', presentation))
        loss_negative = re.search(r'(?:损失|亏损)以[“”"\'－−-]+号填列', presentation)
        gain_negative = re.search(r'(?:收益)以[“”"\'－−-]+号填列', presentation)
        if income_positive and direction == 1:
            debit, credit = money(row.debit), money(row.credit)
            if debit is not None and debit > 0 and credit is not None and credit > 0:
                return None
            if debit == 0 and credit is not None and credit > 0:
                return credit if money(row.opening) == 0 and money(row.ending) == -credit else None
        if amount is not None and (income_positive or loss_negative):
            amount *= -direction
        elif amount is not None and gain_negative:
            amount *= direction
        return amount
    debit, credit = money(row.debit), money(row.credit)
    if debit is None or credit is None:
        return None
    return (debit - credit) * _direction(row)


def _overlap(left, right):
    a, b = str(left.code), str(right.code)
    return a == b or a.startswith(b) or b.startswith(a)


def resolve_match(target: TargetField, sources: list[LedgerRow], method: str = "manual", *, rows=None, _index=None) -> MatchResult:
    """人工/AI 均只选来源；金额和所有写入前置条件统一在此核验。"""
    result = MatchResult(target=target, sources=list(sources), method=method)
    if target.is_formula or (isinstance(target.value, str) and target.value.lstrip().startswith("=")):
        result.status, result.reason = "protected", "目标单元格为公式，保留不改"
        return result
    if not sources:
        result.reason = "没有选择可用的来源科目"
        return result
    try:
        _validate_rows(sources)
    except ValueError as exc:
        result.status, result.reason = "invalid", str(exc)
        return result
    if target.basis not in {"book", "audited"} or any(row.basis != target.basis for row in sources):
        result.status, result.reason = "basis_mismatch", "账面数、审定数或未知口径不能混用；需要对应口径的来源"
        return result
    for index, row in enumerate(sources):
        if any(_overlap(row, other) for other in sources[index + 1:]):
            result.status, result.reason = "duplicate", "来源同时包含同一科目或其上下级，不能重复计入"
            return result
    if target.measure == "opening_balance" and not _opening_profit(target):
        result.status, result.reason = "missing", "年初余额口径仅适用于期末利润分配表的明确年初未分配利润字段"
        return result
    try:
        amounts = [_row_amount(target, row) for row in sources]
    except ValueError as exc:
        result.status, result.reason = "invalid", str(exc)
        return result
    if any(value is None for value in amounts):
        result.status = "missing"
        if _vat_summary(target):
            result.reason = "增值税汇总需确认应交、未交、预交等明细的取数范围，不能用单个同名余额代替"
        elif _allowance_subject(target) and any(not _allowance_matches(_allowance_subject(target), row) for row in sources):
            result.reason = "坏账准备字段必须使用对应应收类别的明确备抵分项，不能使用混合总额或应收本金"
        elif _tax_subject(target) and any(not _tax_matches(_tax_subject(target), row) for row in sources):
            result.reason = "税费明细必须来自应交税费或应交税金下的对应税种"
        elif _asset_contra(target):
            result.reason = "资产备抵字段需要对应的独立折旧、摊销或减值准备来源，不能使用资产原值或费用"
        elif _loan_component(target):
            result.reason = "借款本金或应计利息字段需要对应明确分项，不能用借款总额或利息调整代替"
        elif _account_total(target) and any(row.level != 1 or _canonical(row.path or row.name) != _canonical(target.label) for row in sources):
            result.reason = "总额字段需要对应一级总科目，不能用单个同名明细替代"
        elif target.component and any(not _component_matches(target, row) for row in sources):
            result.reason = "来源不属于模板指定的投资性房地产成本或公允价值变动分项"
        elif target.statement == "balance_sheet" and any(row.is_profit_loss for row in sources):
            result.reason = "资产负债表字段不能使用损益科目的余额或发生额，需要资产负债来源"
        elif target.measure == "profit_loss" and any(not row.is_profit_loss for row in sources):
            result.reason = "损益表字段不能直接使用资产负债科目的发生额，需要对应的损益来源"
        elif target.measure == "profit_loss" and "利息收入" in normalize(target.label):
            result.reason = "利息收入发生额、期初期末或入账与结转方式依据不足，未推定金额和正负号"
        elif target.occurrence_scope == "month":
            result.reason = "缺少单月发生额依据，不能使用年初累计发生额"
        else:
            result.reason = "缺少上期发生额依据，不能使用本年发生额" if target.period == "opening" and target.measure != "balance" else "来源金额缺失；真实零值和缺失值分开处理"
        return result
    if method in {"ai", "code"} and not target.statement and rows is None and _index is None:
        result.status, result.reason = "missing", "缺少完整账套上下文，无法核对来源唯一性"
        return result
    generic = method in {"ai", "code"} and not target.statement
    if target.statement in {"balance_sheet", "profit_loss"} or method in {"ai", "code"}:
        if _index is None:
            if (rows is None and target.parent and not (target.component or _asset_contra(target)
                    or _allowance_subject(target) or _tax_subject(target) or _loan_component(target) or _account_total(target))
                    and any(_canonical(target.parent) not in {_canonical(part) for part in str(source.path).split("/")}
                            for source in sources)):
                result.status, result.reason = "missing", "缺少可核对字段父路径的完整账套上下文"
                return result
            context = sources if rows is None else rows
            try:
                _validate_rows(context)
            except ValueError as exc:
                result.status, result.reason = "invalid", str(exc)
                return result
            _index = _Index([row for row in context if not row.is_profit_loss]
                            if target.statement == "balance_sheet" else context)
        if generic and target.parent and not _root_codes(target.parent, _index):
            result.status, result.reason = "missing", "无法确认字段父科目，不能自动选择来源"
            return result
        # 标准TB以文字和路径为准，索引编码不能绕过语义校验。
        candidates, candidate_method = _candidate_rows(replace(target, index_code=""), _index)
        if target.statement == "balance_sheet" and _canonical(target.label) == "投资性房地产" and not target.component:
            modes = _PROPERTY_ROOTS - {"投资性房地产"}
            candidates += [row for row in _index.rows if row.level == 1 and _canonical(row.name) in modes
                           and _canonical(row.path or row.name) in modes and row not in candidates]
        if any(source not in candidates for source in sources):
            result.status, result.reason = "missing", "来源科目名称、类别或父路径与字段不符，或缺少完整账套上下文"
            return result
        if (method != "manual" or target.measure == "opening_balance") and (candidate_method == "ambiguous_parent" or len(candidates) != 1):
            result.status, result.reason = "ambiguous", "来源存在歧义，自动匹配或AI不能确定或选中"
            return result
    try:
        factor = money(target.unit_factor)
    except ValueError:
        factor = None
    if factor not in {Decimal(1), Decimal(1000), Decimal(10000), Decimal(100000000)}:
        result.status, result.reason = "invalid", "模板金额单位未确认，单位除数只能是元、千元、万元或亿元"
        return result
    result.amount = sum(amounts, Decimal(0)) / factor
    result.status = "matched"
    result.selected = target.value is None or (isinstance(target.value, str) and not target.value.strip())
    result.reason = "按已确认来源本地计算；科目余额按自然方向保留符号"
    if target.measure == "opening_balance":
        result.reason = "按明确未分配利润科目的年初余额取数；按贷方方向列示，保留亏损或冲回符号"
    elif target.measure == "balance" and target.presentation_direction and not target.balance_side:
        result.reason = f"按已确认来源本地计算；按模板{target.presentation_direction}方方向列示，冲回保留反向符号"
    elif target.measure == "balance" and _canonical(target.label) == "未确认融资费用" and not target.balance_side:
        result.reason = "按已确认来源本地计算；未确认融资费用按借方方向列示，冲回保留反向符号"
    if target.component:
        result.reason += "；按模板的投资性房地产" + ("成本分项" if target.component == "cost" else "公允价值变动分项")
    if method == "account_total":
        result.reason += "；字段为科目总额，取对应一级总科目，不重复叠加同名明细"
    elif method == "asset_contra":
        result.reason += "；资产备抵取对应的独立科目，保留模板列示方向"
    elif method == "loan_component":
        result.reason += "；借款本金和应计利息按明确分项来源匹配"
    elif method == "allowance_component":
        result.reason += "；按应收类别及坏账准备科目路径匹配，不将混合准备总额直接分配"
    elif method == "tax_detail":
        result.reason += "；按应交税费科目路径和税种名称匹配"
    if target.measure == "profit_loss" or (target.measure == "balance" and any(row.is_profit_loss for row in sources) and not target.balance_side):
        result.reason += "；损益按科目方向一侧发生额，正负号遵循模板明确的填列说明"
    if not result.selected:
        result.reason += "；目标已有值，默认保留，覆盖需明确选择"
    return result


def _claim_measure(result):
    if result.target.measure == "balance" and not result.target.balance_side and all(row.is_profit_loss for row in result.sources):
        return "profit_loss"
    return result.target.measure


def validate_claims(results: list[MatchResult]) -> list[MatchResult]:
    """同表同期间的重复认领全部提示，不按行序暗选第一行。"""
    for result in results:
        if result.status == "duplicate" and result.amount is not None:
            result.status = "matched"
            result.reason = "原重复认领已重新检查；请确认选择"
            result.selected = False
    proposals = [result for result in results if result.status == "matched" and result.amount is not None]
    conflicts = set()
    for i, left in enumerate(proposals):
        for right in proposals[i + 1:]:
            if (left.target.sheet, left.target.period, left.target.basis, _claim_measure(left)) != (right.target.sheet, right.target.period, right.target.basis, _claim_measure(right)):
                continue
            complementary = (
                left.target.measure == right.target.measure == "balance"
                and {left.target.balance_side, right.target.balance_side} == {"借", "贷"}
                and re.sub(r"\D", "", left.target.cell) == re.sub(r"\D", "", right.target.cell)
                and left.source_ids == right.source_ids
            )
            if not complementary and any(_overlap(a, b) for a in left.sources for b in right.sources):
                conflicts.update((id(left), id(right)))
    for result in proposals:
        if id(result) in conflicts:
            result.status = "duplicate"
            result.selected = False
            result.reason = "同表同期间有多个目标认领同一科目或上下级余额，需要确认，未自动选第一行"
    return results


def apply_ai_suggestions(results: list[MatchResult], suggestions, rows: list[LedgerRow]):
    """AI 只提供来源选择；金额、口径与写入前置条件仍由 resolve_match 计算。"""
    by_id = {result.target.id: result for result in results}
    index = {str(row.code): row for row in rows}
    applied, notes = 0, []
    for item in suggestions:
        target_id = str(item.get("target_id", "")) if isinstance(item, dict) else ""
        result = by_id.get(target_id)
        if result is None:
            notes.append(f"未知目标 {target_id or '(空)'}：已忽略")
            continue
        if result.status == "protected":
            notes.append(f"{target_id}：公式单元格，AI 建议已忽略")
            continue
        picked = [index.get(str(code)) for code in item.get("source_codes", [])]
        if not picked or any(row is None for row in picked):
            notes.append(f"{target_id}：AI 指定的来源不在本次候选内，未采纳")
            continue
        check = resolve_match(result.target, picked, method="ai", rows=rows)
        if check.status != "matched":
            notes.append(f"{target_id}：{check.reason}，未采纳")
            continue
        result.sources, result.amount, result.method = check.sources, check.amount, check.method
        result.status = check.status
        result.reason = "AI 建议来源 + 本地计算；" + check.reason
        result.selected = check.selected
        result.overwrite = False
        applied += 1
    validate_claims(results)
    return applied, notes


def match_fields(targets: list[TargetField], rows: list[LedgerRow]) -> list[MatchResult]:
    _validate_rows(rows)
    index = _Index(rows)
    balance_index = _Index([row for row in rows if not row.is_profit_loss]) if any(target.statement == "balance_sheet" for target in targets) else index
    results = []
    for target in targets:
        if target.is_formula or (isinstance(target.value, str) and target.value.lstrip().startswith("=")):
            results.append(resolve_match(target, []))
            continue
        candidates, method = _candidate_rows(target, balance_index if target.statement == "balance_sheet" else index)
        if target.statement == "balance_sheet" and not candidates:
            rejected, _ = _candidate_rows(target, index)
            if rejected and all(row.is_profit_loss for row in rejected):
                results.append(MatchResult(target=target, sources=rejected, method=method, status="missing",
                    reason="同名候选均为损益科目，不能作为资产负债表来源"))
                continue
        if method == "ambiguous_parent" or len(candidates) != 1:
            result = MatchResult(target=target, sources=candidates, method=method)
            result.status = "ambiguous" if candidates or method == "ambiguous_parent" else "unmatched"
            result.reason = "父科目存在多个候选，需要确认完整路径" if method == "ambiguous_parent" else ("存在多个同名候选，需要按科目路径确认" if candidates else "没有可靠匹配，不猜测或自动归入其他")
            if _vat_summary(target):
                result.reason = "增值税汇总需确认应交、未交、预交等明细的取数范围，不能用单个同名余额代替"
            elif _tax_subject(target) == "防洪费" and method == "ambiguous_parent":
                result.reason = "防洪费来源或水利基金等范围存在歧义，需复核模板的填列范围，不能自动当作同义税费"
            elif target.component == "cost" and method == "ambiguous_parent":
                result.reason = "投资性房地产成本与整体或其他计量根并存，需确认完整范围，不能只取一类成本分项"
            elif _loan_component(target) and _canonical(target.parent) not in {"",_loan_component(target)[0]}:
                result.reason = "借款字段名称与所属类别或到期范围不一致，需要确认"
        else:
            result = resolve_match(target, candidates, method, _index=balance_index if target.statement == "balance_sheet" else index)
        results.append(result)
    return validate_claims(results)
