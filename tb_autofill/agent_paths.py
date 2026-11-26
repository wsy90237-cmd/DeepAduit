"""Resolve paths supplied in the desktop conversation without involving the model."""

from pathlib import Path
import re
import stat

_ROOT = r"(?:(?<![A-Za-z0-9_])[A-Za-z]:[\\/]|\\\\)"
_PATH = re.compile(
    rf'"(?P<quoted>{_ROOT}[^"\r\n]+)"|“(?P<cn>{_ROOT}[^”\r\n]+)”|'
    rf"(?P<bare>{_ROOT}[^\r\n\"“”，。；,;]*?\.(?:xlsx|xlsm)(?![a-z0-9.])|"
    rf"{_ROOT}[^\r\n\"“”，。；,;]+)", re.I)
_OUTPUT = re.compile(r"(?:结果|产物|副本)?\s*(?:另存到|保存到|导出到|输出到|存到|存回|保存位置)\s*[：:]?\s*$")
_WORKBOOKS = {".xlsx", ".xlsm"}


def local_paths(objective):
    """Return the path-free objective, proposed template source, and output location."""
    source, output = [], []
    position = 0
    for match in _PATH.finditer(objective):
        raw = next(value for value in match.groups() if value is not None).strip()
        destination = output if _OUTPUT.search(objective[position:match.start()]) else source
        destination.append(Path(raw))
        position = match.end()
    if len(set(source)) > 1 or len(set(output)) > 1:
        raise ValueError("识别到多个本地路径。请给出一个模板文件或目录，另一个路径用「保存到」注明。")
    cleaned = without_paths(objective)
    return cleaned, next(iter(source), None), next(iter(output), None)


def without_paths(text):
    return _PATH.sub("〔本地路径〕", text)


def template_candidates(path):
    path = path.absolute()
    mode = path.stat().st_mode
    if stat.S_ISREG(mode) and path.suffix.lower() in _WORKBOOKS:
        return (path,)
    if not stat.S_ISDIR(mode):
        raise ValueError("模板须为 .xlsx 或 .xlsm 文件，也可以给出包含模板的目录。")
    candidates = []
    for item in path.iterdir():
        if (item.suffix.lower() not in _WORKBOOKS or item.name.startswith(("~$", "."))
                or "-Attesta副本" in item.stem or item.is_symlink() or not item.is_file()):
            continue
        candidates.append(item)
    if not candidates:
        raise ValueError("指定目录中没有找到可用的 TB 模板。请发模板文件或其所在目录。")
    return tuple(sorted(set(candidates), key=lambda item: item.name.casefold()))
