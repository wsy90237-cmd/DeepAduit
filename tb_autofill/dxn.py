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

from .models import AuxiliaryBalanceRow, JournalRow, JournalRows, LedgerRow, PaperData, Subject, money

_DRAFT = "财务报表底稿.xls"
_ITEM_ID = re.compile(r"^I\d+D(\d+)N(\d+)Z", re.I)
_TABLES = ("c_kmyek", "c_cwkmk")
_AUXILIARY_TABLES = ("c_hsxm_yye", "c_kmvshsxm")
# 序时账＝凭证分录表；同一库内按 kmbh 前缀取明细，不另建汇总。
_JOURNAL_TABLE = "c_cwpz"
_JOURNAL_COLUMNS = "kjmonth,jzsj,pzzl,pzbh,bh,sm,kmbh,jffse,dffse,dfkmbh,dfkmmc"
_CODE = re.compile(r"^\d{1,20}$")
JOURNAL_LIMIT = 2000
_PROFIT_LOSS_NAMES = {
    "主营业务收入", "产品销售收入", "其他业务收入", "其他收益", "营业收入",
    "主营业务成本", "产品销售成本", "其他业务成本", "其他业务支出", "营业成本",
    "销售费用", "产品销售费用", "营业费用", "管理费用", "财务费用", "研发费用",
    "税金及附加", "产品销售税金及附加", "经营税金及附加", "营业税金及附加",
    "投资收益", "投资损益", "资产处置损益", "公允价值变动损益", "公允价值变动收益",
    "信用减值损失", "资产减值损失", "营业外收入", "营业外支出", "补贴收入",
    "所得税", "所得税费用", "以前年度损益调整",
}


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


def _journal_code(code) -> str:
    """序时账科目编码只接受纯数字：既防 LIKE 通配，也防注入。"""
    value = str(code or "").strip()
    if not _CODE.match(value):
        raise ValueError("科目编码不合法，无法读取序时账")
    return value


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
                identities.add((hit.group(1), hit.group(2)))
        if len(identities) != 1:
            raise ValueError("底稿未能唯一识别单位及账套编号（dwid/ndid）")
        subject_id, ledger_id = identities.pop()
        return Subject(subject_id, name, year, month, str(project), str(path), ledger_id)
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
        key = (subject.id, subject.year, subject.month, subject.ledger_id)
        if key in subjects and subjects[key].name != subject.name:
            raise ValueError("同一单位编号和期间出现不同单位名称，请检查鼎信诺底稿")
        subjects[key] = subject
    if not subjects:
        raise ValueError("项目中没有可识别的财务报表底稿。" + (failures[0] if failures else ""))
    return sorted(subjects.values(), key=lambda s: (s.name, s.year, s.month, s.id))


def _source_files(data: Path, tables=_TABLES) -> list[Path]:
    if not tables:
        raise ValueError("没有指定读取数据表")
    schema = data / "powersj"
    files = sorted(p for p in schema.iterdir() if p.is_file() and
                   any(p.name.lower().startswith(table + suffix) for table in tables for suffix in (".", "#")))
    required = "c_kmyek" if "c_kmyek" in tables else _JOURNAL_TABLE if _JOURNAL_TABLE in tables else tables[0]
    if not any(p.name.lower().startswith(required) and p.suffix.lower() == ".myd" for p in files):
        if required == "c_kmyek":
            raise ValueError("此项目不是已支持的 MyISAM 科目余额格式，未尝试修改或修复原数据库")
        raise ValueError(f"此项目没有可读取的序时账数据（{required}），未尝试修改或修复原数据库")
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


def _snapshot(data: Path, destination: Path, tables=_TABLES):
    files = _source_files(data, tables)
    fingerprints = []
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
            fingerprints.append((str(path.resolve()), *before[path], source_hash.hexdigest()))
        after = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in files}
        if before != after or set(_source_files(data, tables)) != set(files):
            raise RuntimeError("复制期间源数据库发生变化；请退出当前项目后重试")
    return tuple(fingerprints)


def _present_table(schema: Path, table: str) -> bool:
    return any((schema / (table + suffix)).is_file() for suffix in (".MYD", ".myd"))


def validate_paper_data_source(data: PaperData) -> bool:
    """导出或发送前重验一致副本来源；来源被改动、成员变化或补入新表都判为失效。"""
    if not data.source_path or not data.source_tables or not data.source_fingerprint:
        return False
    schema = Path(data.source_path) / "powersj"
    if any(_present_table(schema, table) for table in data.source_missing_tables):
        return False
    expected = {record[0]: record[1:] for record in data.source_fingerprint}
    files = _source_files(Path(data.source_path), data.source_tables)
    if {str(path.resolve()) for path in files} != set(expected):
        return False
    with contextlib.ExitStack() as stack:
        opened = [(path, stack.enter_context(_open_source(path))) for path in files]
        for path, stream in opened:
            size, stamp, digest = expected[str(path.resolve())]
            before = path.stat()
            if (before.st_size, before.st_mtime_ns) != (size, stamp):
                return False
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                return False
            after = path.stat()
            if (after.st_size, after.st_mtime_ns) != (size, stamp):
                return False
    return True


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class DxnLedgerDB:
    """同一项目批量读取时可共用一个副本。只持有并关闭本工具启动的进程。"""

    def __init__(self, project_path: str, install_path: str = "", status_callback=None,
                 tables=_TABLES):
        self.data = _data_dir(project_path)
        self.tables = tuple(tables)
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
        self.source_fingerprint = _snapshot(self.data, copy_data, self.tables)
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
        ledger_id = subject.ledger_id
        if ledger_id and not ledger_id.isdigit():
            raise ValueError("账套编号不合法")
        with self.connection.cursor() as cursor:
            if not ledger_id:
                cursor.execute("SELECT DISTINCT ndid FROM c_kmyek WHERE dwid=%s AND kjyear=%s AND kjmonth=%s",
                               (int(subject.id), subject.year, subject.month))
                identities = cursor.fetchall()
                if len(identities) != 1 or not str(identities[0]["ndid"]).isdigit():
                    raise ValueError("所选主体期间缺少唯一账套编号，请重新选择对应的鼎信诺底稿")
                ledger_id = str(identities[0]["ndid"])
            cursor.execute(
                "SELECT kjmonth,kmbh,kmmc,kmjc,jdfx,qcs,jffse,dffse,fse,qms,sjkmmc "
                "FROM c_kmyek WHERE dwid=%s AND ndid=%s AND kjyear=%s AND kjmonth BETWEEN %s AND %s "
                "ORDER BY kmbh,kjmonth", (int(subject.id), int(ledger_id), subject.year, 1, subject.month))
            rows = cursor.fetchall()
        if not any(_integer(row["kjmonth"], "月份") == subject.month for row in rows):
            raise ValueError("科目余额库没有所选主体和截止月份的数据，不自动改用其他月份")
        from dataclasses import replace
        return _aggregate(rows, replace(subject, ledger_id=ledger_id))

    def journal(self, subject: Subject, code: str, limit: int = JOURNAL_LIMIT) -> list[JournalRow]:
        """按科目编码前缀取序时账（凭证分录）；一级科目返回其全部下级分录。

        只读副本，不改原库；分录金额取自 jffse / dffse 原值，不做方向翻转。
        """
        if _data_dir(subject.project_path) != self.data:
            raise ValueError("不能在同一数据库会话混用不同项目")
        code = _journal_code(code)
        if not subject.id.isdigit() or not 1900 <= subject.year <= 2200 or not 1 <= subject.month <= 12:
            raise ValueError("主体编号、年度或月份不合法")
        limit = max(1, min(int(limit), JOURNAL_LIMIT))
        ledger_id = subject.ledger_id
        if ledger_id and not ledger_id.isdigit():
            raise ValueError("账套编号不合法")
        with self.connection.cursor() as cursor:
            if not ledger_id:
                cursor.execute("SELECT DISTINCT ndid FROM c_cwpz WHERE dwid=%s AND kjyear=%s AND kjmonth<=%s",
                               (int(subject.id), subject.year, subject.month))
                identities = cursor.fetchall()
                if len(identities) != 1 or not str(identities[0]["ndid"]).isdigit():
                    raise ValueError("所选主体期间缺少唯一账套编号，请重新选择对应的鼎信诺底稿")
                ledger_id = str(identities[0]["ndid"])
            cursor.execute(
                f"SELECT {_JOURNAL_COLUMNS} FROM {_JOURNAL_TABLE} "
                "WHERE dwid=%s AND ndid=%s AND kjyear=%s AND kjmonth BETWEEN 1 AND %s AND kmbh LIKE %s "
                "ORDER BY kjmonth DESC,jzsj DESC,pzbh DESC,bh DESC LIMIT %s",
                (int(subject.id), int(ledger_id), subject.year, subject.month, f"{code}%", limit + 1))
            rows = cursor.fetchall()
        output = []
        for row in rows[:limit]:
            month = _integer(row["kjmonth"], "月份")
            if not 1 <= month <= subject.month:
                raise ValueError("序时账存在超出所选期间的记录，已停止读取")
            output.append(JournalRow(
                month=month, date=str(row.get("jzsj") or "")[:10],
                voucher=f"{str(row.get('pzzl') or '').strip()}-{row.get('pzbh') or ''}".strip("-"),
                line=str(row.get("bh") or "").strip(),
                summary=re.sub(r"\s+", " ", str(row.get("sm") or "")).strip(),
                code=str(row.get("kmbh") or "").strip(),
                debit=money(row.get("jffse")), credit=money(row.get("dffse")),
                contra=re.sub(r"[;\s]+", "、", str(row.get("dfkmmc") or "")).strip("、"),
            ))
        return JournalRows(output, complete=len(rows) <= limit)

    def paper_data(self, subject: Subject, codes, *, with_journal=True) -> PaperData:
        """报表项目的总账、月份、客商和凭证共用一次一致副本。"""
        from dataclasses import replace
        wanted = tuple(dict.fromkeys(_journal_code(code) for code in codes))
        all_rows = self.read(subject)
        roots = [row for row in all_rows if row.code in wanted]
        if not wanted or len(roots) != len(wanted):
            raise ValueError("报表项目必须明确绑定当前账套的科目")
        if any(row.level != 1 for row in roots):
            from . import audit_index
            # 先用完整总账验证共享总根与全部类别，再允许已确认的类别子树取数。
            components = {item for paper in audit_index.resolve_papers()
                          for item in audit_index.reserve_components(all_rows, paper)}
            for row in roots:
                if row.level == 1:
                    continue
                parent = next((item for item in all_rows if item.level == 1 and item.path
                               and row.path.rpartition("/")[0] == item.path
                               and row.code.startswith(item.code)), None)
                if parent is None or row not in components:
                    raise ValueError("二级科目必须是经共享总根核对并确认归属的备抵类别")
        subject = replace(subject, ledger_id=roots[0].ledger_id)
        rows = tuple(row for row in all_rows if any(row == root or
                     (row.code.startswith(root.code) and root.path and row.path.startswith(root.path + "/"))
                     for root in roots))
        condition = " OR ".join("kmbh LIKE %s" for _ in wanted)
        parameters = (int(subject.id), int(subject.ledger_id))
        patterns = tuple(code + "%" for code in wanted)
        # 上年需同时取得祖先，才能验证类别与层级归属，而非仅按末级编码对数。
        previous_roots = tuple(dict.fromkeys(next((parent.code for parent in all_rows
                               if parent.level == 1 and parent.path and
                               (root == parent or (root.code.startswith(parent.code)
                                and root.path.startswith(parent.path + "/")))), root.code) for root in roots))
        previous_condition = " OR ".join("kmbh LIKE %s" for _ in previous_roots)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT kjmonth,kmbh,kmmc,kmjc,jdfx,qcs,jffse,dffse,fse,qms,sjkmmc "
                           "FROM c_kmyek WHERE dwid=%s AND ndid=%s AND kjyear=%s "
                           f"AND kjmonth BETWEEN 1 AND %s AND ({previous_condition}) ORDER BY kmbh,kjmonth",
                           (*parameters, subject.year - 1, subject.month, *(code + "%" for code in previous_roots)))
            previous = cursor.fetchall()
            cursor.execute("SELECT kmbh,ws_fse,sn_ws_fse FROM c_cwkmk "
                           f"WHERE dwid=%s AND ndid=%s AND ({condition})",
                           (*parameters, *patterns))
            annual = cursor.fetchall()
            auxiliary, configs = [], []
            if all(table in self.tables for table in _AUXILIARY_TABLES):
                cursor.execute("SELECT kmbh,hsxmlxbh,hsxmlxmc,xz FROM c_kmvshsxm "
                               f"WHERE dwid=%s AND ndid=%s AND ({condition})", (*parameters, *patterns))
                configs = cursor.fetchall()
                cursor.execute("SELECT kjmonth,kmbh,kmmc,jdfx,hsxmlxbh,hsxmlxmc,hsxmbh,hsxmmc,"
                               "hsxmjc,hsxmsfmx,qcs,jffse,dffse,qms FROM c_hsxm_yye "
                               "WHERE dwid=%s AND ndid=%s AND kjyear=%s AND kjmonth BETWEEN 1 AND %s "
                               f"AND ({condition}) ORDER BY kmbh,hsxmlxbh,hsxmbh,kjmonth",
                               (*parameters, subject.year, subject.month, *patterns))
                auxiliary = cursor.fetchall()
        rows = _paper_occurrences(rows, previous, annual, subject)
        parties, checks = _auxiliary_balances(auxiliary, configs, rows, subject)
        warnings = []
        if any(row.is_profit_loss and row.period_amount is None for row in rows):
            warnings.append("部分损益科目缺少独立发生额来源，未用借方或贷方发生额代填。")
        if any(row.previous_period_source.startswith("待确认") for row in rows):
            warnings.append("上年同编码科目的名称、层级归属或方向不一致；相应上期数与月份已留空，需确认跨年对应关系。")
        if not all(table in self.tables for table in _AUXILIARY_TABLES):
            warnings.append("未取得支持格式的客商核算配置和余额，客商明细待补。")
        journal = None
        overlapping_codes = any(left != right and left.startswith(right) for left in wanted for right in wanted)
        if with_journal and overlapping_codes:
            warnings.append("所选科目的编码前缀存在重叠，序时账范围待明确，未重复归集。")
        elif with_journal and _JOURNAL_TABLE in self.tables:
            parts = [self.journal(subject, code) for code in wanted]
            combined = sorted((row for part in parts for row in part),
                              key=lambda row: (row.month, row.date, row.voucher, row.line), reverse=True)
            journal = JournalRows(combined[:JOURNAL_LIMIT],
                                  complete=len(combined) <= JOURNAL_LIMIT and all(part.complete for part in parts))
        elif with_journal:
            warnings.append("未取得支持格式的序时账，凭证和截止性测试待补。")
        expected_optional = (*_AUXILIARY_TABLES, _JOURNAL_TABLE) if with_journal else _AUXILIARY_TABLES
        return PaperData(rows=rows, codes=wanted, auxiliary=parties, journal=journal,
                         auxiliary_checks=checks, warnings=tuple(warnings), source_path=str(self.data),
                         source_tables=self.tables, source_fingerprint=getattr(self, "source_fingerprint", ()),
                         source_missing_tables=tuple(table for table in expected_optional if table not in self.tables))

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
        complete = set(months) == set(range(1, subject.month + 1))
        debit = sum(values["jffse"], Decimal(0)) if complete and None not in values["jffse"] else None
        credit = sum(values["dffse"], Decimal(0)) if complete and None not in values["dffse"] else None
        monthly = tuple(money(months[month].get("fse")) if month in months else None for month in range(1, 13))
        occurrence = sum(monthly[:subject.month], Decimal(0)) if complete and None not in monthly[:subject.month] else None
        # qcs/qms 是原库自然方向金额；不把普通贷方余额统一转负。
        level = _integer(last.get("kmjc", ""), "科目级次")
        output.append(LedgerRow(code=code, name=name, direction=direction, opening=opening, ending=ending,
                                debit=debit, credit=credit, level=level, subject_id=subject.id,
                                year=subject.year, month=subject.month, basis="book",
                                ledger_id=subject.ledger_id, period_amount=occurrence,
                                monthly_amounts=monthly,
                                period_source="c_kmyek.fse（独立账面发生额）" if occurrence is not None else ""))
    from dataclasses import replace
    for index, row in enumerate(output):
        parents = sorted((p for p in output if row.code.startswith(p.code) and len(p.code) < len(row.code)
                          and p.level < row.level), key=lambda p: p.level)
        root_name = re.sub(r"\s+", "", parents[0].name if parents else row.name)
        output[index] = replace(row, path="/".join([p.name for p in parents] + [row.name]),
                                is_profit_loss=row.code.startswith("6") or root_name in _PROFIT_LOSS_NAMES)
    return output


def _paper_occurrences(rows, previous, annual, subject):
    """独立发生额保留来源；上年未审数不冒充上年审定数或中期同期数。"""
    from dataclasses import replace
    previous_subject = replace(subject, year=subject.year - 1)
    previous_by_code = {row.code: row for row in _aggregate(previous, previous_subject)}
    annual_by_code = {}
    for item in annual:
        code = str(item["kmbh"]).strip()
        if code in annual_by_code:
            raise ValueError(f"科目 {code} 年度发生额存在重复来源")
        annual_by_code[code] = item
    def identity(item):
        path = re.sub(r"\s+", "", (item.path or item.name).replace("\\", "/"))
        return item.level, item.direction, path
    result = []
    for row in rows:
        before = previous_by_code.get(row.code)
        conflict = before is not None and identity(before) != identity(row)
        previous_amount = before.period_amount if before and not conflict else None
        previous_monthly = before.monthly_amounts if before and not conflict else (None,) * 12
        previous_source = "c_kmyek.fse（同账套上年同期账面发生额）" if previous_amount is not None else ""
        if conflict:
            previous_source = "待确认：上年同编码科目的名称、层级归属或方向不一致"
        occurrence, source = row.period_amount, row.period_source
        meta = annual_by_code.get(row.code, {})
        if subject.month == 12:
            if occurrence is None and money(meta.get("ws_fse")) is not None:
                occurrence = money(meta["ws_fse"])
                source = "c_cwkmk.ws_fse（账套全年未审发生额）"
            if not conflict and previous_amount is None and money(meta.get("sn_ws_fse")) is not None:
                previous_amount = money(meta["sn_ws_fse"])
                previous_source = "c_cwkmk.sn_ws_fse（账套上年全年未审发生额）"
        result.append(replace(row, period_amount=occurrence, period_source=source,
                              previous_period_amount=previous_amount,
                              previous_monthly_amounts=previous_monthly, previous_period_source=previous_source))
    return tuple(result)


def _auxiliary_balances(records, configs, rows, subject):
    """只取明确选中的客商维度，科目和核算项目的汇总层不得重复相加。"""
    accounts = {row.code: row for row in rows}
    records_by_account = {}
    for record in records:
        records_by_account.setdefault(str(record.get("kmbh") or "").strip(), []).append(record)
    chosen = {}
    for item in configs:
        code = str(item.get("kmbh") or "").strip()
        label = str(item.get("hsxmlxmc") or "").strip()
        if str(item.get("xz") or "").strip() == "1" and any(
                token in label for token in ("客户", "供应商", "客商", "往来单位")):
            chosen.setdefault(code, set()).add((str(item.get("hsxmlxbh") or "").strip(), label))
    selected, checks = [], []
    for code, dimensions in sorted(chosen.items()):
        account = accounts.get(code)
        if account is None:
            continue
        # 配置和余额均包含上下级时，只使用有实际辅助余额的末级科目。
        if any(other != code and other in accounts and accounts[other].path.startswith(account.path + "/")
               and any(str(record.get("hsxmlxbh") or "").strip() in {item[0] for item in chosen[other]}
                       for record in records_by_account.get(other, ())) for other in chosen):
            continue
        type_code = next(iter(dimensions))[0] if len(dimensions) == 1 else ""
        check = {"account_code": code, "type_code": type_code, "status": "missing",
                 "opening_difference": None, "ending_difference": None, "note": "未取得完整客商余额"}
        if len(dimensions) != 1 or not type_code:
            check.update(status="ambiguous", note="同一科目选择了多个客商维度，未重复归集")
            checks.append(check)
            continue
        type_name = next(iter(dimensions))[1]
        candidates = [record for record in records_by_account.get(code, ())
                      if str(record.get("hsxmlxbh") or "").strip() == type_code]
        has_hierarchy = any(_integer(record.get("hsxmjc") or 1, "核算项目级次") > 1 for record in candidates)
        unknown_flags = any(str(record.get("hsxmsfmx") or "").strip() not in {"0", "1"}
                            for record in candidates)
        if unknown_flags and (has_hierarchy or any(str(record.get("hsxmjc") or "").strip() != "1"
                                                   for record in candidates)):
            check.update(status="ambiguous", note="客商层级缺少末级标记，未猜测汇总范围")
            checks.append(check)
            continue
        grouped = {}
        for record in candidates:
            if str(record.get("hsxmsfmx") or "").strip() == "0":
                continue
            party = str(record.get("hsxmbh") or "").strip()
            name = str(record.get("hsxmmc") or "").strip()
            month = _integer(record.get("kjmonth"), "月份")
            if not party or not name or not 1 <= month <= subject.month:
                raise ValueError("客商余额缺少编码、名称或有效期间")
            months = grouped.setdefault(party, {})
            if month in months:
                raise ValueError("同一科目、核算维度和客商同月有重复余额，已停止")
            months[month] = record
        lines = []
        for party, months in sorted(grouped.items()):
            last = months[max(months)]
            directions = {str(record.get("jdfx") or "").strip() for record in months.values()}
            names = {str(record.get("hsxmmc") or "").strip() for record in months.values()}
            if len(directions) != 1 or not directions <= {"借", "贷"} or len(names) != 1:
                raise ValueError("同一客商编码的名称或借贷方向在期间内冲突，已停止")
            complete = set(months) == set(range(1, subject.month + 1))
            totals = {}
            for field in ("jffse", "dffse"):
                values = [money(record.get(field)) for record in months.values()]
                totals[field] = sum(values, Decimal(0)) if complete and None not in values else None
            lines.append(AuxiliaryBalanceRow(
                account_code=code, account_name=account.name, type_code=type_code, type_name=type_name,
                code=party, name=str(last["hsxmmc"]).strip(), direction=directions.pop(),
                opening=money(months[1].get("qcs")) if 1 in months else None,
                ending=money(months[subject.month].get("qms")) if subject.month in months else None,
                debit=totals["jffse"], credit=totals["dffse"], subject_id=subject.id,
                ledger_id=subject.ledger_id, year=subject.year, month=subject.month))
        for field in ("opening", "ending"):
            amounts = [getattr(line, field) * (1 if line.direction == account.direction else -1)
                       if getattr(line, field) is not None else None for line in lines]
            if amounts and None not in amounts and getattr(account, field) is not None:
                check[field + "_difference"] = sum(amounts, Decimal(0)) - getattr(account, field)
        gaps = (check["opening_difference"], check["ending_difference"])
        if any(gap is not None and gap != 0 for gap in gaps):
            check.update(status="difference", note="客商余额合计与对应科目账面数存在差异")
        elif None not in gaps:
            check.update(status="matched", note="客商期初、期末合计与对应科目账面数一致")
        checks.append(check)
        selected.extend(lines)
    for root in (row for row in rows if row.level == 1):
        nested = [line for line in selected if line.account_code != root.code and
                  accounts[line.account_code].path.startswith(root.path + "/")]
        if not nested:
            continue
        check = {"account_code": root.code, "type_code": "", "status": "missing",
                 "opening_difference": None, "ending_difference": None, "note": "客商明细覆盖范围待核对"}
        for field in ("opening", "ending"):
            amounts = [getattr(line, field) * (1 if line.direction == root.direction else -1)
                       if getattr(line, field) is not None else None for line in nested]
            if None not in amounts and getattr(root, field) is not None:
                check[field + "_difference"] = sum(amounts, Decimal(0)) - getattr(root, field)
        gaps = (check["opening_difference"], check["ending_difference"])
        incomplete = any(item["status"] in {"missing", "ambiguous"} and item["account_code"] != root.code
                         and accounts[item["account_code"]].path.startswith(root.path + "/") for item in checks)
        if any(gap is not None and gap != 0 for gap in gaps):
            check.update(status="difference", note="客商明细合计与一级科目账面数存在差异")
        elif None not in gaps and not incomplete:
            check.update(status="matched", note="客商明细期初、期末合计与一级科目账面数一致")
        checks.append(check)
    return tuple(selected), tuple(checks)


def read_ledger(subject: Subject, install_path: str = "", status_callback=None) -> list[LedgerRow]:
    with DxnLedgerDB(subject.project_path, install_path, status_callback) as database:
        return database.read(subject)


def read_paper_data(subject: Subject, codes, install_path: str = "", with_journal=True,
                    status_callback=None) -> PaperData:
    data = _data_dir(subject.project_path)
    tables = list(_TABLES)
    for table in _AUXILIARY_TABLES:
        if (data / "powersj" / (table + ".MYD")).is_file() or (data / "powersj" / (table + ".myd")).is_file():
            tables.append(table)
    if with_journal and ((data / "powersj" / (_JOURNAL_TABLE + ".MYD")).is_file()
                         or (data / "powersj" / (_JOURNAL_TABLE + ".myd")).is_file()):
        tables.append(_JOURNAL_TABLE)
    with DxnLedgerDB(subject.project_path, install_path, status_callback, tables=tables) as database:
        return database.paper_data(subject, codes, with_journal=with_journal)


def read_journal(subject: Subject, code: str, install_path: str = "", status_callback=None,
                 limit: int = JOURNAL_LIMIT) -> list[JournalRow]:
    """单独会话读取序时账：只复制凭证分录表，不触发科目余额的完整拷贝。"""
    with DxnLedgerDB(subject.project_path, install_path, status_callback,
                     tables=(_JOURNAL_TABLE,)) as database:
        return database.journal(subject, code, limit=limit)


def read_journals(subject: Subject, codes, install_path: str = "", status_callback=None,
                  limit: int = JOURNAL_LIMIT) -> dict[str, list[JournalRow]]:
    """一次会话读取多个科目的序时账，避免每科重复复制凭证表。"""
    wanted = [str(code) for code in codes if str(code or "").strip()]
    if not wanted:
        return {}
    with DxnLedgerDB(subject.project_path, install_path, status_callback,
                     tables=(_JOURNAL_TABLE,)) as database:
        return {code: database.journal(subject, code, limit=limit) for code in wanted}
