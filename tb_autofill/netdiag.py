"""模型服务连接诊断：把「DeepSeek 用不了」拆成可逐段核对的结论。

同事电脑上最常见的四类原因，这里的顺序就是排查顺序：

1. 代理 / PAC —— Windows 系统代理写在注册表，PAC（自动配置脚本）只有 WinHTTP 认，
   Python 的 urllib 只读环境变量与静态代理，走 PAC 的网络在程序里表现为「连接超时」。
2. DNS —— 域名解析失败或被解析到保留地址（198.18.0.0/15 之类）。
3. TCP / TLS —— 防火墙拦 443、公司代理做 TLS 中间人导致证书校验失败、系统时间不准。
4. 鉴权 / 配额 —— Key 无效、余额不足、所选模型未开通。

本模块只做只读探测，不写配置、不记录 Key；报告可整段复制给同事。
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
from datetime import datetime
from urllib.parse import urlsplit

CONNECT_TIMEOUT = 8.0
READ_TIMEOUT = 180.0
FAST_READ_TIMEOUT = 30.0
MAX_REASON = 160


class Finding:
    """一条诊断结论：检查项、是否通过、可读说明、下一步建议。"""

    def __init__(self, name, ok, detail, hint=""):
        self.name, self.ok, self.detail, self.hint = name, ok, detail, hint

    def line(self):
        mark = "通过" if self.ok else "异常"
        text = f"[{mark}] {self.name}：{self.detail}"
        if self.hint and not self.ok:
            text += f"\n        → {self.hint}"
        return text

    def as_dict(self):
        return {"check": self.name, "ok": self.ok, "detail": self.detail, "hint": self.hint}


def _short(text, limit=MAX_REASON):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def describe_failure(exc, host=""):
    """把底层网络异常翻译成「哪一段坏了 + 该做什么」。

    原实现把所有 OSError/URLError 都归到「无法连接或请求超时」，
    证书问题、DNS 劫持、代理没开因此都看不出区别，这是本次故障排查的直接障碍。
    """
    reason = getattr(exc, "reason", None) or exc
    text = _short(reason)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return (f"与 {host} 建立 TLS 时证书校验失败（{_short(getattr(reason, 'verify_message', text), 80)}）。"
                "常见原因：系统时间不准、公司代理替换了证书但根证书未装入系统，"
                "或 hosts/DNS 把域名指向了别的服务。请在「模型设置 → 网络诊断」查看证书详情。")
    if isinstance(reason, ssl.SSLError):
        return (f"与 {host} 的 TLS 握手被中断（{text}）。多为中间设备拦断 HTTPS，"
                "需要网络管理员放行 api.deepseek.com:443；也请确认代理软件已启动。")
    if isinstance(reason, socket.gaierror):
        return (f"域名 {host} 解析失败（{text}）。请检查 DNS 设置或代理的 fake-IP 模式；"
                "「网络诊断」会显示解析到的地址。")
    if isinstance(reason, ConnectionRefusedError):
        return (f"{host} 拒绝连接（{text}）。本地代理软件未启动、端口不对，或目标只允许经代理访问。")
    if isinstance(reason, (ConnectionResetError, ConnectionAbortedError)):
        return f"连接被 {host} 重置（{text}）。多为防火墙或代理规则拦截，可换网络或用手机热点复测。"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return (f"连接 {host} 超时（{text}）。网络到该地址不可达，或代理未生效；"
                "「模型设置 → 网络诊断」可逐段定位。")
    return f"无法连接 {host}（{text}）。请运行「模型设置 → 网络诊断」逐段核对网络、代理与证书。"


def endpoint_parts(base_url):
    """返回 (host, port, path)；端口缺省按 https 取 443。"""
    parts = urlsplit(str(base_url or ""))
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("接口地址须为 HTTPS 域名")
    return parts.hostname, parts.port or 443, parts.path.rstrip("/")


# ---------- 代理 ----------

def _winhttp_pac(url):
    """用 WinHTTP 解析 PAC（自动配置脚本），返回 (proxy, source) 或 (None, 原因)。"""
    if os.name != "nt":
        return None, "非 Windows 平台"
    try:
        import ctypes
        from ctypes import wintypes

        winhttp = ctypes.WinDLL("winhttp", use_last_error=True)

        class ProxyConfig(ctypes.Structure):
            _fields_ = [("fAutoDetect", wintypes.BOOL), ("lpszAutoConfigUrl", wintypes.LPWSTR),
                        ("lpszProxy", wintypes.LPWSTR), ("lpszProxyBypass", wintypes.LPWSTR)]

        class Options(ctypes.Structure):
            _fields_ = [("dwFlags", wintypes.DWORD), ("dwAutoDetectFlags", wintypes.DWORD),
                        ("lpszAutoConfigUrl", wintypes.LPCWSTR), ("lpvReserved", ctypes.c_void_p),
                        ("dwReserved", wintypes.DWORD), ("dwAutoLogonIfChallenged", wintypes.BOOL)]

        class ProxyInfo(ctypes.Structure):
            _fields_ = [("dwAccessType", wintypes.DWORD), ("lpszProxy", wintypes.LPWSTR),
                        ("lpszProxyBypass", wintypes.LPWSTR)]

        config = ProxyConfig()
        if not winhttp.WinHttpGetIEProxyConfigForCurrentUser(ctypes.byref(config)):
            return None, "读取系统代理设置失败"
        if not config.fAutoDetect and not config.lpszAutoConfigUrl:
            return None, "系统未启用 PAC 自动配置"
        options = Options()
        options.dwFlags = 0x1  # WINHTTP_AUTOPROXY_AUTO_DETECT
        options.dwAutoDetectFlags = 0x1 | 0x2  # DHCP | DNS
        options.dwAutoLogonIfChallenged = True
        if config.lpszAutoConfigUrl:
            options.dwFlags |= 0x2  # WINHTTP_AUTOPROXY_CONFIG_URL
            options.lpszAutoConfigUrl = config.lpszAutoConfigUrl
        session = winhttp.WinHttpOpen("Attesta-Diagnose", 1, None, None, 0)
        if not session:
            return None, "WinHTTP 初始化失败"
        try:
            info = ProxyInfo()
            if not winhttp.WinHttpGetProxyForUrl(session, str(url), ctypes.byref(options), ctypes.byref(info)):
                return None, "PAC 未返回该地址的代理"
            return (str(info.lpszProxy).split(";")[0].split()[0] if info.lpszProxy else None,
                    f"PAC {config.lpszAutoConfigUrl or '自动检测'}")
        finally:
            winhttp.WinHttpCloseHandle(session)
    except (AttributeError, OSError, ValueError) as exc:
        return None, f"PAC 解析不可用（{_short(exc, 60)}）"


def _normalize_proxy(value):
    """把 'http://127.0.0.1:7890' 规范化成 '127.0.0.1:7890'。"""
    text = str(value or "").strip()
    if not text or text.lower() in {"auto", "none", "direct"}:
        return ""
    if "://" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    if not parts.hostname:
        return ""
    return f"{parts.hostname}:{parts.port or (443 if parts.scheme == 'https' else 80)}"


def resolve_proxy(url):
    """返回 (proxy, source)。proxy 形如 'host:port'，无代理返回 ''。"""
    host = urlsplit(str(url)).hostname or ""
    # 1) 环境变量与注册表静态代理（urllib 的行为，保持一致）
    try:
        from urllib.request import getproxies

        proxies = getproxies()
    except (ImportError, OSError):
        proxies = {}
    for key in ("https", "http", "all"):
        candidate = _normalize_proxy(proxies.get(key))
        if candidate:
            return candidate, f"系统代理（{key.upper()}_PROXY）"
    # 2) PAC：urllib 不认，但公司电脑常用
    pac, source = _winhttp_pac(f"https://{host}/")
    candidate = _normalize_proxy(pac)
    if candidate:
        return candidate, source
    return "", source if source.startswith("PAC") else "直连"


# ---------- 各段检查 ----------

def check_time():
    now = datetime.now().astimezone()
    return Finding("系统时间", True, f"{now:%Y-%m-%d %H:%M:%S %Z}",
                   "证书有效期与系统时间强相关；时间偏差会让 TLS 校验失败。")


def check_proxy(url):
    """返回 (结论, 代理地址)；代理地址形如 'host:port'，直连为空串。"""
    proxy, source = resolve_proxy(url)
    if proxy:
        return Finding("网络代理", True, f"经由 {proxy}（{source}）",
                       "代理软件未启动或端口变更会表现为连接被拒绝。"), proxy
    return Finding("网络代理", True, f"直连（{source}）",
                   "如公司要求代理访问外网，需要在系统里配置代理或 PAC。"), ""


def check_dns(host):
    started = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return Finding("域名解析", False, f"{host} 解析失败（{_short(exc)}）",
                       "换一个 DNS（如 223.5.5.5）或检查代理的 fake-IP 设置。")
    addresses = sorted({item[4][0] for item in infos})
    cost = (time.perf_counter() - started) * 1000
    fake = [ip for ip in addresses if ip.startswith(("198.18.", "198.19."))]
    if fake:
        return Finding("域名解析", False, f"{host} → {', '.join(fake)}（{cost:.0f} ms）",
                       "解析到 198.18.0.0/15 是代理的 fake-IP；代理未运行或规则未命中时必然连不上。")
    return Finding("域名解析", True, f"{host} → {', '.join(addresses[:3])}（{cost:.0f} ms）")


def check_tcp(host, port):
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
            pass
    except (OSError, socket.timeout) as exc:
        return Finding("TCP 连接", False, f"{host}:{port} 失败（{_short(exc)}）",
                       "公司防火墙可能只放行代理端口；确认代理已启动、端口正确。")
    return Finding("TCP 连接", True, f"{host}:{port} 已连通（{(time.perf_counter() - started) * 1000:.0f} ms）")


def check_tls(host, port):
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert() or {}
    except ssl.SSLCertVerificationError as exc:
        return Finding("TLS 证书", False, f"证书校验未通过（{_short(getattr(exc, 'verify_message', exc))}）",
                       "常见原因：公司代理替换证书但根证书未装入系统、系统时间偏差、或用 hosts 指向了别的服务。")
    except (ssl.SSLError, OSError) as exc:
        return Finding("TLS 证书", False, f"TLS 握手失败（{_short(exc)}）",
                       "中间设备可能拦断了 TLS；请联系网络管理员确认放行 api.deepseek.com。")
    issuer = dict(item[0] for item in cert.get("issuer", ()) if item)
    return Finding("TLS 证书", True, f"已校验，颁发者 {issuer.get('organizationName', '未知')}，"
                                    f"有效期至 {cert.get('notAfter', '未知')}")


def check_models(base_url, key, timeout=FAST_READ_TIMEOUT):
    """真正调用 /models：这一步才验证 Key 与配额，走的是程序同一条请求路径。"""
    if not key:
        return Finding("接口鉴权", True, "未提供 Key，跳过（仅检查网络可达）")
    import http.client

    from . import ai

    try:
        client = ai.DeepSeekClient(key, base_url=base_url)
        started = time.perf_counter()
        models = client.list_models(timeout=timeout)
    except Exception as exc:                     # 诊断只报告，不抛出
        message = str(exc)
        return Finding("接口鉴权", False, message, _auth_hint(message))
    return Finding("接口鉴权", True, f"{len(models)} 个可用模型：{'、'.join(models[:6])}"
                                    f"（{(time.perf_counter() - started) * 1000:.0f} ms）")


def _auth_hint(message):
    if "Key" in message:
        return "在「模型设置」重新粘贴 Key；换过接口地址后必须重输 Key。"
    if "余额" in message:
        return "到服务商控制台充值后重试。"
    if "无权" in message:
        return "该 Key 未开通此接口或该模型，换 Key 或换模型。"
    return "按提示处理后点「重新诊断」。"


def diagnose(base_url="https://api.deepseek.com", key="", *, full=True):
    """跑完整诊断，返回 Finding 列表。full=False 时只做本地与环境检查。"""
    findings = [check_time()]
    try:
        host, port, _path = endpoint_parts(base_url)
    except ValueError as exc:
        return [Finding("接口地址", False, str(exc), "地址形如 https://api.deepseek.com，不要带 /chat/completions。")]
    findings.append(Finding("接口地址", True, f"{base_url} → {host}:{port}"))
    proxy_finding, proxy = check_proxy(base_url)
    findings.append(proxy_finding)
    target_host, target_port = (proxy.rsplit(":", 1) if proxy else (host, str(port)))
    findings.append(check_dns(target_host))
    findings.append(check_tcp(target_host, int(target_port)))
    if not proxy:
        findings.append(check_tls(host, port))
    else:
        findings.append(Finding("TLS 证书", True, "经由代理转发，本机不直接校验站点证书",
                                "如代理开启「HTTPS 解密」，需把其根证书装入系统。"))
    if full:
        findings.append(check_models(base_url, key))
    return findings


def format_report(findings, *, title="Attesta 模型连接诊断"):
    ok = all(item.ok for item in findings)
    head = f"{title}\n运行时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n结论：{'链路正常' if ok else '存在异常，见下方标注'}"
    return "\n".join([head, "-" * 46, *[item.line() for item in findings]])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    base_url = argv[0] if argv else "https://api.deepseek.com"
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    print(format_report(diagnose(base_url, key)))
    if "--json" in argv:
        print(json.dumps([item.as_dict() for item in diagnose(base_url, key)], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
