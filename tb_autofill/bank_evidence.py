"""Compare one reviewed book balance with the balance shown by bank evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
import re
import unicodedata


@dataclass(frozen=True)
class BankBalance:
    account_name: str = ""
    account_number: str = ""
    currency: str = ""
    cutoff_date: date | str | None = None
    balance: Decimal | str | int | None = None


@dataclass(frozen=True)
class BankReconciliation:
    status: str
    difference: Decimal | None
    issues: tuple[str, ...]
    recommendation: str


_CURRENCIES = {
    "人民币": "CNY", "RMB": "CNY", "美元": "USD", "港币": "HKD", "港元": "HKD",
    "欧元": "EUR", "日元": "JPY", "英镑": "GBP",
}
_AMOUNT = re.compile(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?")


def complete_amount_at(text: str, start: int, end: int) -> bool:
    """Whether a numeric selection leaves no adjacent digits, separators or sign outside it."""
    if not 0 <= start < end <= len(text) or not text[start:end].strip():
        return False
    left = unicodedata.normalize("NFKC", text[:start]).replace("−", "-")
    right = unicodedata.normalize("NFKC", text[end:]).replace("−", "-")
    return not (
        re.search(r"(?:\d|[+\-(]|\d[ \t]*[.,])[ \t]*$", left)
        or re.match(r"[ \t]*(?:\d|[.,][ \t]*\d|[)+\-])", right)
    )


def _account_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError
    return value.strip()


def _account_number(value):
    if not isinstance(value, str):
        raise ValueError
    number = value.replace(" ", "")
    if not re.fullmatch(r"[0-9]+", number):
        raise ValueError
    return number


def _currency(value):
    if not isinstance(value, str):
        raise ValueError
    code = value.strip().upper()
    code = _CURRENCIES.get(code, code)
    if not re.fullmatch(r"[A-Z]{3}", code) or code == "XXX":
        raise ValueError
    return code


def _cutoff_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value.strip()):
        raise ValueError
    return date.fromisoformat(value.strip())


def _amount(value):
    if isinstance(value, Decimal):
        amount = value
    elif type(value) is int:
        amount = Decimal(value)
    elif isinstance(value, str) and _AMOUNT.fullmatch(value.strip()):
        amount = Decimal(value.strip().replace(",", ""))
    else:
        raise ValueError
    if not amount.is_finite():
        raise ValueError
    return amount


def reconcile_bank_balance(
    book: BankBalance, evidence: BankBalance, *, evidence_kind: str
) -> BankReconciliation:
    """Return evidence minus book only after both sides have a valid and matching scope."""
    if evidence_kind not in {"confirmation", "statement"}:
        raise ValueError("证据类型应为银行回函或银行对账单。")

    fields = (
        ("account_name", "户名", _account_name),
        ("account_number", "完整账号", _account_number),
        ("currency", "币种", _currency),
        ("cutoff_date", "截止日", _cutoff_date),
        ("balance", "余额", _amount),
    )
    parsed = []
    issues = []
    for side, balance in (("账面", book), ("银行证据", evidence)):
        values = []
        for name, label, parse in fields:
            value = getattr(balance, name)
            if value is None or isinstance(value, str) and not value.strip():
                issues.append(f"{side}缺少{label}")
                continue
            try:
                values.append(parse(value))
            except ValueError:
                issues.append(f"{side}{label}格式不符合要求")
        parsed.append(values)
    if issues:
        return BankReconciliation("pending", None, tuple(issues), "请核对原件并补齐或更正两侧字段后再比较。")

    book_values, evidence_values = parsed
    mismatched = tuple(
        f"{label}不一致"
        for (_, label, _), left, right in zip(fields[:-1], book_values[:-1], evidence_values[:-1], strict=True)
        if left != right
    )
    if mismatched:
        return BankReconciliation("mismatch", None, mismatched, "请查验户名和完整账号，重新取得同币种、同截止日的证据。")

    book_amount = book_values[-1]
    evidence_amount = evidence_values[-1]
    with localcontext() as context:
        highest_digit = max(book_amount.adjusted(), evidence_amount.adjusted())
        lowest_digit = min(book_amount.as_tuple().exponent, evidence_amount.as_tuple().exponent)
        context.prec = max(context.prec, highest_digit - lowest_digit + 2)
        difference = evidence_amount - book_amount
    if difference:
        return BankReconciliation(
            "difference", difference, ("银行证据余额与账面余额存在差额",),
            "取得余额调节表，核对未达账项和期后银行流水，并评估剩余差额是否需要审计调整。",
        )
    recommendation = (
        "继续核对回函来源及收函控制，复核回函所列受限资金等事项。"
        if evidence_kind == "confirmation"
        else "复核对账单来源和完整性，并结合函证或替代程序评价证据。"
    )
    return BankReconciliation("matched", difference, (), recommendation)
