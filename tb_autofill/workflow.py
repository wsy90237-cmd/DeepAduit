"""Readiness is project state, never an automatically issued audit opinion."""

from datetime import datetime
from html import escape
from pathlib import Path
from . import __version__


def readiness(rows, results, subject=None, scan=None, references=()):
    pending = sum(result.status not in {"matched", "protected"} for result in results)
    differences = sum(result.difference not in (None, 0) for result in results)
    checks = [
        {"item": "主体与报告期间", "status": "已有账套期间" if subject else "待确认",
         "detail": subject.label if subject else "先选择主体、年度和截止期间。"},
        {"item": "科目余额与 TB", "status": "可继续复核" if rows and results else "待导入",
         "detail": f"科目 {len(rows)} 个，目标字段 {len(results)} 个，未明确匹配 {pending} 项，已有值差异 {differences} 项。匹配不等于人工审定。"},
        {"item": "风险评估与审计计划", "status": "待实施",
         "detail": "需项目背景、重要性及重大错报风险评估；完整风险引擎尚未实现。"},
        {"item": "程序执行与证据", "status": "待补齐",
         "detail": "当前只读取科目余额；序时账、凭证、函证、监盘等证据需另行取得和复核。"},
        {"item": "财务报表与附注", "status": "可辅助复核 / 生成待接模板",
         "detail": "可按所选知识复核文字；完整附注生成、勾稽与模板导出尚未实现。"},
        {"item": "底稿与复核记录", "status": "可辅助复核 / 管理待完善",
         "detail": "可复核所选底稿文字；程序执行、证据关联及多级复核尚待实现。"},
        {"item": "知识来源", "status": "已选用片段" if references else "待选择",
         "detail": f"本次选用 {len(references)} 个片段；原文、适用范围和生效日期仍需核对。"},
        {"item": "报告组装与签发", "status": "未就绪",
         "detail": "报告模板、意见判断与签发流程尚未实现，不提供自动签发。"}]
    if subject and subject.month != 12:
        checks[0]["detail"] += "；截止非 12 月，请确认与报告期间一致。"
    return {"engagement": "企业年度财务报表审计", "ready_to_issue": False,
            "source_digest": scan.source_digest if scan else "", "checks": checks,
            "reference_titles": [str(item.get("title", "")) for item in references]}


def export_readiness(path, state):
    path = Path(path)
    if path.suffix.lower() != ".html":
        raise ValueError("请选择 HTML 文件。")
    rows = "".join("<tr>" + "".join(f"<td>{escape(str(item[key]))}</td>" for key in ("item", "status", "detail")) + "</tr>"
                   for item in state["checks"])
    references = "".join(f"<li>{escape(title)}</li>" for title in state["reference_titles"]) or "<li>未选用</li>"
    page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>DeepAudit 报告准备清单</title>'
            '<style>body{font:15px system-ui;max-width:1100px;margin:40px auto;color:#18263b;padding:20px}'
            'h1{color:#087f8c}table{border-collapse:collapse;width:100%}td,th{border:1px solid #dbe3ec;padding:14px;text-align:left}'
            'th{background:#f3f6fa}.note{padding:16px;background:#fff6df}small{color:#68778d}</style>'
            f'<h1>DeepAudit · 报告准备清单</h1><p>{escape(state["engagement"])}</p>'
            '<p class="note">本文件仅反映资料与功能准备状态，不是审计意见或已签发审计报告。</p>'
            f'<table><tr><th>环节</th><th>状态</th><th>依据 / 待办</th></tr>{rows}</table>'
            f'<h2>本次参考资料</h2><ul>{references}</ul>'
            f'<p><small>DeepAudit {__version__} · {datetime.now().astimezone().isoformat(timespec="seconds")}</small></p></html>')
    with path.open("x", encoding="utf-8") as stream:
        stream.write(page)
    return path
