"""Local review of a bank balance against one independent ledger account."""

import calendar
from collections import Counter
from dataclasses import dataclass
from datetime import date
import json
import re
import tkinter as tk
from tkinter import filedialog, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from . import dialogs as messagebox
from .bank_evidence import BankBalance, complete_amount_at, reconcile_bank_balance
from .bank_listing import HEADERS, SHEET, read_listing


@dataclass(frozen=True)
class BankComparison:
    text: str
    listing_id: str = ""


def _current_rows(rows, subject):
    scope = (subject.id, subject.ledger_id, subject.year, subject.month, "book")
    return tuple(row for row in rows if (
        row.subject_id, row.ledger_id, row.year, row.month, row.basis) == scope)


def _bank_rows(rows, subject):
    rows = _current_rows(rows, subject)
    codes = Counter(row.code for row in rows)
    return tuple(row for row in rows if
                 row.level > 1 and row.ending is not None
                 and (row.code.startswith("1002") or row.path.startswith("银行存款/"))
                 and not any(code != row.code and code.startswith(row.code) for code in codes)
                 and codes[row.code] == 1)


def _bank_summary(rows, subject):
    rows = _current_rows(rows, subject)
    matches = [row for row in rows if row.level == 1 and row.name == "银行存款"]
    if len(matches) != 1:
        return None
    row = matches[0]
    if row.ending is None or any(other.code.startswith(row.code) and other != row for other in rows):
        return None
    return row


def _found_in_excerpt(value, excerpt, adjacent):
    if not value:
        return True
    if adjacent == "0-9.,+-":
        return _amount_in_excerpt(value, excerpt)
    # PDF text layers may add whitespace inside otherwise identical printed fields.
    value = re.sub(r"\s+", "", value)
    pattern = r"[ \t]*".join(re.escape(char) for char in value)
    if not adjacent:
        return bool(re.search(pattern, excerpt))
    for match in re.finditer(rf"(?<![{adjacent}]){pattern}(?![{adjacent}])", excerpt):
        if adjacent == "0-9*＊":
            before = excerpt[:match.start()].rstrip(" \t")
            after = excerpt[match.end():].lstrip(" \t")
            if re.search(r"[0-9*＊]$", before) or re.match(r"[0-9*＊]", after):
                continue
        return True
    return False


def _evidence_date(value):
    match = re.fullmatch(r"([0-9]{4})(?:年|/|[.-])([0-9]{1,2})(?:月|/|[.-])([0-9]{1,2})日?", value)
    if match:
        try:
            return date(*(int(part) for part in match.groups()))
        except ValueError:
            pass
    return value


def _number_text(value):
    normalized = value.translate(str.maketrans("０１２３４５６７８９．，＋－−（）", "0123456789.,+--()"))
    return re.sub(r"[ \t]*([.,])[ \t]*", r"\1", normalized)


def _normalize_amount_text(value):
    normalized = _number_text(value)
    financial_negative = re.fullmatch(r"\([ \t]*([0-9][0-9.,]*)[ \t]*\)", normalized)
    if financial_negative:
        return "-" + financial_negative[1]
    return re.sub(r"^([+-])[ \t]+(?=[0-9])", r"\1", normalized)


def _amount_in_excerpt(value, excerpt):
    normalized = _number_text(excerpt)
    return any(complete_amount_at(normalized, *match.span()) for match in
               re.finditer(re.escape(_number_text(value)), normalized))


def compare_dialog(parent, app, value, excerpt, *, store=None) -> BankComparison | None:
    """Prompt for reviewed fields and bind any imported account listing."""
    subject = app.loaded_subject
    if subject is None:
        raise ValueError("请先读取已确认主体和期间的账套。")
    rows = _bank_rows(app.rows or (), subject)
    cutoff = date(subject.year, subject.month, calendar.monthrange(subject.year, subject.month)[1])
    listing = None
    try:
        if not rows:
            summary = _bank_summary(app.rows or (), subject)
            if summary is None or store is None:
                raise ValueError("账套没有独立银行存款明细，也没有可核平至一个独立总账科目的银行存款汇总余额。")
            listing = _import_listing(parent, store, subject, summary, cutoff)
            if listing is None:
                return None
            rows = (summary,)
        return _BankDialog(parent, rows, cutoff, value, excerpt, listing=listing).result
    finally:
        if parent.winfo_exists():
            parent.grab_set()


def _import_listing(parent, store, subject, summary, cutoff):
    scope = json.dumps((subject.ledger_id, cutoff.isoformat(), summary.code), ensure_ascii=False)
    existing = store.current_bank_listing(scope)
    if existing:
        use_existing = messagebox.askyesnocancel(
            "选择逐户余额清单",
            f"是否使用项目中当前快照《{existing[2]}》？选择「否」可导入新版本。",
            parent=parent,
        )
        if use_existing is None:
            return None
        if use_existing:
            currency = simpledialog.askstring("确认账套记账本位币", "请输入已从账套核对的记账本位币（如 CNY）。", parent=parent)
            if currency is None:
                return None
            accounts = read_listing(existing[3], entity=subject.name, cutoff=cutoff, code=summary.code,
                                    currency=currency, ledger_balance=summary.ending)
            return (*existing[:3], accounts)
    confirmed = messagebox.askokcancel(
        "导入逐户余额清单",
        f"账套只有汇总科目 {summary.code} {summary.name}，期末余额 {summary.ending:f}。"
        f"\n\n请选择 XLSX，工作簿仅含一张「{SHEET}」表，第 1 行依序填列："
        f"\n{'、'.join(HEADERS)}。"
        f"\n\n每户一行，主体填 {subject.name}，截止日 {cutoff}，科目编码 {summary.code}。"
        "科目和账号均用文本格式，金额单位填「元」，币种统一用总账记账本位币，余额填带符号原值。"
        "请按完整账户范围导入，含零余额户。\n\n确认全表与总账口径相同并继续选择文件？",
        parent=parent,
    )
    if not confirmed:
        return None
    path = filedialog.askopenfilename(parent=parent, title="选择完整逐户余额清单", filetypes=[("Excel", "*.xlsx")])
    if not path:
        return None
    currency = simpledialog.askstring("确认账套记账本位币", "请输入已从账套核对的记账本位币（如 CNY）。", parent=parent)
    if currency is None:
        return None
    result = store.import_bank_listing(path, scope, lambda payload: read_listing(
        payload, entity=subject.name, cutoff=cutoff, code=summary.code,
        currency=currency, ledger_balance=summary.ending,
    ))
    messagebox.showinfo("已保留清单快照", "逐户合计已核平至账面。现在可选择一户，也可关闭核对窗口，保留此次清单版本。", parent=parent)
    return result


class _BankDialog(simpledialog.Dialog):
    def __init__(self, parent, rows, cutoff, value, excerpt, *, listing=None):
        self.rows, self.cutoff, self.value, self.excerpt = rows, cutoff, value, excerpt
        self.listing = listing
        super().__init__(parent, title="逐户核对银行余额")

    def body(self, frame):
        hint = (
            "请选择清单中已与同一银行账号核对的逐户明细。"
            if self.listing else "请选择已和银行账号独立核对的末级银行存款科目。"
        )
        ttk.Label(frame, text=hint).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=4)
        labels = (
            [f"第 {entry.row} 行  {entry.balance.account_name}  {entry.balance.account_number}  ·  {entry.balance.balance:f}"
             for entry in self.listing[3]] if self.listing else
            [f"{row.code}  {row.path or row.name}  ·  期末账面 {row.ending:f}" for row in self.rows]
        )
        self.account = ttk.Combobox(frame, state="readonly", width=78, values=labels)
        self.account.grid(row=1, column=0, columnspan=3, sticky="ew")
        ttk.Label(frame, text="核对依据").grid(row=2, column=0, sticky="w")
        self.kind = ttk.Combobox(frame, state="readonly", values=("银行回函", "银行对账单"))
        self.kind.grid(row=2, column=1, columnspan=2, sticky="w", pady=4)
        ttk.Label(frame, text="已核对的账面字段").grid(row=3, column=1)
        ttk.Label(frame, text="银行证据字段（按摘录原文）").grid(row=3, column=2)
        self.entries = []
        for i, label in enumerate(("户名", "完整账号", "币种", "余额截止日", "期末余额"), start=4):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", padx=4)
            book = ttk.Entry(frame, width=35)
            evidence = ttk.Entry(frame, width=35)
            book.grid(row=i, column=1, sticky="ew", padx=4, pady=2)
            evidence.grid(row=i, column=2, sticky="ew", padx=4, pady=2)
            self.entries.append((book, evidence))
        book_date = self.entries[3][0]
        book_date.insert(0, self.cutoff.isoformat())
        book_date.configure(state="readonly")
        book_amount = tk.StringVar(self)
        self.entries[4][0].configure(state="readonly", textvariable=book_amount)
        if self.listing:
            listing_vars = [tk.StringVar(self) for _ in range(3)]
            for (widget, _), variable in zip(self.entries, listing_vars):
                widget.configure(state="readonly", textvariable=variable)

            def select(_):
                account = self.listing[3][self.account.current()].balance
                for variable, field in zip(listing_vars, (account.account_name, account.account_number, account.currency)):
                    variable.set(field)
                book_amount.set(format(account.balance, "f"))
        else:
            def select(_):
                book_amount.set(format(self.rows[self.account.current()].ending, "f"))
        self.account.bind("<<ComboboxSelected>>", select)
        if re.fullmatch(r"[+-]?[0-9][0-9,.]*", _normalize_amount_text(self.value)):
            self.entries[4][1].insert(0, self.value)
        preview = ScrolledText(frame, width=80, height=9, wrap="word")
        preview.insert("1.0", self.excerpt)
        preview.configure(state="disabled")
        preview.grid(row=9, column=0, columnspan=3, sticky="ew", pady=6)
        self.verified = tk.BooleanVar(self)
        confirmation = (
            "已核对清单与原件属于同一账户，两侧币种、截止日和金额单位可据实填列。"
            if self.listing else
            "已核对原件及账套，确认该科目只对应一个账号，账面余额为相同币种原币金额，"
            "两侧均按元等货币基本单位填列。"
        )
        ttk.Checkbutton(frame, variable=self.verified, text=confirmation).grid(
            row=10, column=0, columnspan=3, sticky="w")
        return self.account

    def validate(self):
        try:
            self.result = self._comparison()
        except ValueError as exc:
            messagebox.showerror("无法核对", str(exc), parent=self)
            return False
        return True

    def _comparison(self):
        if self.account.current() < 0 or self.kind.current() < 0:
            raise ValueError("请选择独立账户明细和证据类型。")
        if not self.verified.get():
            raise ValueError("请按原件核实独立账户、币种和金额单位后确认。")
        account_idx = self.account.current()
        row = self.rows[0] if self.listing else self.rows[account_idx]
        book, evidence = zip(*[(left.get().strip(), right.get().strip()) for left, right in self.entries])
        for label, field, adjacent in zip(
                ("户名", "账号", "币种", "余额截止日"),
                evidence[:4], ("", "0-9*＊", "a-zA-Z", "0-9"), strict=True):
            if not _found_in_excerpt(field, self.excerpt, adjacent):
                raise ValueError(f"银行证据{label}未在所选摘录中找到，请按原文填写或留空。")
        if evidence[-1] and (not _amount_in_excerpt(evidence[-1], self.excerpt)
                             or not _amount_in_excerpt(evidence[-1], self.value)):
            raise ValueError("余额须同时见于银行证据摘录和草稿的证据原值。")
        evidence_balance = BankBalance(*evidence[:3], _evidence_date(evidence[3]), _normalize_amount_text(evidence[4]))
        book_balance = (self.listing[3][account_idx].balance if self.listing else
                        BankBalance(*book[:3], self.cutoff, row.ending))
        kind = ("confirmation", "statement")[self.kind.current()]
        result = reconcile_bank_balance(book_balance, evidence_balance, evidence_kind=kind)
        status = {"pending": "待补或更正", "mismatch": "核对口径不一致",
                  "difference": "余额有差额", "matched": "余额一致"}[result.status]
        details = [
            f"{self.kind.get()}逐户核对 · {status} · 科目 {row.code} {row.path or row.name}",
            f"账面 {book_balance.account_name or '待补'} / {book_balance.account_number or '待补'} / "
            f"{book_balance.currency or '待补'} / {self.cutoff} / {book_balance.balance:f}",
            "证据 " + " / ".join(field or "待补" for field in evidence),
        ]
        if result.difference is not None:
            details.append(f"差额（银行证据减账面） {result.difference:f}")
        details.extend((*result.issues, result.recommendation))
        if self.listing:
            listing_id, digest, name, accounts = self.listing
            details.append(f"辅助清单《{name}》{SHEET}第 {accounts[account_idx].row} 行，"
                           f"全表合计 {row.ending:f}，SHA-256 {digest}")
            return BankComparison("；".join(details), listing_id)
        return BankComparison("；".join(details))
