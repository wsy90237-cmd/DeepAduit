"""Harvest every installed accounting-standard template root into one coverage inventory.

Example:
  python -B tools/harvest_dxn_library.py --root E:/person/hydemo \
      --out-dir local-data/dxn-library-20261105 --keep-originals

Each standard root is decoded with the same rules as ``harvest_dxn_templates``;
the inventory keeps per-standard coverage, the declared template version, the
originals manifest and failures, so a later reviewer can tell parsed structure
apart from implemented data retrieval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.harvest_dxn_templates import harvest  # noqa: E402

SUFFIXES = {".xls", ".xlsx"}


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def template_files(root):
    return sorted(path for path in Path(root).rglob("*") if path.is_file() and path.suffix.lower() in SUFFIXES)


def declared_version(report):
    """Ver.txt 声明的是模板库版本，与程序版本分别记录。"""
    for config in report.get("configs", []):
        if config.get("file") != "Ver.txt":
            continue
        for row in config.get("rows", []):
            if len(row) >= 3:
                return {"name": row[0], "code": row[1], "version": row[2],
                        "sha256": config.get("sha256", "")}
    return {}


def originals_manifest(directory):
    rows = []
    for path in sorted(Path(directory).rglob("*")):
        if path.is_file():
            rows.append(f"{sha256_file(path)}  {path.relative_to(directory).as_posix()}")
    return rows


def standards(root):
    root = Path(root)
    if not root.is_dir():
        raise SystemExit(f"模板根目录不存在：{root}")
    return sorted(path for path in root.iterdir() if path.is_dir() and template_files(path))


def check_destination(root, out_dir):
    """解包与清单不得写回原安装目录，避免污染供应商原件。"""
    root, out_dir = Path(root).resolve(), Path(out_dir).resolve()
    if out_dir == root or out_dir.is_relative_to(root):
        raise SystemExit("输出目录不得位于原安装模板目录内")
    return root, out_dir


def _reusable(root, candidates):
    """只复用来源根与本标准完全一致、且原件齐全的旧证据。"""
    for candidate in candidates or ():
        path = Path(candidate)
        if not path.is_file():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        originals = path.parent / "originals"
        if Path(report.get("source_root", "")).resolve() == root.resolve() and originals.is_dir():
            return path, report, originals
    return None


def harvest_one(root, out_dir, *, keep_originals, reuse=None):
    root = Path(root)
    slug = root.name
    target = Path(out_dir) / slug
    target.mkdir(parents=True, exist_ok=True)
    evidence = target / "full.json"
    originals = target / "originals"
    reused = False
    matched = None if evidence.exists() or originals.exists() else _reusable(root, reuse)
    if matched:
        source, report, originals_src = matched
        evidence.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        _copy_tree(originals_src, originals)
        reused = True
    else:
        report = harvest(root, extract_dir=originals if keep_originals else None)
        evidence.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n",
                            encoding="utf-8")
    entry = {"standard": slug, "source_root": str(root.resolve()),
             "declared": declared_version(report), "coverage": report.get("coverage", {}),
             "failures": len(report.get("failures", [])), "reused_evidence": reused,
             "evidence": evidence.relative_to(Path(out_dir)).as_posix()}
    if originals.is_dir():
        rows = originals_manifest(originals)
        (target / "ORIGINALS.sha256").write_text("\n".join(rows) + "\n", encoding="utf-8")
        entry.update(originals={"files": len(rows), "manifest": f"{slug}/ORIGINALS.sha256",
                                "manifest_sha256": sha256_file(target / "ORIGINALS.sha256")})
    return entry


def _copy_tree(source, destination):
    import shutil
    shutil.copytree(source, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="hydemo 安装模板根目录")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--keep-originals", action="store_true")
    parser.add_argument("--reuse", action="append", default=[],
                        help="已有 full.json；其 source_root 与某个标准一致时复用该证据")
    parser.add_argument("--only", action="append", default=[], help="只处理指定标准目录名")
    args = parser.parse_args()
    root, out_dir = check_destination(args.root, args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pending = [path for path in standards(root) if not args.only or path.name in args.only]
    inventory = {"root": str(root), "generated_by": "tools/harvest_dxn_library.py",
                 "scope": "所有安装标准目录内的 XLS/XLSX 空白模板；只做静态结构解析",
                 "standards": []}
    for index, path in enumerate(pending, 1):
        print(f"[{index}/{len(pending)}] {path.name}", flush=True)
        entry = harvest_one(path, out_dir, keep_originals=args.keep_originals, reuse=args.reuse)
        inventory["standards"].append(entry)
        (out_dir / "coverage.json").write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(entry["coverage"], ensure_ascii=False), flush=True)
    totals = {key: sum(item["coverage"].get(key, 0) for item in inventory["standards"])
              for key in ("template_files", "parsed_templates", "parsed_sheets",
                          "decoded_formulas", "unresolved_formulas", "failures")}
    inventory["totals"] = totals
    (out_dir / "coverage.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    print(json.dumps(totals, ensure_ascii=False))


if __name__ == "__main__":
    main()
