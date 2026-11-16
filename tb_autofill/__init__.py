"""鼎信诺 → 用户模板填列与复核。"""

__version__ = "0.7.0"
APP_NAME = "Attesta"

# 业务模块单一清单：原生编译门槛与离线自检都从这里取，避免两处清单漂移。
PRODUCT_MODULES = (
    "ai", "analysis", "audit_index", "auditlog", "brand", "connections", "dxn", "dxn_paper",
    "dxn_programs", "dxn_templates", "fields", "gui", "knowledge", "knowledge_ui", "matching",
    "models", "paper_materials", "paper_pack", "paper_sheets", "papers", "sampling", "settings",
    "skills", "theme", "workbook", "workflow", "workspace",
)
