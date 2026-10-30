"""审计底稿草稿：把本地账套事实与内置知识交给模型，产出的仍是待复核草稿。

金额只来自账套与本地点选结果；模型负责组织审计目标、程序、执行记录与结论，
缺证据时必须写"待补"，不得编造凭证号、抽样数量或审定结论。
"""

from __future__ import annotations

from decimal import Decimal

ZERO = Decimal("0")
from pathlib import Path

from . import analysis, skills
from .models import money

KINDS = {
    "audit": ("科目审定表", "按科目列示期初、本期发生额、期末与审定差异，并给出结论与待补证据。"),
    "program": ("审计程序与执行记录", "列出该科目的认定、对应程序、执行情况、样本与结论。"),
    "analysis": ("明细分析与勾稽", "分析余额构成、异常波动、与报表项目的勾稽关系。"),
}
DEFAULT_KIND = "audit"
KIND_LABELS = {key: value[0] for key, value in KINDS.items()}

SYSTEM = (
    "你是审计项目经理，负责起草工作底稿草稿，供注册会计师复核后使用。"
    "只能使用给定的事实清单与内置知识条目，禁止编造金额、凭证号、抽样比例、日期或结论。"
    "金额与差异必须逐字引用给定值，不得自行计算或改写；没有事实支撑的栏目统一写“待补”。"
    "结构固定为：一、审计目标；二、资料来源与范围；三、程序与执行记录；四、数据与差异分析；"
    "五、结论与待补证据。"
    "结论只能写到“基于目前资料可支持/不支持”的层面，不得声称已完成全部审计程序或已出具报告。"
    "引用内置知识时使用 [K1] 这类给定编号，不得创造编号。"
)


def _amount(value):
    try:
        amount = money(value)
    except (ValueError, TypeError):
        return ""
    return "" if amount is None else f"{amount:,}"


def account_choices(rows, limit=80):
    """可制作底稿的科目：优先有金额的叶子科目，按金额绝对值排序。"""
    if not rows:
        return []
    parent_paths = {row.path for row in rows if row.path}
    picked = []
    for row in rows:
        has_child = any(path.startswith(f"{row.path}/") for path in parent_paths) if row.path else False
        weight = (abs(row.ending or ZERO) + abs(row.debit or ZERO) + abs(row.credit or ZERO))
        if has_child and weight == ZERO:      # 无金额的汇总科目对底稿没有意义
            continue
        picked.append((weight, row))
    picked.sort(key=lambda item: (-item[0], item[1].code))
    return [(row.code, f"{row.code} {row.name}") for _weight, row in picked[:limit]]


def build_facts(rows, code):
    """单个科目的本地事实：期初、借贷发生额、期末、方向与父级路径。"""
    target = next((row for row in rows if row.code == code), None)
    if target is None:
        raise ValueError("未找到该科目，请重新读取账套。")
    prefix = f"{target.path}/" if target.path else ""
    children = [row for row in rows if prefix and row.path.startswith(prefix)]
    related = [row for row in rows if row.code != target.code and target.name and target.name in row.name]
    return {
        "subject": {"code": target.code, "name": target.name, "path": target.path,
                    "direction": target.direction,
                    "opening": _amount(target.opening), "debit": _amount(target.debit),
                    "credit": _amount(target.credit), "ending": _amount(target.ending),
                    "level": target.level, "is_profit_loss": target.is_profit_loss},
        "children": [{"code": row.code, "name": row.name, "ending": _amount(row.ending)} for row in children[:20]],
        "similarly_named": [{"code": row.code, "name": row.name, "ending": _amount(row.ending)}
                            for row in related[:10]],
        "ledger_scope": {"rows": len(rows),
                         "note": "仅本期、本主体、本账套版本；不含凭证、合同、函证与实物证据。"},
    }


def draft(client, *, rows, code, kind=DEFAULT_KIND, subject_label="", focus="", limit=5):
    """生成底稿草稿；返回正文与本次引用的知识条目。"""
    kind = kind if kind in KINDS else DEFAULT_KIND
    facts = build_facts(rows, code)
    facts["kind"] = KINDS[kind][0]
    facts["kind_guidance"] = KINDS[kind][1]
    facts["subject_label"] = subject_label
    facts["focus"] = str(focus or "")[:500]
    movements = []
    for item in analysis.movements(rows)[:20] if rows else []:
        movements.append({"code": item.code, "name": item.name, "path": item.path,
                          "direction": item.direction,
                          "opening": _amount(item.opening), "ending": _amount(item.ending),
                          "change": _amount(item.change),
                          "rate": "" if item.rate is None else f"{item.rate:.4f}"})
    facts["movements"] = movements

    query = " ".join(part for part in (facts["subject"]["name"], facts["kind"], facts["focus"],
                                       "审计程序 认定 底稿") if part)
    hits = skills.search(query, limit=limit)
    refs = skills.as_knowledge_refs(hits, limit=limit)

    context = {"facts": facts, "knowledge_refs": refs,
               "constraints": {"amount_source": "账套", "no_fabrication": True,
                               "status": "草稿，需注册会计师复核"}}
    question = (
        f"请为科目 {facts['subject']['code']} {facts['subject']['name']} 起草《{KINDS[kind][0]}》。"
        "只依据 facts 与知识条目，缺证据的栏目写“待补”。"
    )
    answer = client.converse_with_skills(question, context=context)
    if answer.get("intent") != "audit":
        raise ValueError(answer.get("answer") or "模型未按审计任务回答，未生成底稿。")
    body = str(answer.get("answer") or "").strip()
    if not body:
        raise ValueError("模型返回空内容，未生成底稿。")
    return {"kind": kind, "kind_label": KINDS[kind][0], "subject": facts["subject"],
            "body": body, "refs": refs,
            "warnings": answer.get("warnings", []) + answer.get("citation_warnings", [])}


def to_html(title, draft, *, subject_label=""):
    """导出可读的底稿草稿（非正式报告，页眉明确标注）。"""
    def escape(text):
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    paragraphs, bullets = [], []

    def flush():
        if bullets:
            paragraphs.append("<ul>" + "".join(bullets) + "</ul>")
            bullets.clear()

    for line in str(draft["body"]).splitlines():
        text = line.strip()
        if not text:
            flush()
            continue
        if text.startswith("#"):
            flush()
            level = min(3, len(text) - len(text.lstrip("#")) + 1)
            paragraphs.append(f"<h{level}>{escape(text.lstrip('# ').strip())}</h{level}>")
        elif text.startswith(("- ", "* ")):
            bullets.append(f"<li>{escape(text[2:])}</li>")
        else:
            flush()
            paragraphs.append(f"<p>{escape(text)}</p>")
    flush()
    refs = "".join(
        f"<li><b>[{escape(item['citation_id'])}]</b> {escape(item['title'])}"
        f"<span class='src'>{escape(item.get('source', ''))}</span></li>" for item in draft.get("refs", []))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{escape(title)}</title>
<style>
body{{font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;color:#18263b;margin:32px auto;max-width:860px;line-height:1.7}}
h1{{font-size:22px;margin:0 0 4px}} .meta{{color:#68778d;font-size:13px;margin-bottom:20px}}
.notice{{background:#fff7e6;border:1px solid #f0d9a8;color:#7a4d00;padding:10px 12px;border-radius:6px;font-size:13px;margin-bottom:18px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:14px 0}}
th,td{{border:1px solid #dbe7fb;padding:6px 8px;text-align:left}} th{{background:#f5f9ff}}
.src{{color:#9aa1ac;font-size:12px;margin-left:8px}} ul{{padding-left:20px}} li{{margin:4px 0}}
footer{{margin-top:28px;color:#68778d;font-size:12px;border-top:1px solid #eef2f8;padding-top:12px}}
</style></head><body>
<h1>{escape(draft.get('kind_label','底稿草稿'))}</h1>
<div class="meta">主体：{escape(subject_label or '—')} · 科目：{escape(draft['subject']['code'])} {escape(draft['subject']['name'])} · 方向：{escape(draft['subject']['direction'] or '—')}</div>
<div class="notice">本文件为模型辅助生成的草稿，金额取自本机账套，尚未经注册会计师复核，不得作为正式工作底稿归档或对外提供。</div>
<table><tr><th>期初</th><th>借方发生额</th><th>贷方发生额</th><th>期末</th></tr>
<tr><td>{escape(draft['subject']['opening'])}</td><td>{escape(draft['subject']['debit'])}</td><td>{escape(draft['subject']['credit'])}</td><td>{escape(draft['subject']['ending'])}</td></tr></table>
{''.join(paragraphs)}
<h3>引用知识条目</h3><ul>{refs or '<li>本次无命中的内置知识条目</li>'}</ul>
<footer>DeepAudit 生成时间：{escape(draft.get('generated_at', ''))} · 金额来源：本机账套 · 需人工复核</footer>
</body></html>"""


def export(path, title, draft, *, subject_label=""):
    target = Path(path)
    target.write_text(to_html(title, draft, subject_label=subject_label), encoding="utf-8")
    return target
