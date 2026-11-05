"""安装空白模板的结构目录；原插件公式不作为已执行的审计程序。"""

from functools import lru_cache
import json
from pathlib import Path
import re

ASSET = Path(__file__).with_name("assets") / "dxn-templates.json"
ALIASES = {"股本": ("实收资本", "实收资本(股本)"), "税金及附加": ("营业税金及附加",)}


def _name(value):
    return re.sub(r"[\s（）()－—_-]", "", str(value or ""))


@lru_cache(maxsize=1)
def catalog():
    data = json.loads(ASSET.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("底稿模板目录版本无法识别，请重新安装。")
    return data


def for_paper(name):
    target = _name(name)
    for candidate in (target, *(_name(value) for value in ALIASES.get(name, ()))):
        matches = [item for item in catalog()["templates"] if _name(item["name"]) == candidate]
        if len(matches) == 1:
            return matches[0]
    return None


def sheet_names(name):
    template = for_paper(name)
    if template is None:
        return []
    return [item["name"] for item in template["sheets"]
            if item["name"] != "dxnsjtempsheet"
            and not any(word in item["name"] for word in ("审定表", "明细表", "审计程序", "披露表", "识别出的风险"))]


def planning_context(name):
    template = for_paper(name)
    if template is None:
        return {"matched": False, "note": "未确认对应原模板，仅使用已实现的本地专项表。"}
    version = next((item.get("rows", []) for item in catalog()["configs"] if item["file"] == "Ver.txt"), [])
    return {"matched": True, "template": template["name"], "template_sha256": template["decoded_sha256"],
            "version": version[0][-1] if version else "未识别",
            "sheets": sheet_names(name),
            "note": "原模板表名用于规划；未知插件公式不执行，原模板参考表不代表审计程序已经完成。"}


def spec_for(title, paper_name):
    """按已提取的字段重建资料记录表；有专用实现的表由调用者优先选择。"""
    template = for_paper(paper_name)
    if template is None or title not in sheet_names(paper_name):
        return None
    sheet = next(item for item in template["sheets"] if item["name"] == title)
    by_row = {}
    for item in sheet.get("fields", []):
        match = re.fullmatch(r"[A-Z]+(\d+)", item["cell"])
        if match:
            by_row.setdefault(int(match.group(1)), []).append(item["text"])
    fields = max(by_row.values(), key=len, default=[])
    fields = list(dict.fromkeys(item for item in fields if not item.startswith("字符列")))
    if not fields:
        fields = ["检查事项", "资料来源", "项目记录", "复核意见"]
    columns = tuple((item, 22 if "名称" in item else 17,
                     '#,##0.00;[Red]-#,##0.00;"-"' if any(word in item for word in ("余额", "发生额", "调整数", "审定数", "账面数")) else "@")
                    for item in fields)
    note = (f"参考鼎信诺《{template['name']}》中的同名表重建记录栏目。"
            "本表需结合项目资料填写，未执行原软件插件公式，不表示已完成相应审计程序。")
    return title, columns, note, 12, None


def original_path(template, root):
    """只在用户已迁入的模板库内查找原件，防止目录路径越界。"""
    base = Path(root).resolve()
    target = (base / template["file"]).resolve()
    if not target.is_relative_to(base):
        raise ValueError("模板原件路径越界。")
    return target
