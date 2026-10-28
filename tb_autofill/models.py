from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import json


def money(value) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("金额不能是布尔值")
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"无法识别金额：{value}") from exc
    if not result.is_finite():
        raise ValueError("金额必须为有限数值")
    return result


@dataclass(frozen=True)
class LedgerRow:
    code: str
    name: str
    path: str = ""
    direction: str = "借"
    opening: Decimal | None = None
    ending: Decimal | None = None
    debit: Decimal | None = None
    credit: Decimal | None = None
    level: int = 1
    subject_id: str = ""
    year: int = 0
    month: int = 0
    basis: str = "book"
    is_profit_loss: bool = False
    ledger_id: str = ""


@dataclass(frozen=True)
class Subject:
    id: str
    name: str
    year: int
    month: int
    project_path: str
    source_path: str = ""
    ledger_id: str = ""

    @property
    def label(self):
        suffix = f" / 账套{self.ledger_id}" if self.ledger_id else ""
        return f"{self.name} / {self.year}-{self.month:02d} / {self.id}{suffix}"


@dataclass(frozen=True)
class TargetField:
    sheet: str
    cell: str
    label: str
    period: str
    basis: str = "book"
    parent: str = ""
    index_code: str = ""
    value: object = None
    is_formula: bool = False
    measure: str = "balance"
    balance_side: str = ""
    unit_factor: Decimal = Decimal("1")
    occurrence_scope: str = "year_to_date"
    statement: str = ""
    presentation_direction: str = ""
    component: str = ""

    @property
    def id(self):
        return f"{self.sheet}!{self.cell}"


@dataclass
class MatchResult:
    target: TargetField
    sources: list[LedgerRow] = field(default_factory=list)
    amount: Decimal | None = None
    status: str = "unmatched"
    method: str = ""
    reason: str = ""
    selected: bool = False
    overwrite: bool = False

    @property
    def difference(self):
        if self.amount is None or self.target.is_formula:
            return None
        try:
            actual = money(self.target.value)
        except ValueError:
            return None
        return self.amount - actual if actual is not None else None

    @property
    def source_ids(self):
        return [row.code for row in self.sources]


def ledger_digest(rows) -> str:
    """Bind a review to exact loaded values, including subject and period."""
    records = sorted((asdict(row) for row in rows), key=lambda row: str(row["code"]))
    content = json.dumps(records, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
