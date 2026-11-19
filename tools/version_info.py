"""把品牌版本资源写成 PyInstaller 可读的文件。

用法：
    .venv\\Scripts\\python.exe tools/version_info.py build/version_info.txt

内容全部来自 `tb_autofill.brand`，不要在这里另写产品名、公司或版权。
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tb_autofill import brand  # noqa: E402


def write(path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(brand.version_info_text(), encoding="utf-8")
    return target


if __name__ == "__main__":
    default = ROOT / "build" / "version_info.txt"
    print(write(sys.argv[1] if len(sys.argv) > 1 else default))
