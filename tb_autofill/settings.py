import base64
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit, urlunsplit


# 离线兜底候选：真实可用模型以账户 /models 返回为准，界面会动态刷新。
# 下列取值经真实接口验证：账户 /models 返回 deepseek-flash 与 deepseek-v4-pro。
MODELS = ("deepseek-flash", "deepseek-v4-pro")
MAX_MODEL_LENGTH = 64
DEFAULT_BASE_URL = "https://api.deepseek.com"


def valid_model(model):
    return isinstance(model, str) and 0 < len(model.strip()) <= MAX_MODEL_LENGTH


class SettingsError(Exception):
    pass


def normalize_base_url(value):
    value = str(value or "").strip().rstrip("/")
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or "\\" in value
                or any(ord(c) < 33 or ord(c) > 126 for c in value)):
            raise ValueError()
        _port = parts.port
    except ValueError:
        raise SettingsError("API 地址须为 HTTPS，且不能包含账号、密码、查询参数或片段。") from None
    return urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def validate_key(value):
    value = str(value or "").strip()
    if not value or len(value) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise SettingsError("API Key 不能为空、含空白或控制字符，长度最多 4096 个字符。")
    return value


def _seal(text):
    import win32crypt
    return base64.b64encode(win32crypt.CryptProtectData(text.encode("utf-8"), "DeepAudit credential", None, None, None, 1)).decode("ascii")


def _unseal(text):
    import win32crypt
    return win32crypt.CryptUnprotectData(base64.b64decode(text, validate=True), None, None, None, 1)[1].decode("utf-8")


class SettingsStore:
    def __init__(self, path=None):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ZSZH-TB"
        self.path = Path(path) if path else base / "connections.json"
        source = self.path
        if path is None and not source.exists() and (base / "settings.json").exists():
            source = base / "settings.json"
        self.data = {"model": MODELS[0], "ai_enabled": False, "onboarding_done": False,
                     "base_url": DEFAULT_BASE_URL, "key_base_url": DEFAULT_BASE_URL,
                     "last_connection_check": "", "prepared_by": "", "reviewed_by": ""}
        if source.exists():
            try:
                saved = json.loads(source.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    raise ValueError("设置文件格式不正确")
                self.data.update({k: saved[k] for k in self.data if k in saved})
                if isinstance(saved.get("key_dpapi"), str):
                    self.data["key_dpapi"] = saved["key_dpapi"]
                if isinstance(saved.get("ima_dpapi"), str):
                    self.data["ima_dpapi"] = saved["ima_dpapi"]
                self.data["base_url"] = normalize_base_url(self.data["base_url"])
                self.data["key_base_url"] = normalize_base_url(self.data["key_base_url"])
                self.data["ai_enabled"] = self.data["ai_enabled"] is True
                for name in ("prepared_by", "reviewed_by"):
                    value = self.data.get(name)
                    self.data[name] = value.strip()[:40] if isinstance(value, str) else ""
                if self.data["model"] not in MODELS and not valid_model(self.data["model"]):
                    self.data["model"] = MODELS[0]
            except (OSError, ValueError) as exc:
                raise SettingsError("无法读取本机设置，请备份并移走损坏的设置文件后重试。") from exc

    def _write(self):
        handle = None
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(prefix=".deepaudit-settings-", dir=self.path.parent)
            temporary = Path(name)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                handle = None
                stream.write(json.dumps(self.data, ensure_ascii=False, indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise SettingsError("无法保存本机设置，请检查用户目录的写入权限。") from exc
        finally:
            if handle is not None:
                os.close(handle)
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    @property
    def has_key(self):
        return bool(self.data.get("key_dpapi"))

    def get_key(self, base_url=None):
        if not self.has_key:
            return ""
        requested = normalize_base_url(base_url or self.data["base_url"])
        if requested != self.data["key_base_url"]:
            raise SettingsError("接口地址已更改，不能复用原服务的 Key；请为新地址重新输入 Key。")
        try:
            return _unseal(self.data["key_dpapi"])
        except Exception as exc:
            raise SettingsError("当前 Windows 用户无法解密 Key，请在 AI 设置中重新输入。") from exc

    def save_ai(self, key=None, *, enabled=False, model=MODELS[0], base_url=None, verified_at=""):
        if not valid_model(model):
            raise SettingsError("模型 ID 不能为空，且最多 64 个字符。")
        model = model.strip()
        endpoint = normalize_base_url(base_url or self.data["base_url"])
        if key is None and self.has_key and endpoint != self.data["key_base_url"]:
            raise SettingsError("切换 API 地址时必须重新输入对应服务的 Key，原 Key 不会转发。")
        updated = dict(self.data)
        if key is not None:
            key = validate_key(key)
            try:
                updated["key_dpapi"] = _seal(key)
                updated["key_base_url"] = endpoint
            except Exception as exc:
                raise SettingsError("Windows 加密失败，未保存 Key。") from exc
        if enabled and not updated.get("key_dpapi"):
            raise SettingsError("启用 AI 前请先输入自己的 API Key")
        updated.update(ai_enabled=bool(enabled), model=model, onboarding_done=True,
                       base_url=endpoint, last_connection_check=str(verified_at)[:64])
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
            raise SettingsError("模型 ID 不能为空，且最多 64 个字符。")
        previous = self.data
        self.data = dict(previous, model=model.strip())
        try:
            self._write()
        except SettingsError:
            self.data = previous
            raise

    def save_signers(self, prepared_by="", reviewed_by=""):
        """保存底稿签字人：只存姓名，不做任何校验强度声明。"""
        cleaned = {}
        for name, value in (("prepared_by", prepared_by), ("reviewed_by", reviewed_by)):
            text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
            if any(ord(char) < 32 for char in text):
                raise SettingsError("编制人 / 复核人不能包含控制字符。")
            cleaned[name] = text[:40]
        previous = self.data
        self.data = dict(previous, **cleaned)
        try:
            self._write()
        except SettingsError:
            self.data = previous
            raise
        return cleaned

    def delete_key(self):
        previous = dict(self.data)
        self.data.pop("key_dpapi", None)
        self.data.update(ai_enabled=False, onboarding_done=True, last_connection_check="")
        try:
            self._write()
        except SettingsError:
            self.data = previous
            raise

    def skip_onboarding(self):
        self.data["onboarding_done"] = True
        self._write()

    @property
    def has_ima(self):
        return bool(self.data.get("ima_dpapi"))

    def get_ima(self):
        if not self.has_ima:
            raise SettingsError("请在知识库连接中配置 ima Client ID 与 API Key。")
        try:
            value = json.loads(_unseal(self.data["ima_dpapi"]))
            return value["client_id"], value["api_key"]
        except Exception:
            raise SettingsError("当前 Windows 用户无法解密 ima 凭证，请重新配置。") from None

    def save_ima(self, client_id, api_key):
        client_id, api_key = validate_key(client_id), validate_key(api_key)
        previous = self.data
        try:
            sealed = _seal(json.dumps({"client_id": client_id, "api_key": api_key}))
            self.data = dict(previous, ima_dpapi=sealed)
            self._write()
        except Exception as exc:
            self.data = previous
            raise SettingsError("ima 凭证未保存，请检查 Windows 加密及本机目录权限。") from exc

    def delete_ima(self):
        previous = self.data
        self.data = {k: v for k, v in previous.items() if k != "ima_dpapi"}
        try:
            self._write()
        except SettingsError:
            self.data = previous
            raise
