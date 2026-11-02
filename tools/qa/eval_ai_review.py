"""AI 复核闭环评测：只给疑点与候选，不给答案；AI 定来源，本地算金额，最后对答案。

评测口径：
- 可比一致：AI 给出的来源本地重算后与原填数一致（允许 0.005 元误差）
- 有差额：来源可用但金额不同（记录差额，判为需人工复核）
- 未解决：AI 判 hold/drop 或无可采纳来源
- 原填空白：单列待核对，不直接判定为误填；默认只生成发送预览
"""
from __future__ import annotations

import json
import argparse
from dataclasses import replace
import hashlib
import site
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
site.addsitedir(str(ROOT / ".venv/Lib/site-packages"))

from openpyxl import load_workbook

from tb_autofill import ai, fields, matching, settings
from tb_autofill.models import LedgerRow, money

BASE = ROOT / 'local-data/qa'
PLAN = ROOT / "local-data/tb-finish-20261030-1352"
TOLERANCE = Decimal("0.005")


def load_rows(path):
    rows = []
    for item in json.loads(Path(path).read_text(encoding="utf-8")):
        if not {'level', 'basis', 'is_profit_loss', 'subject_id'} <= item.keys():
            raise ValueError('账套快照缺少来源上下文，请重新生成完整快照')
        for key in ('opening', 'ending', 'debit', 'credit'):
            item[key] = money(item.get(key))
        rows.append(LedgerRow(**item))
    return rows


def preview_reviews(client, batches, rows, preview_path, approved_sha256=None):
    envelope = {'purpose': 'TB疑点来源复核评测', 'model': client.model,
                'destination': client.base_url,
                'requests': [ai.review_payload(batch, rows, limit=20) for batch in batches]}
    text = json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2)
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(text, encoding='utf-8', newline='\n')
    print(f'发送预览：{preview_path}；服务：{client.base_url}；SHA256：{digest}')
    if approved_sha256 is None:
        return None
    if approved_sha256 != digest:
        raise ValueError('预览范围或目的服务已变化，未发送；请重新核对预览')
    return [ai.review(client, batch, rows, limit=20) for batch in batches]


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
    parser = argparse.ArgumentParser()
    parser.add_argument('index', type=int, nargs='?', default=1)
    parser.add_argument('--plan', type=Path, default=PLAN / 'trial-plan.json')
    parser.add_argument('--ledger', type=Path)
    parser.add_argument('--approve-preview', help='人工核对预览范围与目的服务后提供其 SHA256')
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    plan = plan["cases"] if isinstance(plan, dict) and "cases" in plan else plan
    index = args.index
    if not 1 <= index <= len(plan):
        parser.error('样本序号超出计划范围')
    case = plan[index - 1]
    rows = load_rows(args.ledger or PLAN / f"fresh-ledger-{index}.json")
    key = answers(Path(case["path"]))
    scan = fields.scan_template(Path(case["path"]), ("opening", "ending"), unit="自动")
    hidden = [replace(field, value=None) if not field.is_formula else field for field in scan.fields]
    results = matching.match_fields(hidden, rows)
    by_code = {row.code: row for row in rows}

    pending = [r for r in results if r.status != "protected" and (r.status != "matched" or not r.selected)]
    print(f"模板 {Path(case['path']).name} 字段 {len(scan.fields)} 疑点 {len(pending)}")

    client = ai.DeepSeekClient.from_store(settings.SettingsStore())
    batches = [pending[start:start + 20] for start in range(0, len(pending), 20)]
    decisions_by_batch = preview_reviews(client, batches, rows, BASE / f'ai-review-preview-{index}.json', args.approve_preview)
    if decisions_by_batch is None:
        print('仅保存预览，未发送请求。核对后用 --approve-preview SHA256 再执行。')
        return
    counts, details = Counter(), []
    for batch, decisions in zip(batches, decisions_by_batch):
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
                check = matching.resolve_match(result.target, chosen, method="ai", rows=rows)
                record["amount"] = None if check.amount is None else str(check.amount)
                blank = result.target.value is None or str(result.target.value).strip() == ""
                if check.status != 'matched' or check.amount is None:
                    record["verdict"] = "未解决"
                elif check.amount == 0 and blank:
                    record["verdict"] = "零值不写"      # 与写入侧同一规则，不算误填
                elif not has_answer:
                    record["verdict"] = "原填空白，待核对"
                else:
                    try:
                        delta = (money(answer) - check.amount) * result.target.unit_factor
                    except Exception:
                        record["verdict"] = "答案非数值"
                    else:
                        record["delta"] = str(delta)
                        record["verdict"] = "可比一致" if abs(delta) <= TOLERANCE else "有差额"
            else:
                record["verdict"] = "未解决"
            counts[record["verdict"]] += 1
            details.append(record)

    print("结果:", dict(counts))
    for item in details:
        if item["verdict"] in {"可比一致", "有差额", "原填空白，待核对"}:
            print(f"  {item['verdict']:4} {item['label'][:16]:16} 答案={item['answer']} "
                  f"本地={item.get('amount')} 来源={item['codes']} 理由={item['reason'][:34]}")
    (BASE / f"ai-review-eval-{index}.json").write_text(
        json.dumps({"case": index, "counts": dict(counts), "details": details},
                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
