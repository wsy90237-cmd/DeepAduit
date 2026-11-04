"""账套变动分析：先用本地规则算出事实，再让 AI 只解释事实。

设计要点：
- 变动额与变动率全部由 Decimal 在本地计算，AI 不参与任何数字。
- 只分析叶子科目，避免父子重复计入；父科目仅用于展示层级。
- 提供给 AI 的事实是受限清单（Top N），并附带可用于核对的金额集合，
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
        return {
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


def _text(value):
    return None if value is None else str(value)


def _rate(value):
    return None if value is None else f"{value * 100:.2f}%"


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
        change = None if row.opening is None or row.ending is None else row.ending - row.opening
        rate = None
        if change is not None and row.opening not in (None, ZERO):
            rate = change / abs(row.opening)
        output.append(Movement(row.code, row.name, row.path, row.level, row.direction,
                               row.opening, row.ending, change, rate,
                               row.debit, row.credit, row.is_profit_loss))
    return output


def build_facts(rows: list[LedgerRow], top: int = 40) -> dict:
    """把账套压缩成可核对的事实清单；不含任何推断或结论。

    movements 只放变动额最大的若干项（控制上下文长度），但 all_codes 给出全部科目编码，
    用于区分「引用了账套里真实存在的科目」和「凭空编造编码」。
    """
    if not rows:
        raise ValueError("没有科目余额数据")
    contexts = {(row.subject_id, row.year, row.month, row.ledger_id) for row in rows}
    if len(contexts) > 1:
        raise ValueError("科目数据包含不同账套版本、主体或期间，不能混在一起做变动分析")
    items = movements(rows)
    parents = _parents([item.code for item in items])
    leaves = [item for item in items if item.code not in parents]
    ranked = sorted((item for item in leaves if item.change not in (None, ZERO)),
                    key=lambda item: abs(item.change), reverse=True)[:top]
    first = rows[0]
    return {
        "subject": {"id": first.subject_id, "year": first.year, "month": first.month},
        "counts": {"accounts": len(items), "leaves": len(leaves)},
        "movements": [item.as_dict() for item in ranked],
        "no_rate": [item.code for item in ranked if item.rate is None][:8],
        "all_codes": sorted(item.code for item in items),
        "totals": {
            "debit": _text(sum((item.debit for item in leaves if item.debit is not None), ZERO)),
            "credit": _text(sum((item.credit for item in leaves if item.credit is not None), ZERO)),
        },
    }


def summarize(facts: dict, limit: int = 6) -> str:
    """无 AI 时也能给出的确定性变动摘要。"""
    lines = []
    for item in facts["movements"][:limit]:
        change = money(item["change"]) if item["change"] else ZERO
        rate = f"（{item['rate']}）" if item["rate"] else "（期初为 0）"
        sign = "+" if change >= 0 else ""
        lines.append(f"{item['code']} {item['name']} {sign}{change:,}{rate}")
    if not lines:
        return "无变动"
    return "；".join(lines)


def fact_amounts(facts: dict) -> set:
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
        for key in ("opening", "ending", "change", "debit", "credit"):
            add(item.get(key))
    for key in ("debit", "credit"):
        add((facts.get("totals") or {}).get(key))
    for value in (facts.get("counts") or {}).values():
        add(value)
    subject = facts.get("subject") or {}
    for key in ("opening", "debit", "credit", "ending"):
        add(subject.get(key))
    for item in facts.get("children") or []:
        for key in ("opening", "debit", "credit", "ending"):
            add(item.get(key))
    for item in facts.get("analysis_rows") or []:
        for key in ("current", "previous", "change"):
            add(item.get(key))
    for item in facts.get("cutoff_samples") or []:
        for key in ("debit", "credit"):
            add(item.get(key))
    add((facts.get("ledger_scope") or {}).get("rows"))
    journal = facts.get("journal") or {}
    for key in ("debit", "credit", "count"):
        add(journal.get(key))
    for group in ("monthly", "top_contra", "samples"):
        for item in journal.get(group) or []:
            for key in ("debit", "credit", "amount", "count"):
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

    for item in facts.get("movements") or []:
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


def suspicious_numbers(text: str, amounts, codes=(), tolerance: Decimal = Decimal("0.5"),
                       extra=()) -> list:
    """回答里出现但事实中不存在的金额。

    先跳过科目编码区间与名称内嵌数字，再做金额比对；百分比、年份与计数不计。
    """
    content = text or ""
    known_codes = {str(code) for code in codes} | {str(token) for token in extra}
    spans = [match.span() for match in _CODE.finditer(content)]
    flagged = []
    for match in _NUMBER.finditer(content):
        if any(start <= match.start() < end for start, end in spans):
            continue
        raw = match.group(0).rstrip(",.")
        token = raw.replace(",", "")
        tail = content[match.end():match.end() + 1]
        if tail == "%" or _YEAR.match(token) or token in known_codes:
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
