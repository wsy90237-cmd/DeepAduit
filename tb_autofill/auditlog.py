"""哈希链及 Windows DPAPI 末尾检查点；不承诺抵御同一用户回滚全部本地状态。"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
import tempfile

GENESIS = "0" * 64
RESERVED = {"seq", "ts", "actor", "event", "prev", "hash"}


class AuditLogError(Exception):
    pass


def _canonical(record) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def default_path(root=None) -> Path:
    # Keep the original storage location so a rename never discards existing keys/logs.
    base = Path(root) if root else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ZSZH-TB"
    return base / "audit" / f"{datetime.now():%Y%m}.jsonl"


class AuditLog:
    def __init__(self, path=None, actor: str = ""):
        self.path = Path(path) if path else default_path()
        self.checkpoint = self.path.with_suffix(self.path.suffix + ".head.dpapi")
        self.actor = actor or os.environ.get("USERNAME", "") or "unknown"

    @contextmanager
    def _locked(self):
        import msvcrt
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(self.path.suffix + ".lock").open("a+b") as lock:
            if not lock.seek(0, os.SEEK_END):
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)

    def _inspect(self):
        previous, count = GENESIS, 0
        if not self.path.exists():
            if self.checkpoint.exists():
                return False, 1, "日志文件缺失但末尾检查点仍存在", previous, count
            return True, None, "", previous, count
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                expected = count + 1
                try:
                    record = json.loads(line)
                except ValueError:
                    return False, expected, "存在无法解析的记录", previous, count
                if not isinstance(record, dict) or record.get("seq") != expected:
                    return False, expected, "记录序号不连续（可能被删除或插入）", previous, count
                if record.get("prev") != previous:
                    return False, expected, "前序哈希不匹配（可能被删除或替换）", previous, count
                body = {key: value for key, value in record.items() if key != "hash"}
                if hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest() != record.get("hash"):
                    return False, expected, "记录内容与哈希不一致（可能被改写）", previous, count
                previous, count = record["hash"], expected
        if self.checkpoint.exists():
            try:
                import win32crypt
                raw = win32crypt.CryptUnprotectData(self.checkpoint.read_bytes(), None, None, None, 1)[1]
                head = json.loads(raw)
                if head != {"seq": count, "hash": previous}:
                    return False, count + 1, "日志末尾与本机检查点不符（可能被截断或回滚）", previous, count
            except Exception:
                return False, count + 1, "无法验证本机末尾检查点，请保留现场并人工核对", previous, count
            return True, None, "", previous, count
        return True, None, "仅链内校验：无末尾检查点，不能判断旧日志是否被截断", previous, count

    def _save_checkpoint(self, count, digest):
        import win32crypt
        raw = _canonical({"seq": count, "hash": digest}).encode("utf-8")
        encrypted = win32crypt.CryptProtectData(raw, "DeepAudit audit checkpoint", None, None, None, 1)
        handle, name = tempfile.mkstemp(prefix=".audit-head-", dir=self.path.parent)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.checkpoint)
        finally:
            Path(name).unlink(missing_ok=True)

    def append(self, event: str, **fields):
        if RESERVED.intersection(fields):
            raise AuditLogError("业务字段不能覆盖日志序号、身份或哈希字段")
        with self._locked():
            ok, _seq, reason, previous, count = self._inspect()
            if not ok:
                raise AuditLogError(reason + "；未继续追加日志")
            record = {"seq": count + 1, "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                      "actor": self.actor, "event": str(event), **fields, "prev": previous}
            record["hash"] = hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                self._save_checkpoint(record["seq"], record["hash"])
            except Exception as exc:
                raise AuditLogError("日志已追加，但末尾检查点保存失败，请保留现场并人工核对") from exc
            return record

    def verify(self):
        """返回 (链与可用检查点是否通过, 首个异常序号, 限制或失败原因)。"""
        try:
            with self._locked():
                return self._inspect()[:3]
        except (OSError, UnicodeError) as exc:
            return False, None, f"日志无法读取或锁定：{exc}"

    def records(self):
        if not self.path.exists():
            return []
        with self._locked(), self.path.open("r", encoding="utf-8") as stream:
            output = []
            for line in stream:
                if line.strip():
                    try:
                        output.append(json.loads(line))
                    except ValueError:
                        continue
            return output
