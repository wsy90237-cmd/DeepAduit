import base64
import json
import os
from pathlib import Path


# 离线兜底候选：真实可用模型以账户 /models 返回为准，界面会动态刷新。
# 下列取值经真实接口验证：账户 /models 返回 deepseek-flash 与 deepseek-v4-pro。
MODELS = ("deepseek-flash", "deepseek-v4-pro")
MAX_MODEL_LENGTH = 64


def valid_model(model):
    return isinstance(model, str) and 0 < len(model.strip()) <= MAX_MODEL_LENGTH


class SettingsError(Exception):
    pass


class SettingsStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ZSZH-TB" / "settings.json"
        self.data = {"model": MODELS[0], "ai_enabled": False, "onboarding_done": False}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    raise ValueError("设置文件格式不正确")
                self.data.update({k: saved[k] for k in self.data if k in saved})
                if isinstance(saved.get("key_dpapi"), str):
                    self.data["key_dpapi"] = saved["key_dpapi"]
                if self.data["model"] not in MODELS and not valid_model(self.data["model"]):
                    self.data["model"] = MODELS[0]
            except (OSError, ValueError) as exc:
                raise SettingsError("无法读取本机设置，请备份并移走损坏的设置文件后重试。") from exc

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as exc:
            raise SettingsError("无法保存本机设置，请检查用户目录的写入权限。") from exc

    @property
    def has_key(self):
        return bool(self.data.get("key_dpapi"))

    def get_key(self):
        if not self.has_key:
            return ""
        try:
            import win32crypt
            encrypted = base64.b64decode(self.data["key_dpapi"], validate=True)
            return win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1].decode("utf-8")
        except Exception as exc:
            raise SettingsError("当前 Windows 用户无法解密 Key，请在 AI 设置中重新输入。") from exc

    def save_ai(self, key=None, *, enabled=False, model=MODELS[0]):
        if not valid_model(model):
            raise SettingsError("请选择受支持的 DeepSeek 模型")
        model = model.strip()
        updated = dict(self.data)
        if key is not None:
            key = key.strip()
            if not key:
                raise SettingsError("API Key 不能为空；停用或删除请使用相应按钮。")
            try:
                import win32crypt
                encrypted = win32crypt.CryptProtectData(key.encode("utf-8"), "ZSZH-TB DeepSeek", None, None, None, 0)
                updated["key_dpapi"] = base64.b64encode(encrypted).decode("ascii")
            except Exception as exc:
                raise SettingsError("Windows 加密失败，未保存 Key。") from exc
        if enabled and not updated.get("key_dpapi"):
            raise SettingsError("启用 AI 前请先输入自己的 API Key")
        updated.update(ai_enabled=bool(enabled), model=model, onboarding_done=True)
        previous = self.data
        self.data = updated
        try:
            self._write()
        except SettingsError:
            self.data = previous
            raise

    def save_model(self, model):
        """只切换模型，不改变启用状态与已有 Key。"""
        if not valid_model(model):
            raise SettingsError("请选择受支持的 DeepSeek 模型")
        previous = self.data
        self.data = dict(previous, model=model.strip())
        try:
            self._write()
        except SettingsError:
            self.data = previous
            raise

    def delete_key(self):
        previous = dict(self.data)
        self.data.pop("key_dpapi", None)
        self.data.update(ai_enabled=False, onboarding_done=True)
        try:
            self._write()
        except SettingsError:
            self.data = previous
            raise

    def skip_onboarding(self):
        self.data["onboarding_done"] = True
        self._write()
