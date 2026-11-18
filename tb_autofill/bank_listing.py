"""Validate a full bank-account listing against the current general ledger."""

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal, localcontext
from io import BytesIO

from openpyxl import load_workbook

from .bank_evidence import BankBalance, _account_number, _amount, _currency, _cutoff_date


SHEET = "银行账户余额清单"
HEADERS = ("主体", "截止日", "科目编码", "币种", "金额单位", "户名", "完整账号", "期末余额")


@dataclass(frozen=True)
class ListedAccount:
    row: int
    balance: BankBalance


def read_listing(content, *, entity, cutoff, code, currency, ledger_balance):
    """Read every listing line and require the signed total to match exactly."""
    try:
        currency = _currency(currency)
    except ValueError as exc:
        raise ValueError("请明确填写账套记账本位币，如 CNY。") from exc
    try:
        book = load_workbook(BytesIO(content), read_only=True, data_only=False)
    except Exception as exc:
        raise ValueError("无法读取 XLSX 清单，请检查文件是否完整。") from exc
    accounts = []
    numbers = set()
    try:
        if book.sheetnames != [SHEET]:
            raise ValueError(f"清单须且仅须含一张名为「{SHEET}」的工作表。")
        rows = book[SHEET].iter_rows(values_only=False)
        header = next(rows, ())
        if tuple(cell.value for cell in header) != HEADERS:
            raise ValueError("第 1 行表头须为 " + "、".join(HEADERS))
        for i, row in enumerate(rows, 2):
            values = tuple(cell.value for cell in row)
            if all(v is None for v in values):
                continue
            if len(row) != len(HEADERS) or any(cell.data_type in {"f", "e"} for cell in row):
                raise ValueError(f"第 {i} 行须逐列填写原值，公式及错误值均无效。")
            company, when, account_code, denomination, unit, holder, number, balance = values
            if isinstance(when, datetime) and when.time() == time():
                when = when.date()
            try:
                day = _cutoff_date(when)
                account_currency = _currency(denomination)
                account_no = _account_number(number)
                amount = _amount(str(balance) if type(balance) is float else balance)
            except ValueError as exc:
                raise ValueError(f"第 {i} 行截止日、币种、文本账号或余额格式无效。") from exc
            if (company, day, account_code, account_currency, unit) != (entity, cutoff, code, currency, "元"):
                raise ValueError(f"第 {i} 行主体、截止日、文本科目编码、币种或金额单位与账面口径不一致。")
            if not isinstance(holder, str) or not holder.strip():
                raise ValueError(f"第 {i} 行缺少户名。")
            if account_no in numbers:
                raise ValueError(f"第 {i} 行完整账号重复，请核查清单。")
            numbers.add(account_no)
            accounts.append(ListedAccount(i, BankBalance(holder.strip(), account_no, account_currency, day, amount)))
    finally:
        book.close()
    if not accounts:
        raise ValueError("清单缺少逐户余额。")
    amounts = [account.balance.balance for account in accounts]
    with localcontext() as context:
        context.prec = max(context.prec, max(a.adjusted() for a in amounts) -
                           min(a.as_tuple().exponent for a in amounts) + len(str(len(amounts))) + 2)
        total = sum(amounts, Decimal(0))
    if total != ledger_balance:
        raise ValueError(f"清单逐户合计 {total:f} 与总账期末余额 {ledger_balance:f} 不符。")
    return accounts
