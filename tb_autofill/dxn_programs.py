"""审计程序库：以本机鼎信诺底稿抽出的程序为主，分类模板与通用尾部补齐。

程序正文与认定来自鼎信诺各科目底稿的「审计程序」表（见 tools/harvest_dxn_programs.py），
不在此处改写措辞；补充部分来自内置分类模板，两者按正文去重后合并。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ASSET = Path(__file__).resolve().parent / "assets" / "dxn-programs.json"
MAX_PROGRAMS = 60          # 单科目程序上限：过长的程序表反而没人看


@lru_cache(maxsize=1)
def _payload():
    if not ASSET.exists():
        return {}
    try:
        data = json.loads(ASSET.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = data.get("items")
    return items if isinstance(items, dict) else {}


@lru_cache(maxsize=1)
def _by_index():
    """索引号 → 条目；同一索引号取程序最多的一份。"""
    result = {}
    for item in _payload().values():
        index_no = str(item.get("index_no") or "").strip()
        programs = item.get("programs") or []
        if not index_no or not programs:
            continue
        if index_no not in result or len(programs) > len(result[index_no]["programs"]):
            result[index_no] = item
    return result


def available():
    """已收录的科目索引号。"""
    return sorted(_by_index())


def source_label(index_no):
    item = _by_index().get(str(index_no or "").strip())
    return str(item.get("source", "")) if item else ""


def library(index_no):
    """返回该科目的鼎信诺程序（dict 列表），没有则返回空列表。"""
    item = _by_index().get(str(index_no or "").strip())
    if not item:
        return []
    return [{"text": str(entry.get("text", "")).strip(),
             "assertions": [str(x) for x in entry.get("assertions") or []],
             "section": str(entry.get("section", "")).strip()}
            for entry in item.get("programs") or [] if str(entry.get("text", "")).strip()]


def _norm(text):
    """去重键：去掉标点与空白，取前 32 个字。"""
    return re.sub(r"[\s\W_]+", "", str(text or ""))[:32]


def merge(base, supplements=(), tail=(), limit=MAX_PROGRAMS):
    """合并程序：鼎信诺库优先，其后分类补充、通用尾部；按正文去重。"""
    seen, merged = set(), []
    for group in (base, supplements, tail):
        for item in group:
            key = _norm(item.get("text"))
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(dict(item))
            if len(merged) >= limit:
                return merged
    return merged


def stats():
    items = _payload()
    total = sum(len(item.get("programs") or []) for item in items.values())
    return {"papers": len(items), "programs": total,
            "average": round(total / len(items), 1) if items else 0}
