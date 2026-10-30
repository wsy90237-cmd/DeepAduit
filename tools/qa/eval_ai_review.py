"""AI 复核闭环评测：只给疑点与候选，不给答案；AI 定来源，本地算金额，最后对答案。

评测口径：
- 命中：AI 给出的来源本地重算后与答案一致（允许 0.005 误差）
- 有差额：来源可用但金额不同（记录差额，判为需人工复核）
- 未解决：AI 判 hold/drop 或无可采纳来源
- 误填：AI 判 fill 但答案为空
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

from tb_autofill import ai, fields, matching, settings
from tb_autofill.models import LedgerRow, money

BASE = Path(__file__).resolve().parent
PLAN = ROOT / "local-data/tb-finish-20261030-1352"
TOLERANCE = Decimal("0.005")


def load_rows(path):
    return [LedgerRow(code=i["code"], name=i["name"], path=i.get("path", ""),
                      direction=i.get("direction", "借"), opening=money(i.get("opening")),
                      ending=money(i.get("ending")), debit=money(i.get("debit")),
                      credit=money(i.get("credit")), year=i.get("year", 0), month=i.get("month", 0),
                      ledger_id=i.get("ledger_id", ""))
            for i in json.loads(Path(path).read_text(encoding="utf-8"))]


def answers(path):
    values = {}
    book = load_workbook(path, data_only=True, keep_links=True)
    try:
        for sheet in book.worksheets:
            for cell in sheet._cells.values():
                if cell.value is not None:
                    values[(sheet.title, cell.coordinate)] = cell.value
    finally:
        book.close()
    return values


def main():
    plan = json.loads((PLAN / "trial-plan.json").read_text(encoding="utf-8"))
    plan = plan["cases"] if isinstance(plan, dict) and "cases" in plan else plan
    index = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    case = plan[index - 1]
    rows = load_rows(PLAN / f"fresh-ledger-{index}.json")
    key = answers(Path(case["path"]))
    scan = fields.scan_template(Path(case["path"]), ("opening", "ending"), unit="自动")
    results = matching.match_fields(scan.fields, rows)
    by_code = {row.code: row for row in rows}

    pending = [r for r in results if r.status != "protected" and (r.status != "matched" or not r.selected)]
    print(f"模板 {Path(case['path']).name} 字段 {len(scan.fields)} 疑点 {len(pending)}")

    client = ai.DeepSeekClient.from_store(settings.SettingsStore())
    counts, details = Counter(), []
    for start in range(0, len(pending), 20):
        batch = pending[start:start + 20]
        subset = [r for r in results if r in batch]
        decisions = ai.review(client, subset, rows, limit=20)
        lookup = {r.target.id: r for r in batch}
        for decision in decisions:
            result = lookup.get(decision["target_id"])
            if result is None:
                continue
            answer = key.get((result.target.sheet, result.target.cell))
            has_answer = answer is not None and str(answer).strip() != ""
            record = {"id": result.target.id, "label": result.target.label,"action": decision["action"],
                      "codes": decision["source_codes"], "answer": None if not has_answer else str(answer),
                      "reason": decision["reason"]}
            if decision["action"] == "fill" and decision["source_codes"]:
                chosen = [by_code[c] for c in decision["source_codes"] if c in by_code]
                check = matching.resolve_match(result.target, chosen, method="manual", rows=rows)
                record["amount"] = None if check.amount is None else str(check.amount)
                blank = result.target.value is None or str(result.target.value).strip() == ""
                if check.amount is None:
                    record["verdict"] = "未解决"
                elif check.amount == 0 and blank:
                    record["verdict"] = "零值不写"      # 与写入侧同一规则，不算误填
                elif not has_answer:
                    record["verdict"] = "误填"
                else:
                    try:
                        delta = Decimal(str(answer)) - check.amount
                    except Exception:
                        record["verdict"] = "答案非数值"
                    else:
                        record["delta"] = str(delta)
                        record["verdict"] = "命中" if abs(delta) <= TOLERANCE else "有差额"
            else:
                record["verdict"] = "未解决"
            counts[record["verdict"]] += 1
            details.append(record)

    print("结果:", dict(counts))
    for item in details:
        if item["verdict"] in {"命中", "有差额", "误填"}:
            print(f"  {item['verdict']:4} {item['label'][:16]:16} 答案={item['answer']} "
                  f"本地={item.get('amount')} 来源={item['codes']} 理由={item['reason'][:34]}")
    (BASE / f"ai-review-eval-{index}.json").write_text(
        json.dumps({"case": index, "counts": dict(counts), "details": details},
                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
