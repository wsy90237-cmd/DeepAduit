"""按训练文件里的已填 TB 作为答案，逐格比对本地填列结果。

答案分两类：
- 叶子单元格（数值）：直接比对金额；
- 公式单元格：引擎标记 protected 不写入，比对时只核对公式是否被保留，
  其值由 Excel 计算，不由我们填写。

输出四类结论：一致 / 有差额 / 漏填（答案有值我们没填）/ 误填（我们填了答案为空）。
每个不一致项都附来源科目与取值，便于回溯数字来源。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(r"P:/exetoBe/软件开发/ZSZH-tb自动填写系统")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".venv/Lib/site-packages"))

from openpyxl import load_workbook

from tb_autofill import fields, matching
from tb_autofill.models import LedgerRow, money

BASE = Path(__file__).resolve().parent
PLAN = ROOT / "local-data/tb-finish-20261030-1352"
TOLERANCE = Decimal("0.005")


def load_rows(path):
    rows = []
    for item in json.loads(Path(path).read_text(encoding="utf-8")):
        rows.append(LedgerRow(
            code=item["code"], name=item["name"], path=item.get("path", ""),
            direction=item.get("direction", "借"), opening=money(item.get("opening")),
            ending=money(item.get("ending")), debit=money(item.get("debit")),
            credit=money(item.get("credit")), level=item.get("level", 1),
            subject_id=item.get("subject_id", ""), year=item.get("year", 0), month=item.get("month", 0),
            basis=item.get("basis", "book"), is_profit_loss=item.get("is_profit_loss", False),
            ledger_id=item.get("ledger_id", "")))
    return rows


def answer_values(path):
    """读取答案：data_only=True 取 Excel 缓存值；同时记录哪些是公式格。"""
    values, formulas = {}, set()
    book = load_workbook(path, data_only=True, keep_links=True)
    try:
        for sheet in book.worksheets:
            for cell in sheet._cells.values():
                if cell.value is None:
                    continue
                values[(sheet.title, cell.coordinate)] = cell.value
    finally:
        book.close()
    book = load_workbook(path, data_only=False, keep_links=True)
    try:
        for sheet in book.worksheets:
            for cell in sheet._cells.values():
                if cell.data_type == "f":
                    formulas.add((sheet.title, cell.coordinate))
    finally:
        book.close()
    return values, formulas


def describe(result, by_code):
    parts, total = [], Decimal(0)
    factor = result.target.unit_factor or Decimal(1)
    for code in result.source_ids or ():
        row = by_code.get(code)
        value = None if row is None else (row.opening if result.target.period == "opening" else row.ending)
        if value is None:
            parts.append(f"{code}:无值")
            continue
        total += value
        parts.append(f"{code} {row.name}={value:,}")
    return {"sources": parts, "raw_total": str(total), "factor": str(factor),
            "recomputed": str(total / factor if factor else total)}


def compare(case_index, template, ledger_file):
    rows = load_rows(ledger_file)
    by_code = {row.code: row for row in rows}
    scan = fields.scan_template(template, ("opening", "ending"), unit="自动")
    results = matching.match_fields(scan.fields, rows)
    answers, formulas = answer_values(template)

    items, counts = [], Counter()
    for result in results:
        key = (result.target.sheet, result.target.cell)
        answer = answers.get(key)
        answer_missing = answer is None or str(answer).strip() == ""
        is_formula = key in formulas
        entry = {"id": result.target.id, "label": result.target.label,
                 "period": result.target.period, "basis": result.target.basis,
                 "status": result.status, "selected": bool(result.selected), "formula": is_formula,
                 "answer": None if answer_missing else str(answer),
                 "ours": None if result.amount is None else str(result.amount),
                 "sources": list(result.source_ids or ()), "reason": result.reason}
        if is_formula:
            counts["公式保护"] += 1
            entry["verdict"] = "公式保护"
        elif result.selected and result.amount is not None:
            if answer_missing:
                entry["verdict"] = "误填"
            else:
                try:
                    delta = Decimal(str(answer)) - Decimal(str(result.amount))
                except Exception:
                    entry["verdict"] = "答案非数值"
                else:
                    entry["delta"] = str(delta)
                    entry["verdict"] = "一致" if abs(delta) <= TOLERANCE else "有差额"
        elif result.status == "protected":
            counts["公式保护"] += 1
            entry["verdict"] = "公式保护"
        elif answer_missing:
            entry["verdict"] = "双方皆空"
        elif result.status == "matched":
            entry["verdict"] = "匹配未勾选"
        else:
            entry["verdict"] = "漏填"
        if entry["verdict"] in {"有差额", "漏填", "误填"}:
            entry.update(describe(result, by_code))
        counts[entry["verdict"]] += 1
        items.append(entry)

    return {"case": case_index, "template": template.name, "ledger": Path(ledger_file).name,
            "unit": scan.unit, "fields": len(scan.fields), "counts": dict(counts), "items": items}


def main():
    plan = json.loads((PLAN / "trial-plan.json").read_text(encoding="utf-8"))
    plan = plan["cases"] if isinstance(plan, dict) and "cases" in plan else plan
    report = []
    for index, case in enumerate(plan, 1):
        template = Path(case["path"])
        ledger = PLAN / f"fresh-ledger-{index}.json"
        if not template.exists() or not ledger.exists():
            print("跳过", template.name, ledger.exists())
            continue
        result = compare(index, template, ledger)
        report.append(result)
        print(f"[{index}] {result['template'][:30]:30} 字段{result['fields']:5} " +
              " ".join(f"{k}{v}" for k, v in result["counts"].items()))

    totals = Counter()
    for result in report:
        for key, value in result["counts"].items():
            totals[key] += value
    (BASE / "answer-comparison.json").write_text(json.dumps({"cases": report, "totals": dict(totals)},
                                                            ensure_ascii=False, indent=2, default=str),
                                                 encoding="utf-8")
    print("合计:", dict(totals))

    for result in report:
        bad = [item for item in result["items"] if item["verdict"] in {"有差额", "漏填", "误填"}]
        for item in bad[:6]:
            print(f"  [{item['verdict']}] {item['label'][:16]:16} 答案={item['answer']} 我们={item['ours']} "
                  f"来源={item.get('sources')} 重算={item.get('recomputed')}")


if __name__ == "__main__":
    main()
