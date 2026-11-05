"""Read installed blank Dingxinno templates without launching Excel or the audit app.

Example:
  python -B tools/harvest_dxn_templates.py --root E:/person/hydemo/<standard>
      --out local-data/dxn-templates/full.json --catalog-out tb_autofill/assets/dxn-templates.json

The optional --extract-dir keeps decoded vendor originals locally.  The catalog
contains structural labels/formulas only, never workbook numeric or cached values.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import io
from importlib.metadata import version
import json
from pathlib import Path
import platform
import re
import struct
import zlib


MAX_DECODED = 64 * 1024 * 1024
OLE = bytes.fromhex("d0cf11e0a1b11ae1")
FIELD = re.compile(r"(?:科目编号|科目名称|上级科目名称|借贷方向|字符列\d+|"
                   r"(?:账面|未审|审定).*(?:数|额)|账表差异.*调整数|"
                   r"(?:期初|期末).*(?:调整|重分类).*|\d{1,2}月发生额)")
EXTERNAL = re.compile(r"<<Name #(\d+) in external\(\?\) file #(\d+)>>")
CONFIG_NAMES = ("Ver.txt", "b_dgxm.txt", "b_dgxm_ss.txt", "b_dgxm_nk.txt",
                "dg_bycj.txt", "b_fzxm.txt", "b_cjgx.txt", "cjbvsdgxm-dyb.txt",
                "dgxmvsbbxm.txt", "dgcjb.txt", "fzxmlx.txt", "b_sjcx.txt")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_stable(path):
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("来源在读取时发生变化，停止该文件采集")
    return data


def decode_zv(raw, *, max_decoded=MAX_DECODED):
    """Decode the observed ZV length-prefixed zlib blocks; reject partial members."""
    if not raw.startswith(b"ZV\0\0"):
        return raw
    first = None
    for start in range(4, min(len(raw) - 2, 4096)):
        if raw[start] != 0x78 or (raw[start] * 256 + raw[start + 1]) % 31:
            continue
        declared = struct.unpack_from("<I", raw, start - 4)[0]
        if 8 <= declared <= len(raw) - start:
            try:
                candidate = zlib.decompressobj()
                value = candidate.decompress(raw[start:start + declared], max_decoded + 1)
                if candidate.eof and not candidate.unused_data and len(value) <= max_decoded:
                    first = start
                    break
            except zlib.error:
                pass
    if first is None:
        raise ValueError("未找到可验证的 ZV 压缩块，未解包")
    output, total, start = [], 0, first
    while start < len(raw):
        if start < 4:
            raise ValueError("ZV 压缩块长度位置无效")
        declared = struct.unpack_from("<I", raw, start - 4)[0]
        if declared < 8 or start + declared > len(raw):
            raise ValueError("ZV 压缩块被截断，未解包")
        decoder = zlib.decompressobj()
        try:
            block = decoder.decompress(raw[start:start + declared], max_decoded - total + 1)
        except zlib.error as exc:
            raise ValueError("ZV 压缩块校验失败，未解包") from exc
        total += len(block)
        if total > max_decoded:
            raise ValueError("ZV 解包大小超限，未解包")
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("ZV 压缩块不完整，未解包")
        output.append(block)
        end = start + declared
        if end == len(raw):
            break
        if end + 4 >= len(raw):
            raise ValueError("ZV 尾块被截断，未解包")
        start = end + 4
    return b"".join(output)


def _cell(row, column):
    letters = ""
    while column:
        column, rest = divmod(column - 1, 26)
        letters = chr(65 + rest) + letters
    return f"{letters}{row}"


def _records(memory, start, end):
    while start + 4 <= end:
        code, length = struct.unpack_from("<HH", memory, start)
        start += 4
        if start + length > end:
            raise ValueError("BIFF 记录被截断")
        yield code, memory[start:start + length]
        start += length
        if code == 0x0A:
            break


def _external_names(book):
    from xlrd.biffh import unpack_unicode_update_pos
    result, index = {}, -1
    end = min(book._sh_abs_posn) if book._sh_abs_posn else book.base + book.stream_len
    for code, value in _records(book.mem, book.base, end):
        if code == 0x1AE:
            index += 1
            result[index] = []
        elif code == 0x23 and len(value) > 6 and index >= 0:
            name, _ = unpack_unicode_update_pos(value, 6, lenlen=1)
            result[index].append(name)
    return result


def _xls_formulas(book, index, names):
    from xlrd.formula import FMLA_TYPE_CELL, FMLA_TYPE_SHARED, decompile_formula
    formulas, errors = [], []
    start = book._sh_abs_posn[index]
    end = book._sh_abs_posn[index + 1] if index + 1 < len(book._sh_abs_posn) else book.base + book.stream_len
    def external(match):
        name_index, sheet_index = map(int, match.groups())
        try:
            supbook = book._externsheet_info[sheet_index][0]
            return names[supbook][name_index]
        except (IndexError, KeyError):
            return match.group()
    records = list(_records(book.mem, start, end))
    shared = {}
    for code, value in records:
        if code == 0x4BC and len(value) >= 10:
            first_row, _last_row, first_col, _last_col = struct.unpack_from("<HHBB", value)
            length = struct.unpack_from("<H", value, 8)[0]
            shared[first_row, first_col] = value[10:10 + length]
    for code, value in records:
        if code not in {0x06, 0x206, 0x406} or len(value) < 22:
            continue
        row, column = struct.unpack_from("<HH", value)
        length = struct.unpack_from("<H", value, 20)[0]
        tokens = value[22:22 + length]
        address = _cell(row + 1, column + 1)
        try:
            mode, anchor = FMLA_TYPE_CELL, None
            if len(tokens) == 5 and tokens[0] == 0x01:
                anchor = struct.unpack_from("<HH", tokens, 1)
                if anchor not in shared:
                    raise ValueError("未取得共享/数组公式定义")
                tokens, mode = shared[anchor], FMLA_TYPE_SHARED
            formula = decompile_formula(book, tokens, len(tokens), mode, browx=row, bcolx=column)
            if not isinstance(formula, str) or formula.startswith("SHARED FMLA"):
                raise ValueError("共享或数组公式未独立反编译")
            item = {"cell": address, "formula": EXTERNAL.sub(external, formula)}
            if anchor is not None:
                item["shared_from"] = _cell(anchor[0] + 1, anchor[1] + 1)
            formulas.append(item)
        except Exception as exc:
            errors.append({"cell": address, "tokens_sha256": sha256(tokens), "reason": str(exc)[:160]})
    return formulas, errors


def inspect_template(path):
    """Return names, field markers and formulas; omit all numeric/cached contents."""
    import xlrd
    raw = read_stable(path)
    decoded = decode_zv(raw)
    info = {"name": path.stem, "file": path.name, "sha256": sha256(raw),
            "decoded_sha256": sha256(decoded), "sheets": []}
    if decoded.startswith(b"PK"):
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(decoded), read_only=True, data_only=False, keep_links=False)
        try:
            for sheet in book:
                headings, fields, formulas = [], [], []
                for row in sheet:
                    for cell in row:
                        value = cell.value
                        if cell.data_type == "f":
                            formulas.append({"cell": cell.coordinate, "formula": value[1:]})
                        elif isinstance(value, str) and cell.row <= 12 and 0 < len(value.strip()) <= 80:
                            item = {"cell": cell.coordinate, "text": value.strip()}
                            headings.append(item)
                            if FIELD.fullmatch(value.strip()):
                                fields.append(item)
                info["sheets"].append({"name": sheet.title, "rows": sheet.max_row,
                    "columns": sheet.max_column, "headings": headings, "fields": fields,
                    "formulas": formulas, "formula_errors": []})
        finally:
            book.close()
    else:
        book = xlrd.open_workbook(file_contents=decoded, on_demand=True, logfile=io.StringIO())
        try:
            names = _external_names(book)
            info["external_functions"] = sorted({name for values in names.values() for name in values})
            for index, name in enumerate(book.sheet_names()):
                sheet = book.sheet_by_index(index)
                headings, fields = [], []
                for row in range(min(sheet.nrows, 12)):
                    for column in range(sheet.ncols):
                        value = sheet.cell_value(row, column)
                        if not isinstance(value, str) or not 0 < len(value.strip()) <= 80:
                            continue
                        item = {"cell": _cell(row + 1, column + 1), "text": value.strip()}
                        headings.append(item)
                        if FIELD.fullmatch(value.strip()):
                            fields.append(item)
                formulas, errors = _xls_formulas(book, index, names)
                info["sheets"].append({"name": name, "rows": sheet.nrows,
                    "columns": sheet.ncols, "headings": headings, "fields": fields,
                    "formulas": formulas, "formula_errors": errors})
                book.unload_sheet(index)
        finally:
            book.release_resources()
    return info, decoded


def inspect_config(path):
    raw = read_stable(path)
    decoded = decode_zv(raw)
    text = decoded.decode("utf-16") if decoded.startswith((b"\xff\xfe", b"\xfe\xff")) else decoded.decode("gb18030")
    rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
    return {"file": path.name, "sha256": sha256(raw), "decoded_sha256": sha256(decoded), "rows": rows}, decoded


def compact_catalog(report):
    catalog = {key: value for key, value in report.items() if key not in {"templates", "configs"}}
    catalog["configs"] = []
    for config in report["configs"]:
        item = {key: value for key, value in config.items() if key != "rows"}
        if config["file"] == "Ver.txt":
            item["rows"] = config["rows"]
        elif config["file"] == "b_dgxm.txt":
            item["workpapers"] = [{"code": row[3], "name": row[4], "direction": row[6],
                "template": row[8], "parent": row[7]} for row in config["rows"] if len(row) > 8 and row[8]]
        elif config["file"] == "dg_bycj.txt":
            item["optional_sheets"] = [{"code": row[0], "name": row[1], "sheet": row[2]}
                for row in config["rows"] if len(row) > 2]
        elif config["file"] == "b_sjcx.txt":
            item["program_bindings"] = [{"code": row[3], "order": row[4],
                "label": row[29], "index_formula": row[21], "sheets": row[34].split(",")}
                for row in config["rows"] if len(row) > 34 and row[34]]
        catalog["configs"].append(item)
    catalog["templates"] = []
    for template in report["templates"]:
        item = {key: value for key, value in template.items() if key != "sheets"}
        item["sheets"] = []
        for sheet in template["sheets"]:
            summary = {key: value for key, value in sheet.items() if key not in {"formulas", "formula_errors", "headings"}}
            summary["formula_count"] = len(sheet["formulas"]) + len(sheet["formula_errors"])
            summary["formula_error_count"] = len(sheet["formula_errors"])
            examples = []
            for category in ("报表项目借贷", "审定表!", "取月余额"):
                match = next((item for item in sheet["formulas"] if category in item["formula"]), None)
                if match and match not in examples:
                    examples.append(match)
            examples.extend(item for item in sheet["formulas"][:4] if item not in examples)
            summary["formula_examples"] = examples[:6]
            item["sheets"].append(summary)
        catalog["templates"].append(item)
    return catalog


def harvest(root, *, extract_dir=None):
    root = Path(root).resolve()
    report = {"schema_version": 1, "source_root": str(root), "evidence_type": "static installed blank resources",
        "parsers": {"python": platform.python_version(), "xlrd": version("xlrd"), "openpyxl": version("openpyxl")},
        "scope": "all XLS/XLSX in the selected installed accounting-standard template root",
        "templates": [], "configs": [], "failures": [], "limitations": [
            "Static structure does not prove runtime execution or the meaning of database columns.",
            "Shared/array BIFF formulas may remain undecoded; their count and token hashes are retained.",
            "Customer account-to-report bindings are not established by the blank installation catalog."]}
    destination = Path(extract_dir).resolve() if extract_dir else None
    if destination and (destination == root or destination.is_relative_to(root)):
        raise ValueError("解包目录不得位于原安装模板目录内")
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in {".xls", ".xlsx"})
    for path in files:
        relative = path.relative_to(root)
        try:
            info, decoded = inspect_template(path)
            info["file"] = relative.as_posix()
            report["templates"].append(info)
            if destination:
                output = destination / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(decoded)
        except Exception as exc:
            report["failures"].append({"file": relative.as_posix(), "sha256": sha256(read_stable(path)),
                                       "reason": str(exc)[:300]})
    for name in CONFIG_NAMES:
        path = root / name
        if not path.is_file():
            continue
        try:
            config, decoded = inspect_config(path)
            report["configs"].append(config)
            if destination:
                (destination / name).write_bytes(decoded)
        except Exception as exc:
            report["failures"].append({"file": name, "sha256": sha256(read_stable(path)), "reason": str(exc)[:300]})
    sheets = [sheet for item in report["templates"] for sheet in item["sheets"]]
    report["coverage"] = {"template_files": len(files), "parsed_templates": len(report["templates"]),
        "parsed_sheets": len(sheets), "decoded_formulas": sum(len(item["formulas"]) for item in sheets),
        "unresolved_formulas": sum(len(item["formula_errors"]) for item in sheets),
        "failures": len(report["failures"]), "formats": dict(Counter(path.suffix.lower() for path in files))}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--catalog-out")
    parser.add_argument("--extract-dir")
    args = parser.parse_args()
    report = harvest(args.root, extract_dir=args.extract_dir)
    for name, value in ((args.out, report), (args.catalog_out, compact_catalog(report))):
        if name:
            output = Path(name)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(report["coverage"], ensure_ascii=False))


if __name__ == "__main__":
    main()
