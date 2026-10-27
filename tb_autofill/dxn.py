"""鼎信诺 7100 科目余额读取；原库始终只读，数据库引擎仅打开临时副本。"""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from decimal import Decimal

from .models import LedgerRow, Subject, money

_DRAFT = "财务报表底稿.xls"
_ITEM_ID = re.compile(r"^I\d+D(\d+)N\d+Z", re.I)
_TABLES = ("c_kmyek", "c_cwkmk")


def _service_dir(path: str | Path) -> Path | None:
    base = Path(path).expanduser()
    for candidate in (base, base / "dxnservice", base.parent if base.name.lower() == "bin" else base):
        if (candidate / "bin" / "mysqld.exe").is_file():
            return candidate.resolve()
    return None


def discover_installations() -> list[Path]:
    candidates = []
    if os.environ.get("DXN_SERVICE_DIR"):
        candidates.append(Path(os.environ["DXN_SERVICE_DIR"]))
    if os.name == "nt":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for branch in (r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                           r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
                try:
                    with winreg.OpenKey(hive, branch) as root:
                        for i in range(winreg.QueryInfoKey(root)[0]):
                            try:
                                with winreg.OpenKey(root, winreg.EnumKey(root, i)) as entry:
                                    title = str(winreg.QueryValueEx(entry, "DisplayName")[0])
                                    if "鼎信诺" in title:
                                        candidates.append(Path(winreg.QueryValueEx(entry, "InstallLocation")[0]))
                            except OSError:
                                continue
                except OSError:
                    continue
    for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        candidates.extend((Path(f"{drive}:/person"), Path(f"{drive}:/dxnservice")))
    found = []
    for candidate in candidates:
        service = _service_dir(candidate)
        if service and service not in found:
            found.append(service)
    return found


def _ini_datadir(service: Path) -> Path | None:
    for ini in (service / "bin" / "DXNDB.ini", service / "bin" / "my.ini", service / "my.ini"):
        if not ini.is_file():
            continue
        raw = ini.read_bytes()
        for encoding in ("utf-8-sig", "gb18030", "utf-16"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeError:
                continue
        else:
            continue
        hit = re.search(r'^\s*datadir\s*=\s*["\']?([^"\'\r\n]+)', text, re.M | re.I)
        if hit:
            result = Path(hit.group(1).strip().replace("\\", "/"))
            return result if result.is_absolute() else (ini.parent / result).resolve()
    return None


def _data_dir(project: str | Path) -> Path:
    base = Path(project).expanduser().resolve()
    source = base if base.name.lower() == "data" else base / "data"
    if not (source / "powersj" / "c_kmyek.frm").is_file():
        raise FileNotFoundError("所选目录不含鼎信诺科目余额数据库（data/powersj/c_kmyek.frm）")
    if source.is_symlink() or (source / "powersj").is_symlink():
        raise ValueError("数据库目录不能使用符号链接")
    return source


def scan_projects(install_path: str = "") -> list[Path]:
    requested = Path(install_path).expanduser() if install_path else None
    services = [_service_dir(requested)] if requested else discover_installations()
    roots = []
    if requested:
        roots.append(requested)
    for service in filter(None, services):
        data = _ini_datadir(service)
        if data:
            project = data.parent if data.name.lower() == "data" else data
            roots.extend((project, project.parent))
    found = set()
    for root in roots:
        if not root.is_dir():
            continue
        candidates = [root]
        try:
            candidates.extend(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            continue
        for candidate in candidates:
            try:
                data = _data_dir(candidate)
            except (FileNotFoundError, ValueError):
                continue
            found.add(data.parent)
    return sorted(found, key=lambda x: str(x).casefold())


def _draft_files(project: Path):
    for root, dirs, files in os.walk(project):
        depth = len(Path(root).relative_to(project).parts)
        dirs[:] = [d for d in dirs if d.lower() not in {"data", "backup", "log"}
                   and not d.startswith((".", "$")) and not (Path(root) / d).is_symlink()] if depth < 3 else []
        if _DRAFT in files:
            yield Path(root) / _DRAFT


def _integer(value, label: str) -> int:
    try:
        numeric = Decimal(str(value).strip())
        number = int(numeric)
    except (ValueError, ArithmeticError) as exc:
        raise ValueError(f"底稿 {label} 不是有效整数") from exc
    if numeric != number:
        raise ValueError(f"底稿 {label} 不是有效整数")
    return number


def _subject_from_draft(path: Path, project: Path) -> Subject:
    import xlrd
    book = xlrd.open_workbook(str(path), on_demand=True)
    try:
        if "dxnsjtempsheet" not in book.sheet_names() or "数据表" not in book.sheet_names():
            raise ValueError("缺少鼎信诺元信息表或数据表")
        sheet = book.sheet_by_name("dxnsjtempsheet")
        meta = {}
        for row in range(min(sheet.nrows, 60)):
            for col in range(min(sheet.ncols - 1, 5)):
                key = str(sheet.cell_value(row, col)).strip()
                if key in {"单位名称", "会计年度期末", "会计期间期末"}:
                    meta[key] = sheet.cell_value(row, col + 1)
        year = _integer(meta.get("会计年度期末", ""), "会计年度期末")
        month = _integer(meta.get("会计期间期末", ""), "会计期间期末")
        name = str(meta.get("单位名称", "")).strip()
        if not name or not 1900 <= year <= 2200 or not 1 <= month <= 12:
            raise ValueError("底稿单位名称、年度或期间不完整")
        main = book.sheet_by_name("数据表")
        identities = set()
        for row in range(min(main.nrows, 100)):
            hit = _ITEM_ID.match(str(main.cell_value(row, 0)).strip())
            if hit:
                identities.add(hit.group(1))
        if len(identities) != 1:
            raise ValueError("底稿未能唯一识别单位 dwid")
        return Subject(identities.pop(), name, year, month, str(project), str(path))
    finally:
        book.release_resources()


def list_subjects(project_path: str) -> list[Subject]:
    project = _data_dir(project_path).parent
    subjects = {}
    failures = []
    for path in sorted(_draft_files(project)):
        try:
            subject = _subject_from_draft(path, project)
        except (ValueError, OSError) as exc:
            failures.append(str(exc))
            continue
        key = (subject.id, subject.year, subject.month)
        if key in subjects and subjects[key].name != subject.name:
            raise ValueError("同一单位编号和期间出现不同单位名称，请检查鼎信诺底稿")
        subjects[key] = subject
    if not subjects:
        raise ValueError("项目中没有可识别的财务报表底稿。" + (failures[0] if failures else ""))
    return sorted(subjects.values(), key=lambda s: (s.name, s.year, s.month, s.id))


def _source_files(data: Path) -> list[Path]:
    schema = data / "powersj"
    files = sorted(p for p in schema.iterdir() if p.is_file() and
                   any(p.name.lower().startswith(table + suffix) for table in _TABLES for suffix in (".", "#")))
    if not any(p.name.lower().startswith("c_kmyek") and p.suffix.lower() == ".myd" for p in files):
        raise ValueError("此项目不是已支持的 MyISAM 科目余额格式，未尝试修改或修复原数据库")
    if any(p.is_symlink() for p in files):
        raise ValueError("科目数据文件不能使用符号链接")
    return files


def _open_source(path: Path):
    if os.name != "nt":
        raise RuntimeError("鼎信诺取数仅支持 Windows")
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                       ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    create.restype = ctypes.c_void_p
    # 禁止并发写入或替换：已被引擎写打开时直接失败，不复制活动表。
    handle = create(str(path), 0x80000000, 1, None, 3, 0x08000000, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        raise OSError(error, "无法锁定只读源文件；请先退出鼎信诺当前项目，再重新取数")
    try:
        return os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY), "rb")
    except BaseException:
        kernel.CloseHandle(ctypes.c_void_p(handle))
        raise


def _snapshot(data: Path, destination: Path) -> None:
    files = _source_files(data)
    with contextlib.ExitStack() as stack:
        opened = [(path, stack.enter_context(_open_source(path))) for path in files]
        before = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in files}
        for path, stream in opened:
            target = destination / "powersj" / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            source_hash = hashlib.sha256()
            with target.open("xb") as output:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    source_hash.update(chunk)
                    output.write(chunk)
            with target.open("rb") as copied:
                if hashlib.file_digest(copied, "sha256").digest() != source_hash.digest():
                    raise RuntimeError("数据库副本校验失败，已停止取数")
        after = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in files}
        if before != after or set(_source_files(data)) != set(files):
            raise RuntimeError("复制期间源数据库发生变化；请退出当前项目后重试")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class DxnLedgerDB:
    """同一项目批量读取时可共用一个副本。只持有并关闭本工具启动的进程。"""

    def __init__(self, project_path: str, install_path: str = "", status_callback=None):
        self.data = _data_dir(project_path)
        services = [_service_dir(install_path)] if install_path else discover_installations()
        self.service = next(filter(None, services), None)
        if self.service is None:
            raise FileNotFoundError("找不到鼎信诺自带 mysqld.exe，请选择鼎信诺安装目录")
        self.status = status_callback or (lambda _: None)
        self.temp = None
        self.process = None
        self.connection = None
        self._log = None

    def __enter__(self):
        try:
            self.start()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *args):
        self.close()

    def start(self):
        import pymysql
        if self.process is not None:
            raise RuntimeError("数据库会话已经启动")
        self.status("正在校验并复制科目余额数据（原库只读）")
        self.temp = tempfile.TemporaryDirectory(prefix="zszh_ledger_")
        root = Path(self.temp.name)
        copy_data = root / "data"
        _snapshot(self.data, copy_data)
        port = _free_port()
        config = root / "reader.ini"
        config.write_text(
            "[mysqld]\n"
            f"basedir={self.service.as_posix()}\ndatadir={copy_data.as_posix()}\nport={port}\n"
            "bind-address=127.0.0.1\nskip-grant-tables\nskip-innodb\n"
            "default-storage-engine=MyISAM\ncharacter-set-server=utf8\n"
            f"log-error={(root / 'server.err').as_posix()}\n",
            encoding="mbcs" if os.name == "nt" else "utf-8")
        self._log = (root / "process.log").open("wb")
        self.process = subprocess.Popen(
            [str(self.service / "bin" / "mysqld.exe"), f"--defaults-file={config}", "--console"],
            cwd=str(self.service / "bin"), stdin=subprocess.DEVNULL, stdout=self._log, stderr=self._log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.status("正在启动科目余额临时副本")
        deadline = time.monotonic() + 45
        last_error = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("鼎信诺临时数据库引擎启动失败；原库未被打开写入。请确认软件版本兼容。")
            try:
                self.connection = pymysql.connect(host="127.0.0.1", port=port, user="root", password="",
                                                  database="powersj", charset="utf8", connect_timeout=1,
                                                  read_timeout=30, write_timeout=5,
                                                  cursorclass=pymysql.cursors.DictCursor)
                break
            except pymysql.MySQLError as exc:
                last_error = exc
                time.sleep(0.25)
        if self.connection is None:
            raise RuntimeError("科目余额临时副本连接超时，请重试") from last_error
        self.status("科目余额副本已就绪")

    def read(self, subject: Subject) -> list[LedgerRow]:
        if _data_dir(subject.project_path) != self.data:
            raise ValueError("不能在同一数据库会话混用不同项目")
        if not subject.id.isdigit() or not 1900 <= subject.year <= 2200 or not 1 <= subject.month <= 12:
            raise ValueError("主体编号、年度或月份不合法")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT kjmonth,kmbh,kmmc,kmjc,jdfx,qcs,jffse,dffse,qms,sjkmmc "
                "FROM c_kmyek WHERE dwid=%s AND kjyear=%s AND kjmonth BETWEEN %s AND %s "
                "ORDER BY kmbh,kjmonth", (int(subject.id), subject.year, 1, subject.month))
            rows = cursor.fetchall()
        if not any(_integer(row["kjmonth"], "月份") == subject.month for row in rows):
            raise ValueError("科目余额库没有所选主体和截止月份的数据，不自动改用其他月份")
        return _aggregate(rows, subject)

    def close(self):
        errors = []
        if self.connection is not None:
            try:
                self.connection.close()
            except Exception:
                pass
            self.connection = None
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    errors.append("临时数据库进程未能结束")
            if not errors:
                self.process = None
        if self._log is not None:
            self._log.close()
            self._log = None
        if self.temp is not None and not errors:
            try:
                self.temp.cleanup()
                self.temp = None
            except OSError:
                errors.append("临时数据库副本清理失败")
        if errors:
            raise RuntimeError("；".join(errors))


def _aggregate(rows: list[dict], subject: Subject) -> list[LedgerRow]:
    grouped = {}
    for row in rows:
        code = str(row["kmbh"]).strip()
        month = _integer(row["kjmonth"], "月份")
        if not code or not 1 <= month <= subject.month:
            raise ValueError("科目余额记录存在无效科目编号或期间")
        account = grouped.setdefault(code, {})
        if month in account:
            raise ValueError(f"科目 {code} 同月存在多条记录，无法确定币种或重复来源，已停止")
        account[month] = row
    output = []
    for code, months in sorted(grouped.items()):
        last = months[max(months)]
        directions = {str(row.get("jdfx", "")).strip() for row in months.values()}
        if len(directions) != 1 or not directions <= {"借", "贷"}:
            raise ValueError(f"科目 {code} 借贷方向缺失或期间内发生变化")
        direction = directions.pop()
        name = str(last.get("kmmc", "")).replace("\\", "").strip()
        if not name:
            raise ValueError(f"科目 {code} 缺少科目名称")
        opening = money(months[1]["qcs"]) if 1 in months else None
        ending = money(months[subject.month]["qms"]) if subject.month in months else None
        values = {field: [money(row.get(field)) for row in months.values()] for field in ("jffse", "dffse")}
        debit = sum(values["jffse"], Decimal(0)) if None not in values["jffse"] else None
        credit = sum(values["dffse"], Decimal(0)) if None not in values["dffse"] else None
        # qcs/qms 是原库自然方向金额；不把普通贷方余额统一转负。
        level = _integer(last.get("kmjc", ""), "科目级次")
        is_pl = code.startswith("6") or (code[:4] in {"5001", "5101", "5201", "5301", "5401", "5402", "5405", "5501", "5502", "5503", "5601", "5701", "5801"}
                                       and any(word in name for word in ("收入", "收益", "损益", "费用", "支出", "主营业务成本", "所得税")))
        output.append(LedgerRow(code=code, name=name, direction=direction, opening=opening, ending=ending,
                                debit=debit, credit=credit, level=level, subject_id=subject.id,
                                year=subject.year, month=subject.month, basis="book", is_profit_loss=is_pl))
    from dataclasses import replace
    for index, row in enumerate(output):
        parents = sorted((p for p in output if row.code.startswith(p.code) and len(p.code) < len(row.code)
                          and p.level < row.level), key=lambda p: p.level)
        output[index] = replace(row, path="/".join([p.name for p in parents] + [row.name]))
    return output


def read_ledger(subject: Subject, install_path: str = "", status_callback=None) -> list[LedgerRow]:
    with DxnLedgerDB(subject.project_path, install_path, status_callback) as database:
        return database.read(subject)
