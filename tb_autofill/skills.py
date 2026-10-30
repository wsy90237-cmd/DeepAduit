"""内置会计知识 skill：随程序分发的本地检索，不联网、不依赖第三方库。

两个 skill 的正文本体存放在 ``tb_autofill/assets/skills/``（随 ``--include-data-dir``
打进发布包）。本模块只做三件事：拆条目、确定性检索、转成 AI 层认识的
``knowledge_refs``。所有打分都是纯计算，同一输入必得同一顺序，不用随机数与时间。
"""

import gzip
import hashlib
import json
import math
from pathlib import Path
import re

ASSETS = Path(__file__).resolve().parent / "assets" / "skills"

MAX_BODY = 1200          # 单条正文上限，避免撑爆上下文
MAX_ENTRIES = 4000       # 条目总数上限，防止资产异常膨胀
MAX_FILE_BYTES = 4 * 1024 * 1024
MIN_SCORE = 2.0          # 绝对分下限：低于此分视为未命中
MIN_RATIO = 0.15         # 相对分下限：低于首条 15% 的视为噪声
MIN_COVERAGE = 0.5       # 查询词权重覆盖率下限：低于一半即算未命中
SKILL_LABELS = {
    "chenyiwei-accounting": "陈版主会计判断",
    "ipo-financial-review": "IPO 财务核查",
}
# 纯导航小节对检索只有干扰，拆条目时跳过。
NAVIGATION = {"章节目录", "目录", "contents", "index", "toc", "table of contents"}
# 提问套话与高频通用词：既不进检索词，也不进覆盖率分母，避免“怎么/处理”这类
# 二字词把无关条目拱上来，也避免长问句被它们稀释。
FILLER = {
    "怎么", "么办", "么样", "怎样", "如何", "何办", "什么", "为何", "是否", "哪些", "哪个",
    "可以", "应该", "需要", "请问", "请教", "问题", "处理", "如何", "这个", "那个", "我们",
    "他们", "以及", "或者", "但是", "因为", "所以", "如果", "还是", "就是", "没有", "有没",
    "一下", "的话", "时候", "情况", "进行", "相关", "关于", "对于", "根据", "按照", "麻烦",
}

_HEADING = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)[ \t]*$")
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[a-z0-9_]{2,}")
_FRONT_MATTER = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.S)
_TIMESTAMP = re.compile(r"时间：\s*\d{4}-\d{2}-\d{2}[^\n]{0,12}")

_CACHE = {}


def _clip(text, limit=MAX_BODY):
    text = _TIMESTAMP.sub("", str(text or ""))
    text = re.sub(r"[ \t]+\n", "\n", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"    # 省略号告诉模型：这是节选，不是全文


def _clean_heading(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().strip("#").strip()


def _entry_id(skill, path, ordinal):
    raw = f"{skill}|{path}|{ordinal}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _paragraphs(body):
    return [block.strip() for block in re.split(r"\n[ \t]*\n", body) if block.strip()]


def _split_long(heading, body, limit=MAX_BODY):
    """把过长小节按段落切成 ≤limit 的若干块，保证正文不丢失。"""
    pieces, buffer = [], ""
    for block in _paragraphs(body):
        while len(block) > limit:
            if buffer:
                pieces.append(buffer)
                buffer = ""
            pieces.append(block[:limit])
            block = block[limit:]
        if not block:
            continue
        candidate = f"{buffer}\n\n{block}" if buffer else block
        if len(candidate) <= limit:
            buffer = candidate
        else:
            pieces.append(buffer)
            buffer = block
    if buffer:
        pieces.append(buffer)
    if not pieces:
        return []
    if len(pieces) == 1:
        return [(heading, _clip(pieces[0], limit))]
    found = [(heading, _clip(pieces[0], limit))]
    for index, piece in enumerate(pieces[1:], start=2):
        found.append((f"{heading}（续{index}）" if heading else f"（续{index}）", _clip(piece, limit)))
    return found


def _section_entries(skill, path, text):
    text = _FRONT_MATTER.sub("", text, count=1)
    heads = list(_HEADING.finditer(text))
    found = []
    preamble = text[:heads[0].start()] if heads else text
    if preamble.strip() and len(preamble.strip()) >= 60:
        found.extend(_split_long("", preamble))
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        heading = _clean_heading(match.group(2))
        if heading.lower() in NAVIGATION:
            continue
        body = text[match.end():end]
        if len(body.strip()) < 40:
            continue
        found.extend(_split_long(heading, body))
    return found


def _jsonl_entries(path, raw):
    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        title = _clean_heading(record.get("title") or "")
        question = str(record.get("question") or "").strip()
        answer = str(record.get("answer") or "").strip()
        body = "\n".join(part for part in (f"问：{question}" if question else "",
                                           f"答：{answer}" if answer else "") if part)
        if len(body) < 60:
            continue
        tag = " · ".join(str(record.get(key)) for key in ("src", "year", "no") if record.get(key))
        entries.append({"heading": title or tag or path, "text": _clip(body),
                        "kind": "qa", "tags": tag})
    return entries


def _read_text(path):
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rb") as stream:
            return stream.read(MAX_FILE_BYTES * 4).decode("utf-8", "replace")
    return path.read_bytes().decode("utf-8-sig", "replace")


def _scan_base(base):
    base = Path(base)
    if not base.is_dir():
        return []
    documents = []
    for path in sorted(base.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        if path.suffix.lower() not in {".md", ".jsonl", ".gz", ".txt"}:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        skill = path.relative_to(base).parts[0]
        rel = path.relative_to(base).as_posix()
        documents.append((skill, rel, path))
    return documents


def load_documents(base=None, *, limit=MAX_ENTRIES):
    """扫描 assets/skills 下所有文本，拆成结构化条目（结果按 id 稳定排序）。

    返回条目：``id`` / ``skill`` / ``skill_label`` / ``path`` / ``heading`` / ``title``
    / ``text`` / ``kind``。目录缺失或为空时返回空列表，不抛异常。
    """
    base = Path(base) if base else ASSETS
    key = (str(base.resolve()), int(limit))
    if key in _CACHE:
        return _CACHE[key]
    entries = []
    for skill, rel, path in _scan_base(base):
        try:
            text = _read_text(path)
        except (OSError, EOFError, UnicodeError):
            continue
        if path.suffix.lower() in {".jsonl", ".gz"}:
            found = _jsonl_entries(rel, text)
        else:
            found = [{"heading": heading, "text": body, "kind": "doc", "tags": ""}
                     for heading, body in _section_entries(skill, rel, text)]
        for ordinal, item in enumerate(found):
            body = item["text"]
            if not body:
                continue
            entries.append({
                "id": _entry_id(skill, rel, ordinal),
                "skill": skill,
                "skill_label": SKILL_LABELS.get(skill, skill),
                "path": rel,
                "heading": item["heading"],
                "title": (item["heading"] or Path(rel).stem)[:200],
                "text": body,
                "kind": item["kind"],
                "tags": item.get("tags", ""),
            })
            if len(entries) >= limit:
                break
        if len(entries) >= limit:
            break
    entries.sort(key=lambda item: item["id"])
    _CACHE[key] = entries
    return entries


def _query_terms(query):
    """中文按 2-gram 切分，整词与英文单词加权；同一查询必得同一词表。

    返回 ``[(term, weight, is_gram)]``：``is_gram`` 为真表示参与覆盖率计算的基本检索词，
    整词与英文单词只加分不参与覆盖率（整句几乎不可能原文出现，参与反而稀释分母）。
    """
    text = str(query or "").strip().lower()
    weights = {}
    for run in _CJK.findall(text):
        if len(run) >= 2:
            if run not in FILLER:
                weights[run] = max(weights.get(run, 0.0), 3.0)
            for index in range(len(run) - 1):
                gram = run[index:index + 2]
                if gram in FILLER:
                    continue
                weights[gram] = max(weights.get(gram, 0.0), 1.0)
    for word in _WORD.findall(text):
        weights[word] = max(weights.get(word, 0.0), 3.0)
    if not weights:
        return []
    grams = [term for term, weight in weights.items() if weight <= 1.0]
    return [(term, weights[term], term in grams) for term in sorted(weights)]


def search(query, limit=5, *, documents=None, base=None, min_score=MIN_SCORE,
           min_ratio=MIN_RATIO, min_coverage=MIN_COVERAGE):
    """本地确定性检索：先整句检索，未命中时退到核心词，避免长句被覆盖率闸门稀释成空。

    三道闸门挡掉“只撞上一个常用二字词”的假命中：查询词覆盖率、绝对分、相对分。
    未命中返回空列表（不抛异常）；同分按 id 升序，保证同一输入顺序一致。
    """
    terms = _query_terms(query)
    if not terms:
        return []
    hits = _rank(terms, limit, documents, base, min_score, min_ratio, min_coverage)
    if hits or len(terms) <= 6:
        return hits
    # 长句兜底：按分句分别检索再合并，避免整句覆盖率把核心术语稀释掉。
    merged = {}
    for clause in re.split(r"[，。；！？,.;!?\s]+", str(query or "")):
        clause = clause.strip()
        if len(clause) < 3:
            continue
        clause_terms = _query_terms(clause)
        if not clause_terms:
            continue
        for item in _rank(clause_terms, limit, documents, base, min_score, min_ratio,
                          max(0.0, min_coverage * 0.5)):
            best = merged.get(item["id"])
            if best is None or item["score"] > best["score"]:
                merged[item["id"]] = item
    return sorted(merged.values(), key=lambda item: (-item["score"], item["id"]))[:limit]


def _rank(terms, limit, documents, base, min_score, min_ratio, min_coverage):
    weights = {term: weight for term, weight, _ in terms}
    has_grams = any(is_gram for _, _, is_gram in terms)
    gram_total = sum(weight for _, weight, is_gram in terms if is_gram) or sum(weights.values())
    limit = max(1, min(int(limit), 20))
    entries = documents if documents is not None else load_documents(base)
    if not entries:
        return []
    prepared = []
    frequency = {term: 0 for term in weights}
    for entry in entries:
        title = entry["title"].lower()
        haystack = (entry["title"] + "\n" + entry.get("tags", "") + "\n" + entry["text"]).lower()
        contributions = {}
        matched_grams = 0
        for term, weight, is_gram in terms:
            hits = haystack.count(term)
            if not hits:
                continue
            frequency[term] += 1
            if is_gram or not has_grams:
                matched_grams += weight
            payload = weight * (1.0 + math.log(1.0 + hits))
            if term in title:
                payload += weight * 2.5
            contributions[term] = payload
        if not contributions:
            continue
        if matched_grams / gram_total < min_coverage:
            continue
        prepared.append((entry, contributions))
    if not prepared:
        return []
    total = len(entries)
    idf = {term: math.log((total + 1.0) / (frequency[term] + 1.0)) + 1.0 for term in weights}
    scored = []
    for entry, contributions in prepared:
        score = round(sum(value * idf[term] for term, value in contributions.items()), 6)
        if score >= min_score:
            scored.append((score, entry))
    if not scored:
        return []
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    floor = max(min_score, scored[0][0] * min_ratio)
    results, seen = [], set()
    for score, entry in scored:
        if score < floor:
            break
        fingerprint = entry["text"][:120]
        if fingerprint in seen:      # checks.md 与 digest 有重复正文，只留先出现的一条
            continue
        seen.add(fingerprint)
        results.append({**entry, "score": score})
        if len(results) >= limit:
            break
    return results


def as_knowledge_refs(entries, limit=5, start=1):
    """转成 AI 层认识的 knowledge_refs：citation_id 从 start 起连续编号。"""
    refs = []
    for index, entry in enumerate(list(entries)[:max(1, int(limit))]):
        excerpt = _clip(entry.get("text"), MAX_BODY)
        if not excerpt:
            continue
        refs.append({
            "citation_id": f"K{int(start) + index}",
            "title": str(entry.get("title") or "")[:200],
            "source": ("内置知识库/" + str(entry.get("skill_label") or entry.get("skill") or ""))[:30],
            "locator": (f"{entry.get('path')} · {entry.get('heading')}")[:700],
            "excerpt": excerpt,
        })
    return refs


def citation_ceiling(refs):
    """已注入编号的最大序号，供追加 skill 条目时接着编号，避免撞号。"""
    highest = 0
    for item in refs or []:
        match = re.fullmatch(r"K(\d+)", str(item.get("citation_id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return highest


def manifest(base=None):
    """读取资产清单；缺失时返回空字典，供界面与自检显示来源与体积。"""
    path = (Path(base) if base else ASSETS) / "manifest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def summary(base=None):
    """条目统计：总数与按 skill 分布，供界面显示与自检使用。"""
    entries = load_documents(base)
    counts = {}
    for entry in entries:
        counts[entry["skill"]] = counts.get(entry["skill"], 0) + 1
    return {"entries": len(entries), "skills": dict(sorted(counts.items()))}
