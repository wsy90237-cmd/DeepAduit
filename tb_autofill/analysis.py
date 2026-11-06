"""账套变动分析：先用本地规则算出事实，再让 AI 只解释事实。

设计要点：
- 变动额与变动率全部由 Decimal 在本地计算，AI 不参与任何数字。
- 全局变动只排列末级科目，项目汇总按所属根科目计算，避免父子重复计入。
- 问答提供相关项目的科目全貌，全局摘要保留 Top N，并明确发送范围及可核对金额集合，
  回答里出现清单外的金额会被标记为可疑。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from .models import LedgerRow, money

ZERO = Decimal(0)


@dataclass(frozen=True)
class Movement:
    code: str
    name: str
    path: str
    level: int
    direction: str
    opening: Decimal | None
    ending: Decimal | None
    change: Decimal | None
    rate: Decimal | None
    debit: Decimal | None
    credit: Decimal | None
    profit_loss: bool

    def as_dict(self) -> dict:
        item = {
            "code": self.code,
            "name": self.name,
            "path": self.path,
            "direction": self.direction,
            "opening": _text(self.opening),
            "ending": _text(self.ending),
            "change": _text(self.change),
            "rate": _rate(self.rate),
            "debit": _text(self.debit),
            "credit": _text(self.credit),
            "profit_loss": self.profit_loss,
        }
        if self.profit_loss:
            for key in ("opening", "ending", "debit", "credit"):
                item.pop(key)
            item.update(current_period=_text(self.ending), previous_period=_text(self.opening))
        return item


def _text(value):
    return None if value is None else str(value)


def _rate(value):
    return None if value is None else f"{value * 100:.2f}%"


def account_fact(row, *, is_pl=None):
    """面向审计问答的科目事实；损益只提供独立发生额，不混入结转余额。"""
    is_pl = row.is_profit_loss if is_pl is None else is_pl
    item = {"code": row.code, "name": row.name, "path": row.path, "level": row.level,
            "direction": row.direction, "profit_loss": is_pl}
    if is_pl:
        item.update(current_period=_text(row.period_amount), previous_period=_text(row.previous_period_amount),
                    monthly_amounts=[_text(value) for value in row.monthly_amounts],
                    period_source=row.period_source, previous_period_source=row.previous_period_source)
    else:
        item.update({key: _text(getattr(row, key)) for key in ("opening", "ending", "debit", "credit")})
    return item


def question_scope(rows, question, history=()):
    """按本轮问题选报表项目，短追问沿用最近一轮用户问题；金额不用于排除零变动科目。"""
    from . import audit_index
    papers = audit_index.resolve_papers()
    roots = [row for row in rows if row.level == 1]
    def matches(text):
        indices = {paper.index_no for paper in papers if paper.name and paper.name in text}
        mapped = audit_index.resolve("", text)[0]
        if mapped:
            indices.add(mapped.index_no)
        direct = [row for row in rows if (len(row.name) >= 2 and row.name not in {"其他", "合计", "明细"} and row.name in text)
                  or re.search(r"(?<![\d.])" + re.escape(row.code) + r"(?![\d.])", text)]
        selected_roots = [root for root in roots if any(row == root or
                          (root.path and row.path.startswith(root.path + "/") and row.code.startswith(root.code))
                          for row in direct)]
        for root in selected_roots:
            owner = audit_index.resolve(root.code, root.name)[0]
            if owner:
                indices.add(owner.index_no)
        return indices, selected_roots
    indices, selected_roots = matches(question)
    if not indices and not selected_roots:
        for turn in reversed(list(history)[-8:]):
            if isinstance(turn, dict) and turn.get("role") == "user":
                indices, selected_roots = matches(str(turn.get("content") or ""))
                if indices or selected_roots:
                    break
    if not indices and not selected_roots:
        return list(rows), [], [], False
    selected = {row.code: row for row in selected_roots}
    for root in roots:
        owner = audit_index.resolve(root.code, root.name)[0]
        if owner and owner.index_no in indices:
            selected[root.code] = root
    warnings = []
    focused_papers = [paper for paper in papers if paper.index_no in indices]
    for paper in focused_papers:
        try:
            selected.update((row.code, row) for row in audit_index.reserve_components(rows, paper))
        except ValueError as exc:
            warnings.append(str(exc))
    scoped = [row for row in rows if any(row == root or (root.path and
              row.path.startswith(root.path + "/") and row.code.startswith(root.code)) for root in selected.values())]
    return scoped, focused_papers, warnings, True


def _parents(codes) -> set:
    """有序编码中，子科目紧跟在父科目之后，据此 O(n) 判定父科目。"""
    parents = set()
    ordered = sorted(codes)
    for index, code in enumerate(ordered[:-1]):
        if ordered[index + 1].startswith(code) and ordered[index + 1] != code:
            parents.add(code)
    return parents


def movements(rows: list[LedgerRow]) -> list[Movement]:
    output = []
    for row in rows:
        opening = row.previous_period_amount if row.is_profit_loss else row.opening
        ending = row.period_amount if row.is_profit_loss else row.ending
        change = None if opening is None or ending is None else ending - opening
        rate = None
        if change is not None and opening not in (None, ZERO):
            rate = change / abs(opening)
        output.append(Movement(row.code, row.name, row.path, row.level, row.direction,
                               opening, ending, change, rate,
                               row.debit, row.credit, row.is_profit_loss))
    return output


def build_facts(rows: list[LedgerRow], top: int = 40, *, question="", history=(), account_limit=300) -> dict:
    """把账套压缩成可核对的事实清单；不含任何推断或结论。

    accounts 保留提问相关的科目（含零变动），movements 仅为末级变动排序摘要。
    范围超过上限时明确标记，all_codes 用于区分已知编码与凭空编造编码。
    """
    if not rows:
        raise ValueError("没有科目余额数据")
    contexts = {(row.subject_id, row.year, row.month, row.ledger_id) for row in rows}
    if len(contexts) > 1:
        raise ValueError("科目数据包含不同账套版本、主体或期间，不能混在一起做变动分析")
    scoped, focused_papers, warnings, focused = question_scope(rows, question, history) if question else (list(rows), [], [], False)
    items = movements(scoped)
    parents = _parents([item.code for item in items])
    leaves = [item for item in items if item.code not in parents]
    ranked = sorted((item for item in leaves if item.change not in (None, ZERO)),
                    key=lambda item: abs(item.change), reverse=True)[:top]
    first = rows[0]
    ranked_codes = {item.code for item in ranked}
    candidates = scoped if focused else [row for row in scoped if row.level == 1 or row.code in ranked_codes]
    candidates = sorted(candidates, key=lambda row: (row.level != 1,
                        not (row.name in question or row.code in question), row.code))
    sent = candidates[:account_limit]
    def known_total(key):
        values = [getattr(item, key) for item in leaves]
        return _text(sum(values, ZERO)) if values and all(value is not None for value in values) else None
    result = {
        "subject": {"id": first.subject_id, "year": first.year, "month": first.month},
        "counts": {"accounts": len(items), "leaves": len(leaves)},
        "accounts": [account_fact(row) for row in sent],
        "account_scope": {"focused": focused, "reports": [paper.label for paper in focused_papers],
                          "available": len(scoped), "sent": len(sent), "truncated": len(sent) < len(scoped),
                          "note": "变动摘要仅为排序结果；未出现在摘要中不表示科目不存在、余额为零或没有发生额。"},
        "source_warnings": warnings,
        "movements": [item.as_dict() for item in ranked],
        "no_rate": [item.code for item in ranked if item.rate is None][:8],
        "all_codes": sorted(row.code for row in rows),
        "totals": {
            "debit": known_total("debit"),
            "credit": known_total("credit"),
        },
    }
    result["report_totals"] = []
    from . import audit_index, dxn_paper
    for paper in focused_papers:
        try:
            accounts = dxn_paper.group_accounts(scoped, paper, lambda code, name: audit_index.resolve(code, name)[0])
            if not accounts or warnings:
                continue
            totals = dxn_paper.totals(accounts, dxn_paper.presentation_direction(paper, accounts))
            keys = ("period", "previous_period") if paper.index_no.startswith(("4-", "5-")) else ("opening", "ending", "debit", "credit")
            result["report_totals"].append({"report": paper.label, "components": [row.code for row in accounts],
                                          **{key: _text(totals[key]) for key in keys}})
        except ValueError as exc:
            result["source_warnings"].append(str(exc))
    if scoped and all(row.is_profit_loss for row in scoped):
        result["totals"] = {}
    return result


def summarize(facts: dict, limit: int = 6) -> str:
    """无 AI 时也能给出的确定性变动摘要。"""
    lines = []
    for item in facts["movements"][:limit]:
        change = money(item["change"]) if item["change"] else ZERO
        rate = f"（{item['rate']}）" if item["rate"] else "（期初为 0）"
        sign = "+" if change >= 0 else ""
        lines.append(f"{item['code']} {item['name']} {sign}{change:,}{rate}")
    if not lines:
        return "所选范围没有可计算的非零变动；缺少比较期或未列入摘要的科目仍需核查。"
    return "；".join(lines)


def fact_amounts(facts: dict, *, include_counts=True) -> set:
    """事实中出现过的全部金额与计数，用于核对 AI 引用的数字。

    底稿事实比 TB 事实多出科目本级、下级与序时账摘要，这些金额同样来自本机数据，
    必须算作“已知”，否则模型照抄也会被误报为编造。
    """
    values = set()

    def add(value):
        if value is None or value == "":
            return
        try:
            amount = money(value)
        except (ValueError, TypeError):
            return
        if amount is not None:
            values.add(amount.copy_abs())

    for item in facts.get("movements") or []:
        for key in ("opening", "ending", "change", "debit", "credit", "current_period", "previous_period"):
            add(item.get(key))
    for key in ("debit", "credit"):
        add((facts.get("totals") or {}).get(key))
    if include_counts:
        for value in (facts.get("counts") or {}).values():
            add(value)
    subject = facts.get("subject") or {}
    for key in ("opening", "debit", "credit", "ending", "current_period", "previous_period"):
        add(subject.get(key))
    for group in ("children", "accounts", "auxiliary", "auxiliary_checks"):
        for item in facts.get(group) or []:
            for key in ("opening", "debit", "credit", "ending", "current_period", "previous_period",
                        "opening_difference", "ending_difference"):
                add(item.get(key))
            for value in item.get("monthly_amounts") or []:
                add(value)
    for item in facts.get("report_totals") or []:
        for key in ("opening", "ending", "debit", "credit", "period", "previous_period"):
            add(item.get(key))
    for item in facts.get("monthly_rows") or []:
        for key in ("current", "previous"):
            for value in item.get(key) or []:
                add(value)
    for item in facts.get("analysis_rows") or []:
        for key in ("current", "previous", "change"):
            add(item.get(key))
    for item in facts.get("cutoff_samples") or []:
        for key in ("debit", "credit"):
            add(item.get(key))
    add((facts.get("book_check") or {}).get("absolute_difference_sum"))
    if include_counts:
        add((facts.get("ledger_scope") or {}).get("rows"))
    journal = facts.get("journal") or {}
    for key in (("debit", "credit", "count") if include_counts else ("debit", "credit")):
        add(journal.get(key))
    for group in ("monthly", "top_contra", "samples"):
        for item in journal.get(group) or []:
            for key in (("debit", "credit", "amount", "count") if include_counts else ("debit", "credit", "amount")):
                add(item.get(key))
    tie = journal.get("tie") or {}
    for key in ("ledger_debit", "ledger_credit"):
        add(tie.get(key))
    return values


def fact_codes(facts: dict) -> set:
    codes = {str(code) for code in facts.get("all_codes", [])}
    codes |= {str(item["code"]) for item in facts.get("movements") or [] if item.get("code")}
    codes |= {str(item["code"]) for item in facts.get("children") or [] if item.get("code")}
    for group in ("analysis_rows", "cutoff_samples"):
        codes |= {str(item["code"]) for item in facts.get(group) or [] if item.get("code")}
    if (facts.get("subject") or {}).get("code"):
        codes.add(str(facts["subject"]["code"]))
    journal = facts.get("journal") or {}
    codes |= {str(item["code"]) for item in journal.get("samples") or [] if item.get("code")}
    return codes


def fact_tokens(facts: dict) -> set:
    """科目名称/路径里内嵌的数字（如“农业银行43128”的账号尾号），不算编造金额。"""
    tokens = set()

    def harvest(text):
        for piece in re.split(r"[^0-9]+", str(text or "")):
            if len(piece) >= 3:
                tokens.add(piece)

    for item in [*(facts.get("movements") or []), *(facts.get("accounts") or [])]:
        for key in ("name", "path"):
            harvest(item.get(key))
    for item in facts.get("similarly_named") or []:
        harvest(item.get("name"))
    journal = facts.get("journal") or {}
    for item in facts.get("analysis_rows") or []:
        harvest(item.get("name"))
    for item in [*(journal.get("samples") or []), *(facts.get("cutoff_samples") or [])]:
        for key in ("summary", "contra", "voucher"):
            harvest(item.get(key))
    return tokens


_CODE_ANY = re.compile(r"(?<![\d,.])\d{3,}(?:\.\d+)+(?![\d,.])")
_CODE_SUFFIX = ("元", "万", "%", "个", "笔", "项", "条", "日", "月", "年")


def unknown_codes(text: str, codes) -> list:
    """回答里像科目编码、但账套中并不存在的串；带金额单位的（1000.50 元）不算。"""
    content = text or ""
    known = {str(code) for code in codes}
    found = []
    for match in _CODE_ANY.finditer(content):
        token = match.group(0)
        if token in known:
            continue
        follow = content[match.end():match.end() + 4].strip()[:1]
        if follow in _CODE_SUFFIX:
            continue
        if token not in found:
            found.append(token)
    return found


_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
# 多级科目编码（如 5001.01.01.01.01）：至少两个点，且不嵌在千分位数字中
_CODE = re.compile(r"(?<![\d,.])\d{2,}(?:\.\d+){2,}(?![\d,.])")
_YEAR = re.compile(r"^(19|20)\d{2}$")
_MONEY_UNIT = re.compile(r"^\s*(亿元|万元|千元|元|万|亿)")
_AMOUNT_PREFIX = re.compile(r"(?:金额|余额|发生额|变动额|差额|差异|合计|借方|贷方|增加|减少|支付|收款)"
                            r"(?:为|了|是|约为|约|[:：=]|\s)*$")
_SCALE = {"元": Decimal(1), "千元": Decimal(1000), "万元": Decimal(10000),
          "万": Decimal(10000), "亿元": Decimal(100000000), "亿": Decimal(100000000)}


def fact_rates(facts: dict) -> set:
    """只接受本地计算并发送给模型的变动率。"""
    values = set()
    for item in [*(facts.get("analysis_rows") or []), *(facts.get("movements") or [])]:
        text = str(item.get("rate") or "").strip()
        if text.endswith("%"):
            try:
                values.add(abs(Decimal(text[:-1].replace(",", ""))))
            except ArithmeticError:
                pass
    return values


def suspicious_numbers(text: str, amounts, codes=(), tolerance: Decimal = Decimal("0.5"),
                       extra=(), *, strict=False, rates=()) -> list:
    """回答里出现但事实中不存在的金额。

    先跳过科目编码区间与名称内嵌数字，再做金额比对；百分比、年份与计数不计。
    """
    content = text or ""
    known_codes = {str(code) for code in codes} | {str(token) for token in extra}
    spans = [] if strict else [match.span() for match in _CODE.finditer(content)]
    if strict:
        normalized_codes = {re.sub(r"\D", "", str(code)) for code in codes}
        for match in _CODE_ANY.finditer(content):
            tail = content[match.end():]
            prefix = content[max(0, match.start() - 8):match.start()]
            code_context = prefix.endswith(("（", "(")) or bool(re.search(r"科目(?:编码|编号)?[：:\s]*$", prefix))
            if (code_context and re.sub(r"\D", "", match.group()) in normalized_codes
                    and not _MONEY_UNIT.match(tail) and not tail.lstrip().startswith("%")):
                spans.append(match.span())
    flagged = []
    for match in _NUMBER.finditer(content):
        if any(start <= match.start() < end for start, end in spans):
            continue
        raw = match.group(0).rstrip(",.")
        token = raw.replace(",", "")
        suffix = content[match.end():]
        tail = suffix[:1]
        unit = _MONEY_UNIT.match(suffix) if strict else None
        prefix = content[max(0, match.start() - 16):match.start()]
        amount_context = (strict and not suffix.lstrip().startswith(("年", "月", "日", "笔", "个", "项", "期"))
                          and (_AMOUNT_PREFIX.search(prefix) or prefix.endswith(("¥", "￥"))))
        if strict and suffix.lstrip().startswith("%"):
            if not any(abs(abs(Decimal(token)) - rate) <= Decimal("0.005") for rate in rates):
                flagged.append(raw + "%")
            continue
        if unit or amount_context:
            value = abs(Decimal(token)) * (_SCALE[unit.group(1)] if unit else 1)
            if not any(abs(value - known) <= tolerance for known in amounts):
                flagged.append(raw + (unit.group(1) if unit else ""))
            continue
        if tail == "%" or _YEAR.match(token) or token in known_codes or (strict and token.lstrip("-") in known_codes):
            continue
        if raw.count(",") == 0 and token.lstrip("-").replace(".", "", 1).isdigit() and abs(Decimal(raw)) < 100:
            continue
        try:
            value = money(raw).copy_abs()
        except ValueError:
            continue
        if any(abs(value - known) <= tolerance for known in amounts):
            continue
        if raw not in flagged:
            flagged.append(raw)
    return flagged
