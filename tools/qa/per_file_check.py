"""逐家核对：读鼎信诺账套 → 识别模板 → 逐格对答案 → 每家一行。

优化点：
- 按项目分组，一个项目只启动一次数据库副本，批量读取该项目下所有主体；
- 缓存键用「项目目录名__主体ID」，避免不同项目 ID 重复导致串号；
- 输出 JSON 与 Markdown 清单；原填空白、未可靠取数单列待核对。
"""
from __future__ import annotations

import json
from dataclasses import asdict, replace
import hashlib
import site
import re
import sys
import time
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
site.addsitedir(str(ROOT / ".venv/Lib/site-packages"))

from openpyxl import load_workbook

from tb_autofill import dxn, fields, matching
from tb_autofill.models import LedgerRow, money

BASE = ROOT / 'local-data/qa'
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
    context = [str(Path(subject.project_path).resolve()), subject.id, subject.year, subject.month, subject.ledger_id]
    return hashlib.sha256(json.dumps(context, ensure_ascii=False).encode('utf-8')).hexdigest()


def store_rows(subject, rows):
    CACHE.mkdir(parents=True, exist_ok=True)
    payload = [asdict(row) for row in rows]
    (CACHE / f"{cache_key(subject)}.json").write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")


def load_cache(subject):
    path = CACHE / f"{cache_key(subject)}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for item in data:
        if not {'level', 'basis', 'is_profit_loss', 'subject_id'} <= item.keys():
            return None
        for key in ('opening', 'ending', 'debit', 'credit'):
            item[key] = money(item.get(key))
        rows.append(LedgerRow(**item))
    return rows


def check_one(template, rows):
    scan = fields.scan_template(template, ("opening", "ending"), unit="自动")
    hidden = [replace(field, value=None) if not field.is_formula else field for field in scan.fields]
    results = matching.match_fields(hidden, rows)
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
                counts["原填空白，待核对"] += 1
                samples.append(["原填空白，待核对", result.target.label, None, str(result.amount)])
            else:
                try:
                    delta = (money(answer) - result.amount) * result.target.unit_factor
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
            counts["未可靠取数，待复核"] += 1
            samples.append(["未可靠取数，待复核", result.target.label, str(answer), None])
    return {"fields": len(scan.fields), "unit": scan.unit, "counts": dict(counts), "samples": samples[:8]}


def main():
    BASE.mkdir(parents=True, exist_ok=True)
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
        plan.append((template, matches[0] if len(matches) == 1 else None))

    by_project = defaultdict(list)
    for template, subject in plan:
        if subject is not None:
            by_project[str(subject.project_path)].append((template, subject))
    refreshed = set()
    for project, items in by_project.items():
        # Each run reads a fresh consistent copy; disk caches are not evidence of source freshness.
        missing = [subject for _t, subject in items]
        if not missing:
            continue
        print(f"读取项目 {Path(project).name}：{len(missing)} 个主体", flush=True)
        started = time.perf_counter()
        try:
            with dxn.DxnLedgerDB(project, INSTALL) as database:
                for subject in missing:
                    store_rows(subject, database.read(subject))
                    refreshed.add(cache_key(subject))
        except Exception as exc:
            print("  项目读取失败:", repr(exc)[:120], flush=True)
            continue
        print(f"  完成，用时 {time.perf_counter() - started:.1f}s", flush=True)

    report, totals = [], Counter()
    for template, subject in plan:
        if subject is None:
            report.append({"template": template.name, "status": "缺账套"})
            continue
        rows = load_cache(subject) if cache_key(subject) in refreshed else None
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
    lines = ["| 模板 | 期间 | 字段 | 可比一致 | 有差额 | 未可靠取数 | 原填空白待核对 |", "|---|---|---|---|---|---|---|"]
    for item in report:
        counts = item.get("counts") or {}
        lines.append(f"| {item['template'][:30]} | {item.get('period', '—')} | {item.get('fields', '—')} | "
                     f"{counts.get('一致', 0)} | {counts.get('有差额', 0)} | {counts.get('未可靠取数，待复核', 0)} | "
                     f"{counts.get('原填空白，待核对', 0)} |")
    (BASE / "per-file-report.md").write_text("\n".join(lines), encoding="utf-8")
    print("合计:", dict(totals), flush=True)
    print("状态:", dict(Counter(item["status"] for item in report)), flush=True)


if __name__ == "__main__":
    main()
