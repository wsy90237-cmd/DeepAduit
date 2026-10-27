"""基于文字和科目层级的匹配；所有金额均由本地规则计算。"""

import re
import unicodedata
from decimal import Decimal

from .models import LedgerRow, MatchResult, TargetField, money


def normalize(text) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    text = re.sub(r"^(?:[一二三四五六七八九十百]+[、.．]|\([一二三四五六七八九十]+\)|\d+[、.．]|(?:加|减)[:：])\s*", "", text)
    return re.sub(r"[\s\u3000]+", "", text)


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
_PATH_SEP = re.compile(r"[/\\>＞—–－-]+")


def _canonical(text):
    name = normalize(text)
    if name in _ALIASES:
        return _ALIASES[name]
    match = re.search(r"\(([^()]*)\)$", name)
    if match and _NOTE_PAREN.match(match.group(1)):
        name = name[:match.start()]
    return _ALIASES.get(name, name)


def _leaf(text):
    return _PATH_SEP.split(str(text or ""))[-1].strip()


def _validate_rows(rows):
    contexts = {(str(row.subject_id), row.year, row.month) for row in rows}
    if len(contexts) > 1:
        raise ValueError("科目数据包含不同主体、年度或期间，请分别处理")
    codes = [str(row.code).strip() for row in rows]
    if any(not code for code in codes):
        raise ValueError("科目编码不能为空")
    if len(codes) != len(set(codes)):
        raise ValueError("同一主体期间存在重复科目编码，需要先核对原始数据")


def _root_codes(parent, rows):
    key = _canonical(parent)
    if not key:
        return []
    roots = [row.code for row in rows if key in {_canonical(row.name), _canonical(row.path)}]
    if roots:
        return roots
    if key == "货币资金":
        return [row.code for row in rows if _canonical(row.name) in {"库存现金", "银行存款", "其他货币资金"}]
    return []


def _in_root(row, root):
    return str(row.code) == str(root) or str(row.code).startswith(str(root))


def _detail_label(target, rows):
    label = normalize(target.label)
    parts = _PATH_SEP.split(label)
    if len(parts) > 1 and _root_codes(parts[0], rows):
        return parts[0], parts[-1]
    if target.parent and _root_codes(target.parent, rows):
        return target.parent, target.label
    if any(_canonical(target.label) == _canonical(row.name) for row in rows):
        return "", target.label
    # 只有真正存在的父科目名称才可把“应交税费增值税”拆为父项和叶子。
    for row in sorted(rows, key=lambda item: len(normalize(item.name)), reverse=True):
        root = normalize(row.name)
        if len(root) >= 3 and label.startswith(root) and label != root:
            return row.name, label[len(root):].lstrip("-—/\\:：")
    return "", target.label


def _candidate_rows(target, rows):
    parent, leaf = _detail_label(target, rows)
    roots = _root_codes(parent, rows)
    scoped = [row for row in rows if any(_in_root(row, root) for root in roots)] if roots else list(rows)
    if len(roots) > 1 and _canonical(parent) != "货币资金":
        key = _canonical(leaf)
        candidates = [row for row in scoped if key in {_canonical(row.name), _canonical(row.path), _canonical(_leaf(row.path))}]
        return candidates, "ambiguous_parent"
    if re.fullmatch(r"\d{4,}", str(target.index_code)):
        coded = [row for row in scoped if str(row.code) == str(target.index_code)]
        if coded:
            return coded, "code"
    exact = [row for row in scoped if normalize(target.label) in {normalize(row.name), normalize(row.path)}]
    if exact:
        return exact, "exact"
    key = _canonical(leaf)
    candidates = [row for row in scoped if key and key in {_canonical(row.name), _canonical(row.path), _canonical(_leaf(row.path))}]
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
    if target.period not in {"opening", "ending"}:
        raise ValueError("字段期间未识别")
    if measure not in {"balance", "debit", "credit", "movement", "profit_loss"}:
        raise ValueError("字段金额口径未识别")
    if measure == "balance" and row.is_profit_loss and target.period == "ending" and not target.balance_side:
        measure = "profit_loss"
    if measure == "balance":
        amount = money(row.opening if target.period == "opening" else row.ending)
        if amount is not None and target.balance_side:
            if target.balance_side not in {"借", "贷"}:
                raise ValueError("余额列的借贷侧未识别")
            signed = amount * _direction(row)
            amount = max(signed if target.balance_side == "借" else -signed, Decimal(0))
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
        return money(row.debit if _direction(row) == 1 else row.credit)
    debit, credit = money(row.debit), money(row.credit)
    if debit is None or credit is None:
        return None
    return (debit - credit) * _direction(row)


def _overlap(left, right):
    a, b = str(left.code), str(right.code)
    return a == b or a.startswith(b) or b.startswith(a)


def resolve_match(target: TargetField, sources: list[LedgerRow], method: str = "manual") -> MatchResult:
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
    try:
        amounts = [_row_amount(target, row) for row in sources]
    except ValueError as exc:
        result.status, result.reason = "invalid", str(exc)
        return result
    if any(value is None for value in amounts):
        result.status = "missing"
        result.reason = "缺少上期发生额依据，不能使用本年发生额" if target.period == "opening" and target.measure != "balance" else "来源金额缺失；真实零值和缺失值分开处理"
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
    if target.measure == "profit_loss" or (target.measure == "balance" and any(row.is_profit_loss for row in sources) and not target.balance_side):
        result.reason += "；损益按科目方向一侧发生额"
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
        check = resolve_match(result.target, picked, method="ai")
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
    results = []
    for target in targets:
        if target.is_formula or (isinstance(target.value, str) and target.value.lstrip().startswith("=")):
            results.append(resolve_match(target, []))
            continue
        candidates, method = _candidate_rows(target, rows)
        if method == "ambiguous_parent" or len(candidates) != 1:
            result = MatchResult(target=target, sources=candidates, method=method)
            result.status = "ambiguous" if candidates or method == "ambiguous_parent" else "unmatched"
            result.reason = "父科目存在多个候选，需要确认完整路径" if method == "ambiguous_parent" else ("存在多个同名候选，需要按科目路径确认" if candidates else "没有可靠匹配，不猜测或自动归入其他")
        else:
            result = resolve_match(target, candidates, method)
        results.append(result)
    return validate_claims(results)
