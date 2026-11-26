"""Local continuation records for audit tasks waiting for a user decision."""

from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import agent_tools
from .agent import StaleContextError, _sha256
from .models import money


def data_dir():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ZSZH-TB" / "agent"


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return sha256(encoded).hexdigest()


def _paper_digest(data):
    return _digest((asdict(data), getattr(data.journal, "complete", None)))


def _subject(snapshot):
    return asdict(snapshot.subject)


def _identity(snapshot):
    return {"subject": _subject(snapshot), "ledger_digest": snapshot.ledger_digest,
            "template": snapshot.template_path, "template_digest": snapshot.template_digest,
            "unit": snapshot.unit, "periods": list(snapshot.periods), "install_path": snapshot.install_path,
            "evidence_root": snapshot.evidence_root}


def _source_key(row):
    return row.code, row.path, row.basis, row.direction


def save(run_id, config, context, state):
    directory = data_dir() / "pending"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{run_id}-{state['sequence']:06d}.json"
    matches = [dict(target=item.target.id, sources=[_source_key(row) for row in item.sources],
                    amount=None if item.amount is None else str(item.amount), status=item.status,
                    method=item.method, reason=item.reason, selected=item.selected, overwrite=item.overwrite)
               for item in context.results]
    prepared = {index: {"data_digest": _paper_digest(item["data"]), "draft": item["draft"],
                        "warnings": item["warnings"]} for index, item in context.prepared_workpapers.items()}
    record = {"version": 1, "run_id": run_id, "identity": _identity(context.snapshot), "config": config, "state": state,
              "evidence_digest": _digest(context.evidence_rows), "scope": sorted(context.allowed_scope),
              "scanned": context.scan is not None, "results": matches, "prepared": prepared,
              "artifacts": context.artifacts, "gaps": context.gaps, "tb_exported": context.tb_exported,
              "initial_results_digest": context.snapshot.results_digest}
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, ensure_ascii=False, default=str)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def load(path):
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("version") != 1 or not all(
            key in record for key in ("run_id", "identity", "config", "state", "scope")):
        raise ValueError("无法识别任务进度文件。")
    return record


def pending_for(subject):
    directory = data_dir() / "pending"
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            record = load(path)
            if record["identity"]["subject"] == asdict(subject):
                yield path, record
        except (OSError, ValueError, KeyError, TypeError):
            continue


def restore(path, record, context):
    if record["identity"] != _identity(context.snapshot):
        raise StaleContextError("暂停后账套、模板、期间或来源位置已变化，请重新核对后开始新任务。")
    if record["evidence_digest"] != _digest(context.evidence_rows):
        raise StaleContextError("暂停后项目证据已变化，请重新核对后开始新任务。")
    for artifact in record["artifacts"]:
        if _sha256(artifact["path"]) != artifact["sha256"]:
            raise StaleContextError("暂停后已生成的文件发生变化，请确认文件后开始新任务。")
    if record["scanned"]:
        scan = agent_tools._ensure_scan(context)
        targets = {field.id: field for field in scan.fields}
        ledger = {_source_key(row): row for row in context.rows}
        by_target = {item.target.id: item for item in context.results}
        results = []
        for saved in record["results"]:
            target = targets[saved["target"]]
            match = by_target[target.id]
            match.sources = [ledger[tuple(key)] for key in saved["sources"]]
            match.amount = money(saved["amount"])
            for name in ("status", "method", "reason", "selected", "overwrite"):
                setattr(match, name, saved[name])
            results.append(match)
        context.results = results
    for index, item in record["prepared"].items():
        paper, data, _accounts = agent_tools.read_workpaper_facts(context, index)
        if _paper_digest(data) != item["data_digest"]:
            raise StaleContextError(f"暂停后 {index} 底稿的取数已变化，请重新编制。")
        context.prepared_workpapers[index] = {"paper": paper, "data": data, "draft": item["draft"],
                                              "warnings": item["warnings"]}
    context.artifacts = record["artifacts"]
    context.gaps = record["gaps"]
    context.tb_exported = record["tb_exported"]
    context.ensure_current()
    # A later pause gets a new filename, so a stale window can only claim its original checkpoint.
    path.unlink()
