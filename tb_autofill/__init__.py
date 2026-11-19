"""鼎信诺 → 用户模板填列与复核。"""

__version__ = "0.8.0"
APP_NAME = "Attesta"

# 业务模块单一清单：原生编译门槛与离线自检都从这里取，避免两处清单漂移。
PRODUCT_MODULES = (
    'auto_project', 'auto_intake_ui',
    "ai", "analysis", "audit_index", "auditlog", "brand", "connections", "dxn", "dxn_paper",
    "dxn_programs", "dxn_templates", "fields", "gui", "knowledge", "knowledge_ui", "matching",
    "models", "paper_materials", "paper_pack", "paper_sheets", "papers", "sampling", "settings",
    "skills", "theme", "workbook", "workflow", "workspace",
    "bank_evidence", "bank_evidence_ui", "bank_listing", "evidence_draft", "evidence_ownership", "evidence_project",
    "evidence_ui", "pdf_text",
)
