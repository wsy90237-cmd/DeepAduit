"""从本机鼎信诺底稿中抽取审计程序清单，生成内置程序库。

用法：python tools/harvest_dxn_programs.py --root <已获授权的底稿目录> [--out ...]

做法：按文件名分组（如 "ZA 货币资金"），每组取最新的一份底稿，读取其中的「审计程序」表：
C 列为 0 的是分节标题，为 1 的是具体程序，G–K 列的 √ 表示对应认定。
输出 JSON 以《审计业务索引号及底稿名称清单》的「索引号 底稿名称」为键，未匹配的按鼎信诺原名保留。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tb_autofill import audit_index  # noqa: E402

ASSERTIONS = ((7, "存在"), (8, "完整性"), (9, "权利和义务"), (10, "计价和分摊"), (11, "列报"))
SECTION_MARK = "0"
SKIP_PREFIXES = ("财务报表底稿", "科目余额表", "试算平衡表")


def _text(value):
    return "" if value is None else str(value).strip()


def _cell(reader, row, column):
    try:
        return reader(row, column)
    except (IndexError, KeyError):
        return ""


def _parse_rows(rows):
    """rows 为 (行号 → 列值) 的可调用对象，返回 (分节, 程序)。"""
    header_row = None
    for row in range(1, 60):
        if _text(rows(row, 4)) == "审计程序内容":
            header_row = row
            break
    if header_row is None:
        return None
    sections, programs = [], []
    current = ""
    for row in range(header_row + 1, 400):
        text = _text(rows(row, 4))
        if not text:
            continue
        flag = _text(rows(row, 3))
        # 有些模板的分节行不带标记，按「(一)xx」这类写法兜底识别
        if flag == SECTION_MARK or re.match(r"^[（(][一二三四五六七八九十]+[)）]", text):
            current = text
            sections.append(text)
            continue
        assertions = [label for column, label in ASSERTIONS
                      if _text(rows(row, column)) in {"√", "v", "V", "✓"}]
        programs.append({
            "text": re.sub(r"\s+", " ", text),
            "index": _text(rows(row, 5)),
            "assertions": assertions,
            "section": current,
        })
    if not programs:
        return None
    # 同名程序只保留首次出现，避免跨分节重复
    seen, unique = set(), []
    for item in programs:
        key = item["text"][:60]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return {"sections": sections, "programs": unique}


def _read_xlsx(path):
    from openpyxl import load_workbook
    book = load_workbook(path, read_only=True, data_only=True)
    if "审计程序" not in book.sheetnames:
        return None
    sheet = book["审计程序"]
    rows = [[cell for cell in row] for row in sheet.iter_rows(min_row=1, max_row=400, max_col=12)]
    book.close()

    def reader(row, column):
        return rows[row - 1][column - 1].value if row - 1 < len(rows) else ""

    return _parse_rows(reader)


def _read_xls(path):
    import xlrd
    book = xlrd.open_workbook(str(path), on_demand=True)
    try:
        if "审计程序" not in book.sheet_names():
            return None
        sheet = book.sheet_by_name("审计程序")

        def reader(row, column):
            return sheet.cell_value(row - 1, column - 1) if row - 1 < sheet.nrows else ""

        return _parse_rows(reader)
    finally:
        book.release_resources()


def _norm(name):
    text = re.sub(r"^[A-Za-z][A-Za-z\-]*\s*", "", name).strip()
    text = re.sub(r"[（(].*?[)）]", "", text)
    return re.sub(r"[\s　]+", "", text)


def _map_to_papers(papers):
    """鼎信诺底稿名 → 清单索引号，先精确、再去括号、最后互为包含。"""
    exact = {}
    for paper in papers:
        exact.setdefault(_norm(paper.name), paper)

    def resolve(name):
        key = _norm(name)
        if key in exact:
            return exact[key]
        for candidate, paper in exact.items():
            if candidate and (candidate in key or key in candidate):
                return paper
        return None

    return resolve


def harvest(root, out):
    papers = audit_index.bundled()
    resolve = _map_to_papers(papers)
    groups = {}
    for path in root.rglob("*"):
        if path.suffix.lower() not in {".xls", ".xlsx"} or not path.is_file():
            continue
        if path.stem.startswith(SKIP_PREFIXES) or path.name.startswith("~$"):
            continue
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        group = groups.setdefault(path.stem, {"path": path, "stamp": stamp, "size": path.stat().st_size})
        if (stamp, path.stat().st_size) > (group["stamp"], group["size"]):
            groups[path.stem] = {"path": path, "stamp": stamp, "size": path.stat().st_size}

    result, stats = {}, []
    for stem, info in sorted(groups.items()):
        path = info["path"]
        try:
            parsed = _read_xls(path) if path.suffix.lower() == ".xls" else _read_xlsx(path)
        except Exception as exc:                       # 单份文件损坏不影响整体
            stats.append((stem, 0, f"读取失败：{exc}"))
            continue
        if not parsed:
            continue
        paper = resolve(stem)
        key = paper.label if paper else stem
        if key in result and len(result[key]["programs"]) >= len(parsed["programs"]):
            continue
        parsed.update({"source": stem, "index_no": paper.index_no if paper else "",
                       "name": paper.name if paper else _norm(stem)})
        result[key] = parsed
        stats.append((stem, len(parsed["programs"]), key))

    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_from": str(root),
        "count": len(result),
        "items": {key: value for key, value in sorted(result.items())},
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    for stem, count, key in sorted(stats, key=lambda item: -item[1])[:25]:
        print(f"{count:>4}  {stem:<28} -> {key}")
    total = sum(len(item["programs"]) for item in result.values())
    print(f"\n科目 {len(result)} 个，程序 {total} 条，平均 {total / max(1, len(result)):.1f} 条/科目")
    print(f"写入 {out}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="明确指定已获授权的底稿目录，不自动探测客户文件")
    parser.add_argument("--out", default=str(ROOT / "tb_autofill" / "assets" / "dxn-programs.json"))
    args = parser.parse_args()
    harvest(Path(args.root), Path(args.out))


if __name__ == "__main__":
    main()
