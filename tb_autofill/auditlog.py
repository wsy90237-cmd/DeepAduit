"""本地审计痕迹：哈希链追加日志。

每条记录包含前一条的哈希，任何改写、删除或插入都会在 verify() 中被发现。
日志只追加（append-only），不做原地修改，用于回答「谁在何时确认了哪一处改动」。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

GENESIS = "0" * 64


class AuditLogError(Exception):
    pass


def _canonical(record) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def default_path(root=None) -> Path:
    base = Path(root) if root else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ZSZH-TB"
    return base / "audit" / f"{datetime.now():%Y%m}.jsonl"


class AuditLog:
    def __init__(self, path=None, actor: str = ""):
        self.path = Path(path) if path else default_path()
        self.actor = actor or os.environ.get("USERNAME", "") or "unknown"

    def _tail(self):
        if not self.path.exists():
            return GENESIS, 0
        last, count = None, 0
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    last, count = line, count + 1
        if last is None:
            return GENESIS, 0
        try:
            return json.loads(last)["hash"], count
        except (ValueError, KeyError) as exc:
            raise AuditLogError("审计日志尾部损坏，请先备份并人工核对后再写入") from exc

    def append(self, event: str, **fields):
        previous, count = self._tail()
        record = {
            "seq": count + 1,
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "actor": self.actor,
            "event": str(event),
        }
        record.update(fields)
        record["prev"] = previous
        record["hash"] = hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return record

    def verify(self):
        """返回 (是否完整, 首个异常序号, 原因)。日志不存在视为完整。"""
        if not self.path.exists():
            return True, None, ""
        previous, expected = GENESIS, 1
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    return False, expected, "存在无法解析的记录"
                if not isinstance(record, dict) or record.get("seq") != expected:
                    return False, expected, "记录序号不连续（可能被删除或插入）"
                if record.get("prev") != previous:
                    return False, expected, "前序哈希不匹配（可能被删除或替换）"
                body = {key: value for key, value in record.items() if key != "hash"}
                if hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest() != record.get("hash"):
                    return False, expected, "记录内容与哈希不一致（可能被改写）"
                previous, expected = record["hash"], expected + 1
        return True, None, ""

    def records(self):
        if not self.path.exists():
            return []
        output = []
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    try:
                        output.append(json.loads(line))
                    except ValueError:
                        continue
        return output
