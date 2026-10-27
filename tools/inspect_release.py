"""Read-only PE and legacy bytecode-container checks; no target execution."""

import argparse
import hashlib
import json
from pathlib import Path
import struct


def inspect(path):
    data = Path(path).read_bytes()
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError("不是有效的 Windows PE 文件")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if pe + 24 + 152 > len(data) or data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("PE 头缺失或被截断")
    machine = struct.unpack_from("<H", data, pe + 4)[0]
    optional = pe + 24
    if machine != 0x8664 or struct.unpack_from("<H", data, optional)[0] != 0x20B:
        raise ValueError("客户发布包必须为 Windows x64 PE32+")
    flags = struct.unpack_from("<H", data, optional + 70)[0]
    cert_offset, cert_size = struct.unpack_from("<II", data, optional + 112 + 4 * 8)
    result = {"sha256": hashlib.sha256(data).hexdigest(), "architecture": "AMD64",
              "aslr": bool(flags & 0x40), "dep": bool(flags & 0x100),
              "high_entropy_aslr": bool(flags & 0x20),
              "authenticode_table_present": bool(cert_offset and cert_size),
              "pyinstaller_archive_cookie": b"MEI\x0c\x0b\x0a\x0b\x0e" in data}
    if result["pyinstaller_archive_cookie"]:
        raise ValueError("拒绝发布：检测到旧 PyInstaller 字节码归档")
    if not result["aslr"] or not result["dep"]:
        raise ValueError("拒绝发布：PE 未同时启用 ASLR 和 DEP")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(args.path), ensure_ascii=False, indent=2))
