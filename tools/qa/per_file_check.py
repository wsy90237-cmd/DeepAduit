"""逐家核对：读鼎信诺账套 → 识别模板 → 逐格对答案 → 每家一行。

优化点：
- 按项目分组，一个项目只启动一次数据库副本，批量读取该项目下所有主体；
- 缓存键用「项目目录名__主体ID」，避免不同项目 ID 重复导致串号；
- 输出 JSON 与 Markdown 清单，逐家列出 一致/差额/漏填/误填 与样例。
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(r"P:/exetoBe/软件开发/ZSZH-tb自动填写系统")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".venv/Lib/site-packages"))

from openpyxl import load_workbook

from tb_autofill import dxn, fields, matching
from tb_autofill.models import LedgerRow, money

BASE = Path(__file__).resolve().parent
CACHE = BASE / "ledgers"
TOLERANCE = Decimal("0.005")
INSTALL = r"E:\person\dxnservice"


def answers_of(path):
    values, formulas = {}, set()
    book = load_workbook(path, data_only=True, keep_links=True)
    try:
        for sheet in book.worksheets:
            for cell in sheet._cells.values():
                if cell.value is not None:
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


def cache_key(subject):
    project = Path(subject.project_path).name if subject.project_path else "unknown"
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", f"{project}__{subject.id}")


def store_rows(subject, rows):
    CACHE.mkdir(parents=True, exist_ok=True)
    payload = [{"code": row.code, "name": row.name, "path": row.path, "direction": row.direction,
                "opening": None if row.opening is None else str(row.opening),
                "ending": None if row.ending is None else str(row.ending),
                "debit": None if row.debit is None else str(row.debit),
                "credit": None if row.credit is None else str(row.credit),
                "level": row.level, "year": row.year, "month": row.month,
                "ledger_id": row.ledger_id} for row in rows]
    (CACHE / f"{cache_key(subject)}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_cache(subject):
    path = CACHE / f"{cache_key(subject)}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return [LedgerRow(code=i["code"], name=i["name"], path=i.get("path", ""),
                      direction=i.get("direction", "借"), opening=money(i.get("opening")),
                      ending=money(i.get("ending")), debit=money(i.get("debit")),
                      credit=money(i.get("credit")), level=i.get("level", 1),
                      year=i.get("year", 0), month=i.get("month", 0),
                      ledger_id=i.get("ledger_id", "")) for i in data]


def check_one(template, rows):
    scan = fields.scan_template(template, ("opening", "ending"), unit="自动")
    results = matching.match_fields(scan.fields, rows)
    answers, formulas = answers_of(template)
    counts, samples = Counter(), []
    for result in results:
        key = (result.target.sheet, result.target.cell)
        answer = answers.get(key)
        blank_answer = answer is None or str(answer).strip() == ""
        if key in formulas or result.status == "protected":
            counts["公式保护"] += 1
            continue
        if result.selected and result.amount is not None:
            if blank_answer:
                counts["误填"] += 1
                samples.append(["误填", result.target.label, None, str(result.amount)])
            else:
                try:
                    delta = Decimal(str(answer)) - result.amount
                except Exception:
                    counts["答案非数值"] += 1
                    continue
                if abs(delta) <= TOLERANCE:
                    counts["一致"] += 1
                else:
                    counts["有差额"] += 1
                    samples.append(["有差额", result.target.label, str(answer), str(result.amount)])
        elif blank_answer:
            counts["双方皆空"] += 1
        else:
            counts["漏填"] += 1
            samples.append(["漏填", result.target.label, str(answer), None])
    return {"fields": len(scan.fields), "unit": scan.unit, "counts": dict(counts), "samples": samples[:8]}


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"C:\Users\29294\Desktop\deeepaudit-训练文件\TB-2025闽侯建投0428定稿")
    templates = sorted(folder.rglob("TB-*.xlsx"))
    print(f"模板 {len(templates)} 份，目录 {folder}", flush=True)

    index = defaultdict(list)
    for project in dxn.scan_projects(INSTALL):
        try:
            for subject in dxn.list_subjects(str(project)):
                index[subject.name].append(subject)
        except Exception as exc:
            print("项目读取失败", project.name, exc, flush=True)
    print(f"可用主体 {sum(len(v) for v in index.values())} 条", flush=True)

    plan = []
    for template in templates:
        name = re.sub(r"^\d+", "", template.stem.replace("TB-", "").split("（")[0].split("(")[0]).strip()
        matches = index.get(name) or []
        plan.append((template, matches[0] if matches else None))

    by_project = defaultdict(list)
    for template, subject in plan:
        if subject is not None:
            by_project[str(subject.project_path)].append((template, subject))
    for project, items in by_project.items():
        missing = [subject for _t, subject in items if load_cache(subject) is None]
        if not missing:
            continue
        print(f"读取项目 {Path(project).name}：{len(missing)} 个主体", flush=True)
        started = time.perf_counter()
        try:
            with dxn.DxnLedgerDB(project, INSTALL) as database:
                for subject in missing:
                    store_rows(subject, database.read(subject))
        except Exception as exc:
            print("  项目读取失败:", repr(exc)[:120], flush=True)
            continue
        print(f"  完成，用时 {time.perf_counter() - started:.1f}s", flush=True)

    report, totals = [], Counter()
    for template, subject in plan:
        if subject is None:
            report.append({"template": template.name, "status": "缺账套"})
            continue
        rows = load_cache(subject)
        if rows is None:
            report.append({"template": template.name, "status": "账套读取失败"})
            continue
        started = time.perf_counter()
        try:
            outcome = check_one(template, rows)
            outcome.update({"template": template.name, "status": "已核对", "subject": subject.name,
                            "period": f"{subject.year}-{subject.month:02d}",
                            "seconds": round(time.perf_counter() - started, 1)})
        except Exception as exc:
            outcome = {"template": template.name, "status": "出错", "detail": repr(exc)[:140]}
        for key, value in (outcome.get("counts") or {}).items():
            totals[key] += value
        report.append(outcome)
        counts = outcome.get("counts") or {}
        print(f"  {template.name[:34]:34} {outcome['status']:4} " +
              " ".join(f"{k}{v}" for k, v in counts.items()), flush=True)

    (BASE / "per-file-report.json").write_text(
        json.dumps({"report": report, "totals": dict(totals)}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    lines = ["| 模板 | 期间 | 字段 | 一致 | 有差额 | 漏填 | 误填 |", "|---|---|---|---|---|---|---|"]
    for item in report:
        counts = item.get("counts") or {}
        lines.append(f"| {item['template'][:30]} | {item.get('period', '—')} | {item.get('fields', '—')} | "
                     f"{counts.get('一致', 0)} | {counts.get('有差额', 0)} | {counts.get('漏填', 0)} | "
                     f"{counts.get('误填', 0)} |")
    (BASE / "per-file-report.md").write_text("\n".join(lines), encoding="utf-8")
    print("合计:", dict(totals), flush=True)
    print("状态:", dict(Counter(item["status"] for item in report)), flush=True)


if __name__ == "__main__":
    main()
