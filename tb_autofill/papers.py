"""审计底稿草稿：把本地账套事实与内置知识交给模型，产出的仍是待复核草稿。

金额只来自账套与本地点选结果；模型负责组织审计目标、程序、执行记录与结论，
缺证据时必须写"待补"，不得编造凭证号、抽样数量或审定结论。
"""

from __future__ import annotations

from decimal import Decimal
import re

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

# 底稿程序口径来自审计 skill；检索不到才回落到全库。
AUDIT_SKILL = "china-audit"
LEVEL_ALL = 0
PRIMARY_LEVEL = 1        # 一级科目：审定表默认按此级次出
LEVEL_LABELS = {1: "一级科目", 2: "二级科目"}
JOURNAL_SAMPLES = 8

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


def level_label(level):
    """级次下拉与底稿抬头共用的中文名。"""
    level = int(level or 0)
    return LEVEL_LABELS.get(level, f"{level}级科目" if level else "全部级次")


def level_options(rows):
    """只列出账套里真实存在的级次，避免出现选了必然为空的选项。"""
    options = [(LEVEL_ALL, level_label(LEVEL_ALL))]
    for level in sorted({row.level for row in rows if row.level and row.level > 0}):
        options.append((level, level_label(level)))
    return options


def account_choices(rows, limit=120, level=None):
    """可制作底稿的科目：一级科目排在前面，同级按金额绝对值降序。

    ``level`` 为真时只保留该级次——一级科目做审定表时不必再翻下级明细。
    """
    if not rows:
        return []
    level = int(level or 0)
    parent_paths = {row.path for row in rows if row.path}
    picked = []
    for row in rows:
        if level and row.level != level:
            continue
        has_child = any(path.startswith(f"{row.path}/") for path in parent_paths) if row.path else False
        weight = (abs(row.ending or ZERO) + abs(row.debit or ZERO) + abs(row.credit or ZERO))
        if has_child and weight == ZERO:      # 无金额的汇总科目对底稿没有意义
            continue
        picked.append((weight, row))
    picked.sort(key=lambda item: (item[1].level or 1, -item[0], item[1].code))
    return [(row.code, f"{row.code} {row.name}") for _weight, row in picked[:limit]]


def _search_knowledge(query, limit):
    """先按审计 skill 检索程序口径；无命中再回落到全库，避免被问答库淹没。"""
    documents = [item for item in skills.load_documents() if item.get("skill") == AUDIT_SKILL]
    hits = skills.search(query, limit=limit, documents=documents) if documents else []
    return hits or skills.search(query, limit=limit)


def _strip_unknown_citations(body, refs):
    """删掉模型自造的 [Kn]：注入的编号之外一律不保留，并在正文末尾说明。"""
    allowed = {item["citation_id"] for item in refs}
    invented = sorted({match for match in re.findall(r"\[(K\d+)\]", body) if match not in allowed})
    if not invented:
        return body, []
    for code in invented:
        body = body.replace(f"[{code}]", "")
    return body, [f"已删除模型自造的知识编号：{'、'.join(invented)}"]


def _journal_samples(journal):
    """抽样凭证：金额最大的若干笔 + 期末最后几笔，按凭证号去重后稳定排序。"""
    picked, seen = [], set()

    def take(items):
        for row in items:
            key = (row.month, row.voucher, row.line)
            if key in seen:
                continue
            seen.add(key)
            picked.append(row)

    take(sorted(journal, key=lambda row: (-row.amount, row.month, row.voucher, row.line)))
    take(sorted(journal, key=lambda row: (row.month, row.voucher, row.line), reverse=True))
    return picked[:JOURNAL_SAMPLES]


def _journal_facts(journal, ledger):
    """序时账事实：笔数、月度分布、对方科目与抽样凭证，并与科目发生额做勾稽。"""
    debit = sum((row.debit or ZERO) for row in journal)
    credit = sum((row.credit or ZERO) for row in journal)
    monthly = {}
    for row in journal:
        slot = monthly.setdefault(row.month, {"month": row.month, "count": 0, "debit": ZERO, "credit": ZERO})
        slot["count"] += 1
        slot["debit"] += row.debit or ZERO
        slot["credit"] += row.credit or ZERO
    contra = {}
    for row in journal:
        if not row.contra:
            continue
        side = "贷方" if (row.credit or ZERO) else "借方"
        box = contra.setdefault((row.contra, side), [ZERO, 0])
        box[0] += row.amount
        box[1] += 1
    top_contra = sorted(contra.items(), key=lambda item: (-item[1][0], item[0]))[:10]
    checked = ledger is not None and ledger.debit is not None and ledger.credit is not None
    facts = {
        "source": "鼎信诺序时账（凭证分录）",
        "count": len(journal),
        "debit": _amount(debit),
        "credit": _amount(credit),
        "monthly": [{"month": slot["month"], "count": slot["count"],
                     "debit": _amount(slot["debit"]), "credit": _amount(slot["credit"])}
                    for slot in sorted(monthly.values(), key=lambda slot: slot["month"])],
        "top_contra": [{"name": name, "side": side, "amount": _amount(box[0]), "count": box[1]}
                       for (name, side), box in top_contra],
        "samples": [{"date": row.date, "voucher": row.voucher, "line": row.line,
                     "summary": row.summary, "code": row.code,
                     "debit": _amount(row.debit), "credit": _amount(row.credit),
                     "contra": row.contra} for row in _journal_samples(journal)],
        "note": "序时账只到凭证分录层级；原始凭证、合同、银行回单与实物盘点未包含。",
        "note_contra": "top_contra 按对方科目与借贷方向分别汇总，不要把同一对方科目的借方与贷方相加。",
    }
    if checked:
        facts["tie"] = {
            "checked": True,
            "debit_matches": debit == ledger.debit,
            "credit_matches": credit == ledger.credit,
            "ledger_debit": _amount(ledger.debit),
            "ledger_credit": _amount(ledger.credit),
        }
    else:
        facts["tie"] = {"checked": False, "reason": "科目发生额不完整（期间缺月），未做勾稽"}
    return facts


def build_facts(rows, code, journal=None):
    """单个科目的本地事实：期初、借贷发生额、期末、方向、父级路径与序时账摘要。"""
    target = next((row for row in rows if row.code == code), None)
    if target is None:
        raise ValueError("未找到该科目，请重新读取账套。")
    prefix = f"{target.path}/" if target.path else ""
    children = [row for row in rows if prefix and row.path.startswith(prefix)]
    related = [row for row in rows if row.code != target.code and target.name and target.name in row.name]
    facts = {
        "subject": {"code": target.code, "name": target.name, "path": target.path,
                    "direction": target.direction,
                    "opening": _amount(target.opening), "debit": _amount(target.debit),
                    "credit": _amount(target.credit), "ending": _amount(target.ending),
                    "level": target.level, "level_label": level_label(target.level),
                    "is_profit_loss": target.is_profit_loss},
        "children": [{"code": row.code, "name": row.name, "level": row.level,
                      "opening": _amount(row.opening), "debit": _amount(row.debit),
                      "credit": _amount(row.credit), "ending": _amount(row.ending)}
                     for row in children[:20]],
        "similarly_named": [{"code": row.code, "name": row.name, "ending": _amount(row.ending)}
                            for row in related[:10]],
        "ledger_scope": {"rows": len(rows),
                         "note": "仅本期、本主体、本账套版本；不含凭证、合同、函证与实物证据。"},
    }
    if journal is not None:
        facts["journal"] = _journal_facts(journal, target)
    return facts


def draft(client, *, rows, code, kind=DEFAULT_KIND, subject_label="", focus="", limit=5, journal=None):
    """生成底稿草稿；返回正文与本次引用的知识条目。"""
    kind = kind if kind in KINDS else DEFAULT_KIND
    facts = build_facts(rows, code, journal)
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

    query = " ".join(part for part in (facts["subject"]["name"], facts["focus"]) if part) or KINDS[kind][0]
    hits = _search_knowledge(query, limit)
    refs = skills.as_knowledge_refs(hits, limit=limit)

    context = {"facts": facts, "knowledge_refs": refs,
               "constraints": {"amount_source": "账套" + ("与序时账" if journal else ""),
                               "no_fabrication": True,
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
    body, citation_notes = _strip_unknown_citations(body, refs)
    return {"kind": kind, "kind_label": KINDS[kind][0], "subject": facts["subject"],
            "body": body, "refs": refs,
            "has_journal": journal is not None, "journal": facts.get("journal"),
            "warnings": answer.get("warnings", []) + answer.get("citation_warnings", []) + citation_notes}


# ---------- 附注与报告草稿 ----------

DOCUMENTS = {
    "notes": ("财务报表附注草稿",
              "按已复核 TB 列示重要报表项目、会计政策要点、变动说明与待补披露。"),
    "report": ("审计报告草稿",
               "按固定要素起草意见段与基础段；证据不足时只能写保留或无法表示意见的理由，不得直接出具。"),
}


def _summary_context(rows, results):
    """附注与报告共用的事实摘要：科目规模、匹配情况、差异与缺证据项。"""
    matched = [item for item in results if item.status == "matched"]
    pending = [item for item in results if item.status not in {"matched", "protected"}]
    totals = {"debit": _amount(sum((row.debit or ZERO) for row in rows)),
              "credit": _amount(sum((row.credit or ZERO) for row in rows)),
              "accounts": len(rows),
              "profit_loss_accounts": sum(1 for row in rows if row.is_profit_loss)}
    return {
        "ledger": totals,
        "targets": {"total": len(results), "matched": len(matched), "pending": len(pending),
                    "protected": len(results) - len(matched) - len(pending)},
        "matched_samples": [{"target": item.target.label, "amount": _amount(item.amount),
                             "sources": list(item.source_ids)} for item in matched[:20]],
        "pending_samples": [{"target": item.target.label, "status": item.status,
                             "reason": item.reason} for item in pending[:15]],
        "note": "以上仅为本地账套与匹配结果摘要，不含凭证、合同、函证、盘点与截止测试证据。",
    }


def draft_document(client, *, rows, results, kind="notes", subject_label="", focus="", limit=5):
    """生成附注或报告草稿；结论必须停在“可支持/不支持”，不得声称已签发。"""
    if kind not in DOCUMENTS:
        raise ValueError("不支持的文档类型")
    if not rows:
        raise ValueError("请先读取科目余额。")
    label, guidance = DOCUMENTS[kind]
    facts = _summary_context(rows, results)
    facts.update({"kind": label, "kind_guidance": guidance, "subject_label": subject_label,
                  "focus": str(focus or "")[:500]})
    query = " ".join(part for part in (label, facts["focus"], "披露 认定 审计意见") if part)
    refs = skills.as_knowledge_refs(skills.search(query, limit=limit), limit=limit)
    context = {"facts": facts, "knowledge_refs": refs,
               "constraints": {"amount_source": "账套与本地匹配结果", "no_fabrication": True,
                               "status": "草稿，需注册会计师复核"}}
    question = (f"请起草《{label}》。只依据 facts 与知识条目；缺少证据的段落写“待补”，"
                "并在结尾列出无法支持结论的事项。")
    answer = client.converse_with_skills(question, context=context)
    body = str(answer.get("answer") or "").strip()
    if answer.get("intent") != "audit" or not body:
        raise ValueError(answer.get("answer") or "模型未返回可用草稿。")
    subject = {"code": "—", "name": label, "direction": "", "opening": "", "debit": "", "credit": "",
               "ending": ""}
    return {"kind": kind, "kind_label": label, "subject": subject, "body": body, "refs": refs,
            "warnings": answer.get("warnings", []) + answer.get("citation_warnings", [])}


def _journal_block(draft):
    """导出页里的序时账摘要：只列本地读取值，勾稽结果直接写“一致/不一致”。"""
    journal = draft.get("journal")
    if not journal:
        return ""
    tie = journal.get("tie") or {}
    if tie.get("checked"):
        verdict = "与科目发生额勾稽一致" if tie.get("debit_matches") and tie.get("credit_matches") \
            else "与科目发生额勾稽不一致，需追查"
    else:
        verdict = tie.get("reason") or "未勾稽"
    return (f"<div class='meta'>序时账：{journal['count']} 笔 · 借方 {escape_cell(journal['debit'])}"
            f" / 贷方 {escape_cell(journal['credit'])} · {escape_cell(verdict)}</div>")


def escape_cell(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
    level_text = str((draft.get("subject") or {}).get("level_label") or "")
    level_note = f" · 级次：{escape(level_text)}" if level_text else ""
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
<div class="meta">主体：{escape(subject_label or '—')} · 科目：{escape(draft['subject']['code'])} {escape(draft['subject']['name'])} · 方向：{escape(draft['subject']['direction'] or '—')}{level_note}</div>
<div class="notice">本文件为模型辅助生成的草稿，金额取自本机账套，尚未经注册会计师复核，不得作为正式工作底稿归档或对外提供。</div>
<table><tr><th>期初</th><th>借方发生额</th><th>贷方发生额</th><th>期末</th></tr>
<tr><td>{escape(draft['subject']['opening'])}</td><td>{escape(draft['subject']['debit'])}</td><td>{escape(draft['subject']['credit'])}</td><td>{escape(draft['subject']['ending'])}</td></tr></table>
{_journal_block(draft)}
{''.join(paragraphs)}
<h3>引用知识条目</h3><ul>{refs or '<li>本次无命中的内置知识条目</li>'}</ul>
<footer>DeepAudit 生成时间：{escape(draft.get('generated_at', ''))} · 金额来源：本机账套{'与序时账' if draft.get('has_journal') else ''} · 需人工复核</footer>
</body></html>"""


def export(path, title, draft, *, subject_label=""):
    target = Path(path)
    target.write_text(to_html(title, draft, subject_label=subject_label), encoding="utf-8")
    return target
