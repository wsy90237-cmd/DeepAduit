"""凭证抽样：从序时账里挑最值得查的 10 笔。

两条路：
1. 接了模型 —— 把候选分录交给模型，由它按风险挑 10 笔并写理由；
2. 没接模型 —— 用固定规则打分（金额、整数金额、期末月份、异常摘要、涉及货币资金或往来），
   结果可复现，理由写清楚依据的是哪条规则。
两种方式都只挑不判，检查结论一律留空待现场核对。
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

SAMPLE_SIZE = 10
RISK_WORDS = ("调整", "冲销", "红字", "结转", "暂估", "往来", "借款", "其他", "重分类", "更正")
MONEY_WORDS = ("银行", "现金", "库存现金")
RELATED_WORDS = ("应收", "应付", "预付", "预收", "其他应收", "其他应付")


def _amount(row):
    value = getattr(row, "amount", None)
    if value is None:
        value = (getattr(row, "debit", None) or Decimal("0")) + (getattr(row, "credit", None) or Decimal("0"))
    return abs(value or Decimal("0"))


def reasons(row, *, amount_rank=None, total=0):
    """按风险特征给出抽取理由；可复现，不依赖随机数。"""
    found = []
    amount = _amount(row)
    text = str(getattr(row, "summary", "") or "")
    contra = str(getattr(row, "contra", "") or "")
    if amount_rank is not None and amount_rank < 3:
        found.append("金额较大")
    if amount and amount % 10000 == 0:
        found.append("整数金额")
    if any(word in text for word in RISK_WORDS):
        found.append("摘要含" + next(word for word in RISK_WORDS if word in text))
    if any(word in contra for word in MONEY_WORDS):
        found.append("涉及货币资金")
    elif any(word in contra for word in RELATED_WORDS):
        found.append("涉及往来")
    month = getattr(row, "month", 0) or 0
    if month in (12, 1):
        found.append(f"{month} 月发生（期末/期初）")
    return "、".join(found)


def _score(row, amount_rank, total):
    amount = _amount(row)
    score = Decimal("0")
    if total:
        score += (amount / total) * 100
    if amount and amount % 10000 == 0:
        score += 12
    if any(word in str(getattr(row, "summary", "") or "") for word in RISK_WORDS):
        score += 15
    if any(word in str(getattr(row, "contra", "") or "") for word in MONEY_WORDS):
        score += 8
    if (getattr(row, "month", 0) or 0) in (12, 1):
        score += 10
    if amount_rank < 3:
        score += 20 - amount_rank * 5
    return score


def pick(journal, limit=SAMPLE_SIZE):
    """规则抽样：返回 [(JournalRow, 抽取理由)]，按得分降序，最多 limit 笔。"""
    rows = [row for row in journal or [] if _amount(row) > 0]
    if not rows:
        return []
    ordered = sorted(rows, key=_amount, reverse=True)
    ranks = {id(row): index for index, row in enumerate(ordered)}
    total = sum((_amount(row) for row in rows), Decimal("0"))
    scored = sorted(rows, key=lambda row: (_score(row, ranks[id(row)], total), _amount(row)),
                    reverse=True)
    chosen, seen = [], set()
    for row in scored:
        key = (str(getattr(row, "date", "")), str(getattr(row, "voucher", "")),
               str(getattr(row, "line", "")))
        if key in seen:
            continue
        seen.add(key)
        chosen.append((row, reasons(row, amount_rank=ranks[id(row)], total=total)))
        if len(chosen) >= limit:
            break
    return chosen


def candidates(journal, limit=40):
    """给模型的候选清单：金额大的前 limit 笔，编号从 1 开始。"""
    rows = sorted([row for row in journal or [] if _amount(row) > 0],
                  key=_amount, reverse=True)[:limit]
    return [{"no": index, "date": str(getattr(row, "date", "")),
             "voucher": str(getattr(row, "voucher", "")),
             "summary": str(getattr(row, "summary", "")),
             "contra": str(getattr(row, "contra", "")),
             "amount": float(_amount(row)), "month": getattr(row, "month", 0)}
            for index, row in enumerate(rows, 1)], rows


def pick_with_ai(client, subject_name, journal, limit=SAMPLE_SIZE):
    """让模型挑样本；模型不可用或返回不合法时回落到规则抽样。"""
    pool, rows = candidates(journal)
    if not pool or client is None:
        return pick(journal, limit), "规则抽样"
    question = (
        f"以下是「{subject_name}」本期序时账中金额最大的 {len(pool)} 笔分录。"
        f"请从风险角度挑出最应该抽查的 {limit} 笔（考虑大额、整数金额、期末月份、"
        "调整/冲销/暂估等异常摘要、涉及货币资金或关联往来），只输出 JSON："
        '{"picks":[{"no":1,"reason":"为什么挑它"}]}。不要编造不存在的编号。'
    )
    try:
        answer = client.converse_with_skills(question, context={"candidates": pool,
                                                                "constraints": {"output": "JSON"}})
        if answer.get("intent") != "audit":
            raise ValueError("模型未按审计任务回答")
        raw = str(answer.get("answer") or "")
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型未返回 JSON")
        data = json.loads(raw[start:end + 1])
        picks = data.get("picks") or []
        chosen, seen = [], set()
        for item in picks:
            try:
                number = int(item.get("no"))
            except (TypeError, ValueError):
                continue
            if not 1 <= number <= len(rows) or number in seen:
                continue
            seen.add(number)
            reason = re.sub(r"\s+", " ", str(item.get("reason") or "")).strip()[:80]
            chosen.append((rows[number - 1], f"模型判断：{reason or '风险较高'}"))
            if len(chosen) >= limit:
                break
        if not chosen:
            raise ValueError("模型未给出有效编号")
        return chosen, "模型挑选"
    except Exception:                       # 网络、格式、拒答都回落，不阻断出表
        return pick(journal, limit), "规则抽样（模型不可用）"
