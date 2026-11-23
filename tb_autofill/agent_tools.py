"""Agent 可调用的本地审计工具。

工具只做三件事：读取本次运行快照、调用现有本地算法、把结果整理成模型可读的 JSON。
文件写入始终使用运行时确认弹窗选定的路径；工具参数里不接受模型自造的输出路径。
"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path

from . import ai, audit_index, dxn, dxn_paper, fields, matching, paper_pack, papers, skills, workbook
from .agent import AgentContext, AgentError, ToolRegistry, ToolSpec
from .models import ledger_digest, money


def _revision(context):
    # 只给模型回传摘要哈希，不把本机绝对路径带进模型上下文。
    snapshot = context.snapshot
    return [snapshot.app_revision, snapshot.ledger_digest, snapshot.template_digest,
            snapshot.results_digest]


def _amount(value):
    return None if value is None else str(value)


def _ok(context, summary, data=None, warnings=()):
    return {"ok": True, "summary": str(summary)[:600], "data": data or {},
            "warnings": [str(item)[:600] for item in warnings if str(item).strip()],
            "revision": _revision(context)}


def _error(summary):
    return {"ok": False, "error": str(summary)[:600]}


def _ensure_scan(context):
    if context.scan is not None:
        return context.scan
    scan = fields.scan_template(context.template_path, context.periods, context.unit)
    workbook.validate_source_context(scan, [], context.rows)
    scan.source_digest = ledger_digest(context.rows)
    context.scan = scan
    context.results = matching.match_fields(scan.fields, list(context.rows))
    return scan


def _counts(results):
    counts = {}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts


def _pending(context, limit=30):
    items = [item for item in context.results if matching.tb_review_item(item, context.rows)]
    return [{"target_id": item.target.id, "label": item.target.label, "parent": item.target.parent,
             "period": item.target.period, "status": item.status,
             "existing": None if item.target.is_formula else _amount(item.target.value),
             "amount": _amount(item.amount), "difference": _amount(item.difference),
             "reason": item.reason[:300]} for item in items[:limit]], len(items)


def _account_fact(row):
    return {"code": row.code, "name": row.name, "path": row.path, "direction": row.direction,
            "level": row.level, "opening": _amount(row.opening), "debit": _amount(row.debit),
            "credit": _amount(row.credit), "ending": _amount(row.ending),
            "is_profit_loss": bool(row.is_profit_loss)}


def get_project_context(context, arguments, runtime):
    scan = context.scan
    pending, pending_total = _pending(context, 5)
    return _ok(context, f"{context.subject_label}：科目 {len(context.rows)} 个，字段 {len(context.results)} 个，待处理 {pending_total} 项。", {
        "project": context.summary(),
        "has_template": bool(context.template_path),
        "has_scan": scan is not None,
        "unit": context.unit,
        "periods": list(context.periods),
        "counts": _counts(context.results),
        "pending": pending,
        "artifacts": [{"kind": item.get("kind", ""), "name": item.get("name", ""),
                       "target": item.get("target", ""), "sha256": item.get("sha256", "")}
                      for item in context.artifacts],
        "tb_exported": context.tb_exported,
        "notes_ready": context.notes_ready,
    })


def search_accounts(context, arguments, runtime):
    query = str(arguments.get("query") or "").strip().casefold()
    limit = max(1, min(int(arguments.get("limit", 20)), 50))
    level = arguments.get("level")
    rows = [row for row in context.rows if level is None or row.level == int(level)]
    if query:
        rows = [row for row in rows if query in f"{row.code} {row.name} {row.path}".casefold()]
    rows = rows[:limit]
    return _ok(context, f"匹配到 {len(rows)} 个科目。", {
        "accounts": [_account_fact(row) for row in rows],
        "limit": limit,
        "note": "只返回本次运行快照中的科目；未列出的科目仍可能存在，不代表零余额。",
    })


def get_account_facts(context, arguments, runtime):
    codes = arguments.get("codes") or []
    if not isinstance(codes, list) or not 1 <= len(codes) <= 10:
        raise AgentError("每次最多查询 10 个科目编码")
    facts = []
    missing = []
    for code in codes:
        row = context.find_row(str(code))
        if row is None:
            missing.append(str(code))
            continue
        prefix = f"{row.path}/" if row.path else ""
        children = [item for item in context.rows if prefix and item.path.startswith(prefix)][:20]
        related = [item for item in context.rows if item.code != row.code
                   and row.name and row.name in item.name][:10]
        facts.append({**_account_fact(row), "children": [_account_fact(item) for item in children],
                      "similarly_named": [{"code": item.code, "name": item.name,
                                           "ending": _amount(item.ending)} for item in related]})
    return _ok(context, f"返回 {len(facts)} 个科目的本地事实。",
               {"accounts": facts, "missing": missing})


def scan_template(context, arguments, runtime):
    context.check_cancel()
    scan = _ensure_scan(context)
    warnings = list(getattr(scan, "warnings", ()) or ())
    return _ok(context, f"模板扫描完成：{len(context.results)} 个字段。", {
        "template": Path(scan.path).name,
        "unit": scan.unit,
        "counts": _counts(context.results),
        "warnings": warnings,
        "note": "扫描与规则匹配均在本地完成；金额未发送给模型。",
    }, warnings)


def get_tb_status(context, arguments, runtime):
    limit = max(1, min(int(arguments.get("limit", 30)), 100))
    pending, pending_total = _pending(context, limit)
    counts = _counts(context.results)
    differences = [item for item in context.results if item.difference not in (None, 0)]
    return _ok(context, f"字段 {len(context.results)} 个，待处理 {pending_total} 项。", {
        "counts": counts,
        "pending": pending,
        "pending_total": pending_total,
        "differences": len(differences),
        "selected": sum(1 for item in context.results if item.selected),
        "protected": counts.get("protected", 0),
        "note": "待处理包含未匹配、歧义和缺少依据的字段；金额由本地计算。",
    })


def run_tb_ai_fill(context, arguments, runtime):
    _ensure_scan(context)
    batch_size = max(1, min(int(arguments.get("batch_size", 12)), 20))
    max_batches = max(1, min(int(arguments.get("max_batches", 8)), 20))
    batches = ai.tb_draft_batches(context.results, context.rows, batch_size=batch_size)
    if not batches:
        return _ok(context, "没有需要模型补配的字段。", {"batches": 0, "applied": 0})
    applied_total, notes, warnings = 0, [], []
    for index, batch in enumerate(batches[:max_batches], 1):
        context.check_cancel()
        context.ensure_current()
        context.spend_model_call(["tb", "ledger"])
        runtime._audit("agent_model_request", source="run_tb_ai_fill",
                       scope=["tb", "ledger"], messages=len(batch.get("targets") or ()))
        try:
            decisions = ai.review(runtime.client, context.results, context.rows,
                                  payload=batch, include_unconfirmed=True)
        except ai.AIError as exc:
            warnings.append(f"第 {index} 批未完成：{exc}")
            break
        eligible = [item for item in context.results if item.status in {"ambiguous", "unmatched"}]
        applied, batch_notes = matching.apply_ai_suggestions(
            eligible, [item for item in decisions if item.get("action") == "fill"], context.rows)
        matching.select_tb_writes(context.results)
        matching.validate_claims(context.results)
        accepted = {item.target.id: item for item in eligible
                    if item.selected and item.status == "matched"}
        suggestions = {item["target_id"]: item.get("unconfirmed_codes") for item in decisions
                       if item.get("unconfirmed_codes")}
        names = {row.code: row.name for row in context.rows}
        for item in context.results:
            codes = suggestions.get(item.target.id)
            if codes and item.status in {"ambiguous", "unmatched"}:
                item.reason = "AI 建议核对：" + "、".join(
                    f"{names.get(code, code)}（{code}）" for code in codes) + "；未填入"
        context.results = [accepted.get(item.target.id, item) for item in context.results]
        matching.select_tb_writes(context.results)
        applied_total += applied
        notes.extend(batch_notes)
    remaining = max(0, len(batches) - max_batches)
    if remaining:
        warnings.append(f"还有 {remaining} 批未处理，请分批继续。")
    return _ok(context, f"AI 补配完成：采纳 {applied_total} 项，待处理 {_pending(context, 1)[1]} 项。", {
        "batches": min(len(batches), max_batches),
        "applied": applied_total,
        "notes": notes[:10],
        "remaining_batches": remaining,
    }, warnings)


def stage_tb_sources(context, arguments, runtime):
    items = arguments.get("items") or []
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise AgentError("每次最多暂存 20 个字段的来源")
    applied_total, details, warnings = 0, [], []
    for item in items:
        if not isinstance(item, dict):
            details.append({"ok": False, "error": "条目格式不正确"})
            continue
        target_id = str(item.get("target_id") or "")
        codes = item.get("source_codes") or []
        if not isinstance(codes, list) or not codes or not all(isinstance(code, str) for code in codes):
            details.append({"target_id": target_id, "ok": False, "error": "来源科目格式不正确"})
            continue
        result = context.find_result(target_id)
        if result is None:
            details.append({"target_id": target_id, "ok": False, "error": "目标不在本次运行快照中"})
            continue
        applied, notes = matching.apply_ai_suggestions(
            [result], [{"target_id": target_id, "source_codes": codes, "reason": "Agent 指定来源"}],
            context.rows)
        matching.select_tb_writes(context.results)
        matching.validate_claims(context.results)
        applied_total += applied
        warnings.extend(notes)
        details.append({"target_id": target_id, "ok": applied == 1,
                        "status": result.status, "amount": _amount(result.amount),
                        "difference": _amount(result.difference),
                        "sources": list(result.source_ids),
                        "error": "" if applied == 1 else (notes[0] if notes else result.reason)})
    return _ok(context, f"暂存来源：采纳 {applied_total} 个字段。", {
        "applied": applied_total, "items": details}, warnings)


def _default_tb_path(context):
    base = context.output_dir or Path(context.template_path).parent
    stem = Path(context.template_path).stem if context.template_path else "TB"
    candidate = base / f"{stem}-Attesta副本.xlsx"
    if candidate.exists():
        candidate = base / f"{stem}-Attesta副本-{datetime.now():%Y%m%d-%H%M%S}.xlsx"
    return candidate


def _tb_approval_details(context, arguments):
    # 确认先于写入；若尚未扫描，先在本地补扫描，确保“将写入几个字段”与实际一致。
    if context.scan is None:
        try:
            _ensure_scan(context)
        except (OSError, ValueError) as exc:
            return (("工具", "export_tb_copy"), ("无法预先扫描模板", str(exc)[:300]))
    selected = sum(1 for item in context.results if item.selected)
    overwrite = sum(1 for item in context.results if item.selected and item.overwrite)
    pending = _pending(context, 1)[1]
    sample = [f"{item.target.label} = {_amount(item.amount)}（{'; '.join(item.source_ids)}）"
              for item in context.results if item.selected][:5]
    return (("将写入字段", f"{selected} 个；允许覆盖模板已有值 {overwrite} 个"),
            ("待处理字段", f"{pending} 项"),
            ("默认输出", str(_default_tb_path(context))),
            ("金额来源", "本地账套与模板公式保护；原模板不修改"),
            ("写入示例", "；".join(sample) or "无选中字段"))


def export_tb_copy(context, arguments, runtime):
    try:
        scan = _ensure_scan(context)
    except (OSError, ValueError) as exc:
        return _error(f"无法扫描模板，未写入：{exc}")
    context.ensure_current()
    selected = sum(1 for item in context.results if item.selected)
    has_confirmed = any(item.target.basis == "book" and item.status == "matched"
                        and item.difference is not None
                        and abs(item.difference) * item.target.unit_factor < money("0.005")
                        for item in context.results)
    if not selected and not has_confirmed:
        return _error("没有可写入的金额；请先完成匹配或指定来源。")
    path = Path(runtime.approved_path).resolve() if runtime.approved_path else _default_tb_path(context)
    if path == Path(scan.path).resolve():
        return _error("不能覆盖原始模板，请选择新文件。")
    if path.exists():
        return _error("输出文件已存在，请选择新的文件名。")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        saved = workbook.write_results(scan, context.results, path,
                                       rows=tuple(context.rows), allow_empty=has_confirmed,
                                       preserve_tb_balance=True)
        checks = workbook.tb_balance_checks(saved, scan)
    except workbook.TBBalanceRegression as exc:
        return _error(str(exc))
    except (OSError, ValueError, KeyError) as exc:
        return _error(f"未生成 Excel：{exc}")
    except Exception as exc:
        return _error(f"Excel 写入或重算失败：{str(exc)[:300]}")
    notes = workbook.tb_balance_summary(checks, context.results) if checks else ["重算后未识别到可核验的合计。"]
    gaps = matching.tb_ledger_gaps(context.results, context.rows)
    notes.extend(f"{'期初' if period == 'opening' else '期末'}非零科目未确认完整入表：{code} {name} {amount} 元"
                 for period, code, name, amount in gaps)
    unresolved = [item for item in context.results if matching.tb_review_item(item, context.rows)]
    report = saved.with_name(f"{saved.stem}-待处理.csv")
    try:
        workbook.export_report(report, unresolved, subject=context.subject_label,
                               unit=scan.unit, warnings=notes)
    except OSError as exc:
        notes.append(f"待处理清单未保存：{exc}")
        report = None
    context.tb_exported = True
    context.record_artifact("tb", saved, source=scan.source_digest or "",
                            note=f"写入 {selected} 个字段，待处理 {len(unresolved)} 项",
                            target="tb")
    if report is not None:
        context.record_artifact("tb_pending", report, source=scan.source_digest or "",
                                note="TB 待处理清单", target="tb_pending")
    return _ok(context, f"已生成 TB 副本：{saved.name}；待处理 {len(unresolved)} 项。", {
        "output": saved.name, "pending_report": report.name if report else "",
        "selected": selected, "unresolved": len(unresolved), "gaps": len(gaps),
        "balance": notes[:10],
    }, notes)


def list_report_projects(context, arguments, runtime):
    limit = max(1, min(int(arguments.get("limit", 100)), 200))
    names = {row.code: row.name for row in context.rows if getattr(row, "code", "")}
    raw = papers.account_choices(context.rows, level=papers.PRIMARY_LEVEL, limit=None)
    grouped, unmapped = {}, []
    for code, _label in raw:
        paper, basis = audit_index.resolve(code, names.get(code, ""), context.paper_library)
        if paper is None:
            unmapped.append({"code": code, "name": names.get(code, "")})
            continue
        grouped.setdefault(paper.index_no, {"paper": paper, "codes": [], "basis": basis})
        if code not in grouped[paper.index_no]["codes"]:
            grouped[paper.index_no]["codes"].append(code)
    projects = []
    for index_no in [paper.index_no for paper in audit_index.options(context.paper_library)]:
        entry = grouped.get(index_no)
        if not entry:
            continue
        paper = entry["paper"]
        try:
            accounts, _scoped = papers.report_scope(context.rows, paper, context.paper_library)
        except ValueError as exc:
            projects.append({"index_no": index_no, "name": paper.name, "error": str(exc)})
            continue
        is_pl = index_no.startswith(("4-", "5-")) or any(row.is_profit_loss for row in accounts)
        total = sum((row.period_amount if is_pl else row.ending) or money("0") for row in accounts)
        projects.append({"index_no": index_no, "name": paper.name, "label": paper.label,
                         "is_profit_loss": is_pl, "total": str(total),
                         "accounts": [{"code": row.code, "name": row.name,
                                       "ending": _amount(row.ending),
                                       "period_amount": _amount(row.period_amount)}
                                      for row in accounts],
                         "mapped_basis": entry["basis"]})
        if len(projects) >= limit:
            break
    return _ok(context, f"识别到 {len(projects)} 个可出册报表项目。", {
        "projects": projects, "unmapped": unmapped[:30],
        "note": "未映射项目不会静默套用底稿；请人工确认归属。"})


def _validate_paper_data(context, data, accounts):
    keys = ("code", "name", "path", "direction", "opening", "ending", "debit", "credit", "level",
            "subject_id", "year", "month", "basis", "ledger_id")
    expected = sorted(tuple(getattr(row, key) for key in keys) for row in accounts)
    actual = sorted(tuple(getattr(row, key) for key in keys) for row in data.rows)
    if expected != actual:
        raise AgentError("账套来源与本次运行已复核的科目余额不一致，请重新读取后再制作底稿。")


def prepare_workpaper(context, arguments, runtime):
    index_no = str(arguments.get("index_no") or "").strip()
    paper = audit_index.find(context.paper_library, index_no)
    if paper is None:
        raise AgentError("未找到该报表项目索引")
    if context.subject is None:
        raise AgentError("缺少主体与期间，请先读取科目余额")
    accounts, scoped = papers.report_scope(context.rows, paper, context.paper_library)
    if not accounts:
        raise AgentError("该报表项目没有可列示的一级科目")
    try:
        # 与桌面底稿一致：只把报表项目的根科目交给取数层，由它读取完整子树；
        # 把全部明细编码传进去会被当成多个根，遇到非纯数字子编码时会误报科目编码不合法。
        data = dxn.read_paper_data(context.subject, tuple(row.code for row in accounts),
                                   context.install_path, with_journal=True)
        _validate_paper_data(context, data, scoped)
        if data.journal is None:
            return _error("未取得序时账，无法结合凭证生成审计说明；请先核对账套来源。")
        context.spend_model_call(["workpaper", "journal", "knowledge"])
        runtime._audit("agent_model_request", source="prepare_workpaper",
                       scope=["workpaper", "journal", "knowledge"], messages=1)
        texts = [item.text for item in paper_pack.procedures(paper.index_no, paper.name)]
        focus = str(arguments.get("focus") or context.focus or "")[:500]
        request = papers.pack_request(rows=data.rows, paper=paper, procedures=texts,
                                      subject_label=context.subject_label, focus=focus,
                                      journal=data.journal, library=context.paper_library,
                                      paper_data=data)
    except (OSError, ValueError) as exc:
        return _error(f"无法准备底稿：{exc}")
    warnings = []
    try:
        answer = runtime.client.paper_text(request)
        draft = papers.pack_result(answer, request)
    except papers.UnverifiedNarrativeError as exc:
        draft = {"kind": "local"}
        warnings.append(f"AI 文字未通过核对，仅保留本地事实：{exc}")
    except ai.AIError as exc:
        draft = {"kind": "local"}
        warnings.append(f"AI 文字未生成，仅保留本地事实：{exc}")
    except ValueError as exc:
        draft = {"kind": "local"}
        warnings.append(f"AI 文字无法解析，仅保留本地事实：{str(exc)[:200]}")
    context.prepared_workpapers[index_no] = {"paper": paper, "data": data, "draft": draft,
                                             "request": request, "warnings": warnings}
    has_narrative = draft.get("kind") == "pack"
    return _ok(context, f"底稿草稿已准备：{paper.label}，含 {len(accounts)} 个科目。", {
        "index_no": index_no, "name": paper.name, "accounts": len(accounts),
        "has_narrative": has_narrative,
        "analysis_missing": draft.get("analysis_missing") or [],
        "validation_warnings": (draft.get("validation_warnings") or [])[:10],
    }, warnings + list(draft.get("validation_warnings") or [])) 


def _default_workpaper_path(context, paper):
    if context.output_dir:
        base = context.output_dir
    elif context.template_path:
        base = Path(context.template_path).parent / "底稿"
    else:
        project = str(getattr(context.subject, "project_path", "") or "")
        base = (Path(project).parent if project else Path.cwd()) / "Attesta底稿"
    candidate = base / f"{paper.index_no} {paper.name}-底稿.xlsx"
    if candidate.exists():
        candidate = base / f"{paper.index_no} {paper.name}-底稿-{datetime.now():%Y%m%d-%H%M%S}.xlsx"
    return candidate


def _workpaper_approval_details(context, arguments):
    index_no = str(arguments.get("index_no") or "")
    prepared = context.prepared_workpapers.get(index_no) or {}
    paper = prepared.get("paper")
    evidence = context.evidence_rows.get(index_no) if context.evidence_rows else None
    if evidence is None and callable(context.evidence_provider):
        try:
            evidence = tuple(context.evidence_provider(index_no) or ())
        except Exception:
            evidence = ()
    missing = "未关联已确认证据" if not evidence else f"{len(evidence)} 条已确认证据"
    return (("报表项目", getattr(paper, "label", index_no)),
            ("模型文字", "含" if (prepared.get("draft") or {}).get("kind") == "pack" else "仅本地事实"),
            ("证据", missing),
            ("默认输出", str(_default_workpaper_path(context, paper)) if paper else ""),
            ("金额来源", "本机账套与序时账；原文件不修改"))


def export_workpaper(context, arguments, runtime):
    index_no = str(arguments.get("index_no") or "").strip()
    prepared = context.prepared_workpapers.get(index_no)
    if prepared is None:
        raise AgentError("请先调用 prepare_workpaper 准备该报表项目")
    context.ensure_current()
    paper, data, draft = prepared["paper"], prepared["data"], prepared["draft"]
    try:
        if getattr(data, "source_path", ""):
            if not dxn.validate_paper_data_source(data):
                gap = "账套来源已变化或一致副本失效，底稿未导出，请重新准备该报表项目。"
                context.add_gap(gap)
                return _error(gap)
    except (OSError, ValueError) as exc:
        return _error(f"无法重验账套来源：{exc}")
    path = Path(runtime.approved_path).resolve() if runtime.approved_path else _default_workpaper_path(context, paper)
    if path.exists():
        return _error("底稿输出文件已存在，请选择新的文件名。")
    narrative = draft if draft.get("kind") == "pack" else {}
    evidence = context.evidence_rows.get(index_no)
    if evidence is None and callable(context.evidence_provider):
        try:
            evidence = tuple(context.evidence_provider(index_no) or ())
        except Exception:
            evidence = ()
    evidence = tuple(evidence or ())
    warnings = list(prepared.get("warnings") or [])
    if not evidence and paper.index_no == "1-010":
        warning = "货币资金未关联已确认的银行回函/对账单，底稿证据页需项目组补充。"
        context.add_gap(warning)
        warnings.append(warning)
    temporary = path.with_name(f".attesta-{paper.index_no}-{datetime.now():%H%M%S%f}.xlsx")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        dxn_paper.build(
            temporary, paper=paper, subject_label=context.subject_label, rows=data.rows,
            resolve=lambda code, name: audit_index.resolve(
                code, name, context.paper_library)[0],
            prepared_by=context.prepared_by, reviewed_by=context.reviewed_by,
            when=datetime.now().strftime("%Y-%m-%d"),
            procedures=paper_pack.procedures(paper.index_no, paper.name),
            narrative=narrative, journal=data.journal, paper_data=data, evidence_rows=evidence)
        os.replace(temporary, path)
        output = path
    except (OSError, ValueError, KeyError) as exc:
        return _error(f"未生成底稿：{exc}")
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    context.record_artifact("workpaper", output, source=str(data.source_fingerprint)[:400],
                            note=f"{paper.index_no} {paper.name}；{'含' if narrative else '未含'}模型文字；"
                                 f"证据 {len(evidence)} 条",
                            target=index_no)
    return _ok(context, f"已生成整册底稿：{output.name}。", {
        "output": output.name, "index_no": index_no,
        "has_narrative": bool(narrative), "evidence": len(evidence),
    }, warnings)


def search_knowledge(context, arguments, runtime):
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise AgentError("检索词不能为空")
    limit = max(1, min(int(arguments.get("limit", 5)), 5))
    hits = skills.search(query, limit=limit)
    refs = skills.as_knowledge_refs(hits, limit=limit)
    return _ok(context, f"本地知识库命中 {len(refs)} 条。", {
        "references": refs,
        "note": "仅本地内置知识；不是项目审计证据，也不代表已取得完整原文。",
    })


def ask_user(context, arguments, runtime):
    question = str(arguments.get("question") or "").strip()
    if not question:
        raise AgentError("问题不能为空")
    options = arguments.get("options") or []
    if not isinstance(options, list):
        raise AgentError("选项格式不正确")
    answer = runtime._ask_user(question, tuple(str(item) for item in options))
    return _ok(context, "用户已答复。", {"answer": answer})


def finish(context, arguments, runtime):
    return {"ok": True, "summary": "结束"}


def build_registry(*, include_papers=True):
    specs = [
        ToolSpec("get_project_context", "读取当前主体、期间、模板、字段和待处理摘要。",
                 {"type": "object", "properties": {}, "additionalProperties": False},
                 "read", get_project_context, lambda _args: "读取项目上下文",
                 result_scope=("ledger",)),
        ToolSpec("search_accounts", "按编码、名称或路径查找账套科目及余额。",
                 {"type": "object", "properties": {
                     "query": {"type": "string", "maxLength": 200},
                     "limit": {"type": "integer"},
                     "level": {"type": "integer"}}, "additionalProperties": False},
                 "read", search_accounts,
                 lambda args: f"查找科目：{args.get('query', '全部')}",
                 result_scope=("ledger",)),
        ToolSpec("get_account_facts", "读取指定科目的期初、借贷、期末、父子与同名科目事实。",
                 {"type": "object", "properties": {
                     "codes": {"type": "array", "items": {"type": "string"}}},
                     "required": ["codes"], "additionalProperties": False},
                 "read", get_account_facts,
                 lambda args: f"读取 {len(args.get('codes') or [])} 个科目事实",
                 result_scope=("ledger",)),
        ToolSpec("scan_template", "在本地扫描用户模板并执行规则匹配（不联网）。",
                 {"type": "object", "properties": {}, "additionalProperties": False},
                 "compute", scan_template, lambda _args: "扫描模板并匹配字段",
                 result_scope=("tb",)),
        ToolSpec("get_tb_status", "读取 TB 状态、待处理字段和差异摘要。",
                 {"type": "object", "properties": {"limit": {"type": "integer"}},
                  "additionalProperties": False},
                 "read", get_tb_status, lambda _args: "读取 TB 状态",
                 result_scope=("tb",)),
        ToolSpec("run_tb_ai_fill", "调用现有 AI 补配链路处理待确认字段；只暂存，不写文件。",
                 {"type": "object", "properties": {
                     "batch_size": {"type": "integer"}, "max_batches": {"type": "integer"}},
                  "additionalProperties": False},
                 "model", run_tb_ai_fill, lambda _args: "AI 补配 TB 字段",
                 result_scope=("tb",)),
        ToolSpec("stage_tb_sources", "把指定来源暂存到 TB 字段，由本地重新计算金额。",
                 {"type": "object", "properties": {
                     "items": {"type": "array", "items": {"type": "object"}}},
                     "required": ["items"], "additionalProperties": False},
                 "compute", stage_tb_sources,
                 lambda args: f"暂存 {len(args.get('items') or [])} 个字段来源",
                 result_scope=("tb",)),
        ToolSpec("export_tb_copy", "另存 TB 副本、重算并导出待处理清单（需用户确认）。",
                 {"type": "object", "properties": {}, "additionalProperties": False},
                 "write", export_tb_copy, lambda _args: "另存 TB 副本",
                 approval_details=_tb_approval_details),
        ToolSpec("search_knowledge", "检索本地内置财会知识，返回可引用的 K 编号。",
                 {"type": "object", "properties": {
                     "query": {"type": "string", "maxLength": 500},
                     "limit": {"type": "integer"}}, "required": ["query"],
                  "additionalProperties": False},
                 "read", search_knowledge,
                 lambda args: f"检索知识：{args.get('query', '')[:60]}",
                 result_scope=("knowledge",)),
        ToolSpec("ask_user", "遇到重大歧义时暂停并向用户提问。",
                 {"type": "object", "properties": {
                     "question": {"type": "string", "maxLength": 2000},
                     "options": {"type": "array", "items": {"type": "string"}}},
                     "required": ["question"], "additionalProperties": False},
                 "user", ask_user, lambda args: f"询问用户：{args.get('question', '')[:80]}"),
        ToolSpec("finish", "结束本次运行并报告完成情况与缺口。",
                 {"type": "object", "properties": {
                     "status": {"type": "string"}, "summary": {"type": "string"},
                     "deliverables": {"type": "array", "items": {"type": "string"}},
                     "gaps": {"type": "array", "items": {"type": "string"}}},
                     "required": ["status", "summary"], "additionalProperties": False},
                 "user", finish, lambda _args: "结束运行"),
    ]
    if include_papers:
        specs.extend([
            ToolSpec("list_report_projects", "列出可出册的报表项目、索引号和包含科目。",
                     {"type": "object", "properties": {"limit": {"type": "integer"}},
                      "additionalProperties": False},
                     "read", list_report_projects, lambda _args: "列出报表项目",
                     result_scope=("ledger", "tb")),
            ToolSpec("prepare_workpaper", "读取项目科目与序时账，生成整册底稿文字草稿。",
                     {"type": "object", "properties": {
                         "index_no": {"type": "string", "maxLength": 20},
                         "focus": {"type": "string", "maxLength": 500}},
                      "required": ["index_no"], "additionalProperties": False},
                     "model", prepare_workpaper,
                     lambda args: f"准备底稿：{args.get('index_no', '')}",
                     result_scope=("workpaper", "journal")),
            ToolSpec("export_workpaper", "导出已准备的整册底稿 Excel（需用户确认）。",
                     {"type": "object", "properties": {
                         "index_no": {"type": "string", "maxLength": 20}},
                      "required": ["index_no"], "additionalProperties": False},
                     "write", export_workpaper,
                     lambda args: f"导出底稿：{args.get('index_no', '')}",
                     approval_details=_workpaper_approval_details),
        ])
    return ToolRegistry(specs)
