"""同一账套 × 多套模板交叉校验：每个写入数字都必须能回溯到具体科目与取值。

检查三类问题：
1. 不可回溯：匹配成功但金额无法由来源科目重算得出，或来源不在账套里；
2. 写入不一致：写进单元格的数值与本地计算值不同；
3. 跨模板冲突：同一报表项目在两套模板里算出的金额不同（单位、期间、科目范围差异）。
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(r"P:/exetoBe/软件开发/ZSZH-tb自动填写系统")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".venv/Lib/site-packages"))

from openpyxl import load_workbook

from tb_autofill import fields, matching, workbook
from tb_autofill.models import LedgerRow, money

BASE = Path(__file__).resolve().parent
PLAN = ROOT / "local-data/tb-finish-20261030-1352"


def load_rows(path):
    rows = []
    for item in json.loads(Path(path).read_text(encoding="utf-8")):
        rows.append(LedgerRow(
            code=item["code"], name=item["name"], path=item.get("path", ""),
            direction=item.get("direction", "借"),
            opening=money(item.get("opening")), ending=money(item.get("ending")),
            debit=money(item.get("debit")), credit=money(item.get("credit")),
            level=item.get("level", 1), subject_id=item.get("subject_id", ""),
            year=item.get("year", 0), month=item.get("month", 0),
            basis=item.get("basis", "book"), is_profit_loss=item.get("is_profit_loss", False),
            ledger_id=item.get("ledger_id", "")))
    return rows


def trace(result, by_code):
    """金额溯源：来源科目 + 期间取值 → 独立重算，与本地结果比对。"""
    factor = result.target.unit_factor or Decimal(1)
    total, items, missing = Decimal(0), [], []
    for code in result.source_ids or ():
        row = by_code.get(code)
        if row is None:
            missing.append(code)
            continue
        value = row.opening if result.target.period == "opening" else row.ending
        if value is None:
            missing.append(code)
            continue
        total += value
        items.append({"code": code, "name": row.name, "value": str(value)})
    recomputed = total / factor if factor else total
    amount = result.amount
    ok = amount is not None and abs(recomputed - amount) <= Decimal("0.005")
    return {"sources": items, "missing_sources": missing, "recomputed": str(recomputed),
            "amount": None if amount is None else str(amount), "factor": str(factor),
            "matches": ok}


def run_template(template, rows, tag):
    started = time.perf_counter()
    scan = fields.scan_template(template, ("opening", "ending"), unit="自动")
    results = matching.match_fields(scan.fields, rows)
    by_code = {row.code: row for row in rows}
    records, problems = [], []
    for result in results:
        record = {"id": result.target.id, "label": result.target.label, "period": result.target.period,
                  "basis": result.target.basis, "status": result.status,
                  "unit": str(result.target.unit_factor),
                  "sources": list(result.source_ids or ()), "reason": result.reason,
                  "amount": None if result.amount is None else str(result.amount),
                  "existing": None if result.target.value is None else str(result.target.value)}
        if result.status == "matched":
            record.update(trace(result, by_code))
            if record.get("missing_sources"):
                problems.append(dict(record, kind="来源不在账套"))
            elif not record.get("matches"):
                problems.append(dict(record, kind="金额不可重算"))
        records.append(record)

    # 写入并回读，确认单元格里就是本地计算值
    chosen = [dict(result.__dict__) for result in results]
    written, readback_mismatch = 0, []
    output = BASE / f"filled-{tag}.xlsx"
    try:
        selected = []
        for result in results:
            result.selected = result.status == "matched" and not result.target.is_formula
            selected.append(result)
        targets = [(result.target, result.amount) for result in results
                   if result.selected and result.amount is not None]
        if targets:
            workbook.write_results(scan, results, output, rows=rows)
            written = len(targets)
            book = load_workbook(output, data_only=False)
            try:
                for target, amount in targets:
                    cell = book[target.sheet][target.cell]
                    if money(cell.value) != money(amount):
                        readback_mismatch.append({"id": target.id, "cell": cell.value,
                                                  "expected": str(amount)})
            finally:
                book.close()
    except Exception as exc:                     # 写入失败如实记录，不吞掉
        problems.append({"kind": "写入异常", "detail": repr(exc)})

    return {"tag": tag, "template": template.name, "seconds": round(time.perf_counter() - started, 2),
            "unit": scan.unit, "fields": len(scan.fields),
            "matched": sum(1 for r in results if r.status == "matched"),
            "written": written, "readback_mismatch": readback_mismatch,
            "status_counts": dict((s, sum(1 for r in results if r.status == s))
                                  for s in {r.status for r in results}),
            "records": records, "problems": problems}


def main():
    plan = json.loads((PLAN / "trial-plan.json").read_text(encoding="utf-8"))
    plan = plan["cases"] if isinstance(plan, dict) and "cases" in plan else plan
    ledger = PLAN / "fresh-ledger-1.json"
    rows = load_rows(ledger)
    print(f"账套：{ledger.name} 行数 {len(rows)}")

    runs = []
    for index, case in enumerate(plan, 1):
        template = Path(case["path"])
        if not template.exists():
            print("跳过缺失模板", template)
            continue
        run = run_template(template, rows, index)
        runs.append(run)
        print(f"[{index}] {run['template'][:34]:34} 字段{run['fields']:5} 匹配{run['matched']:5} "
              f"写入{run['written']:4} 问题{len(run['problems'])} {run['seconds']}s")

    # 跨模板一致性：同项目 + 同期间，金额应一致
    buckets = defaultdict(list)
    for run in runs:
        for record in run["records"]:
            if record["status"] != "matched" or record["amount"] is None:
                continue
            key = (record["label"], record["period"], record["basis"])
            buckets[key].append({"template": run["template"], "unit": run["unit"],
                                 "amount": record["amount"], "sources": record["sources"]})
    conflicts = []
    for (label, period, basis), items in buckets.items():
        values = {item["amount"] for item in items}
        if len(values) > 1:
            conflicts.append({"label": label, "period": period, "basis": basis, "entries": items})

    report = {"ledger": str(ledger), "rows": len(rows), "runs": runs,
              "cross_template_conflicts": conflicts,
              "totals": {"problems": sum(len(r["problems"]) for r in runs),
                         "readback_mismatch": sum(len(r["readback_mismatch"]) for r in runs),
                         "conflicts": len(conflicts)}}
    (BASE / "crosstest-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                                encoding="utf-8")
    print(json.dumps(report["totals"], ensure_ascii=False))
    for problem in (p for run in runs for p in run["problems"][:4]):
        print("  问题:", problem.get("kind"), problem.get("label") or problem.get("id"),
              "来源", problem.get("sources"), "金额", problem.get("amount"),
              "重算", problem.get("recomputed"), "缺", problem.get("missing_sources"))
    for conflict in conflicts[:6]:
        print("  冲突:", conflict["label"], conflict["period"],
              [(c["template"][:18], c["unit"], c["amount"]) for c in conflict["entries"]])


if __name__ == "__main__":
    main()
