"""连接诊断与超时设置的回归测试：既验证结论可读，也验证不泄露凭证。"""

import socket
import ssl
import tkinter as tk
import unittest
from unittest.mock import patch

from tb_autofill import ai, netdiag, settings, theme


class FailureMessageTests(unittest.TestCase):
    """每类底层异常都要给出不同的结论，不能再统一成「无法连接或超时」。"""

    def test_certificate_failure_mentions_certificate_not_timeout(self):
        error = ssl.SSLCertVerificationError("certificate verify failed")
        message = netdiag.describe_failure(error, "api.deepseek.com")
        self.assertIn("证书", message)
        self.assertNotIn("超时", message)

    def test_dns_failure_mentions_resolution(self):
        message = netdiag.describe_failure(socket.gaierror(11001, "getaddrinfo failed"), "api.deepseek.com")
        self.assertIn("解析", message)

    def test_refused_connection_suggests_proxy_state(self):
        message = netdiag.describe_failure(ConnectionRefusedError(10061, "refused"), "127.0.0.1:7890")
        self.assertIn("拒绝", message)

    def test_timeout_suggests_diagnostics(self):
        message = netdiag.describe_failure(TimeoutError("timed out"), "api.deepseek.com")
        self.assertIn("超时", message)

    def test_urlerror_reason_is_unwrapped(self):
        import urllib.error

        wrapped = urllib.error.URLError(ssl.SSLCertVerificationError("bad cert"))
        self.assertIn("证书", netdiag.describe_failure(wrapped, "api.deepseek.com"))


class ProxyResolutionTests(unittest.TestCase):
    def test_environment_proxy_is_reported_with_source(self):
        with patch("urllib.request.getproxies", return_value={"https": "http://127.0.0.1:7890"}):
            proxy, source = netdiag.resolve_proxy("https://api.deepseek.com/models")
        self.assertEqual(proxy, "127.0.0.1:7890")
        self.assertIn("系统代理", source)

    def test_bare_host_port_proxy_is_normalized(self):
        self.assertEqual(netdiag._normalize_proxy("127.0.0.1:1080"), "127.0.0.1:1080")
        self.assertEqual(netdiag._normalize_proxy("http://10.0.0.2:8080/"), "10.0.0.2:8080")
        self.assertEqual(netdiag._normalize_proxy(""), "")


class TimeoutPolicyTests(unittest.TestCase):
    """长文本生成不能被 60 秒掐断；/models 这类轻请求仍要快速失败。"""

    def test_generation_uses_long_read_timeout_and_models_uses_fast_one(self):
        timeouts = []

        class Response:
            def read(self, _limit=None):
                return b'{"data":[{"id":"deepseek-flash"}],"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}'

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        def open_(request, timeout=None):
            timeouts.append(timeout)
            return Response()

        opener = unittest.mock.MagicMock()
        opener.open.side_effect = open_
        client = ai.DeepSeekClient("synthetic-key")
        with patch.object(ai.netdiag, "resolve_proxy", return_value=("", "直连")), \
                patch.object(ai.DeepSeekClient, "_preflight", return_value=None), \
                patch.object(ai.urllib.request, "build_opener", return_value=opener):
            client.list_models()
            client.paper_text({"system": "只返回 JSON", "question": "写出说明", "context": {}})
        self.assertEqual(timeouts[0], netdiag.FAST_READ_TIMEOUT)
        self.assertEqual(timeouts[1], netdiag.READ_TIMEOUT)
        self.assertGreaterEqual(netdiag.READ_TIMEOUT, 120)

    def test_http_error_body_is_redacted(self):
        import io
        import urllib.error

        secret = "synthetic-sensitive-secret"
        error = urllib.error.HTTPError("https://api.deepseek.com/models", 401, secret, {},
                                       io.BytesIO(secret.encode()))
        opener = unittest.mock.MagicMock()
        opener.open.side_effect = error
        client = ai.DeepSeekClient(secret)
        with patch.object(ai.netdiag, "resolve_proxy", return_value=("", "直连")), \
                patch.object(ai.DeepSeekClient, "_preflight", return_value=None), \
                patch.object(ai.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ai.AIError) as caught:
                client.list_models()
        self.assertNotIn(secret, str(caught.exception))
        self.assertIn("API Key", str(caught.exception))


class LayoutScaleTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_px_scales_with_dpi_and_never_returns_zero(self):
        with patch.object(self.root, "winfo_fpixels", return_value=144.0):
            self.assertEqual(theme.px(self.root, 100), 150)
            self.assertEqual(theme.px(self.root, 0), 1)
        with patch.object(self.root, "winfo_fpixels", return_value=96.0):
            self.assertEqual(theme.px(self.root, 208), 208)

    def test_fit_window_clamps_to_work_area_and_centers(self):
        window = tk.Toplevel(self.root)
        with patch.object(theme, "work_area", return_value=(1366, 728)), \
                patch.object(window, "winfo_fpixels", return_value=144.0):
            width, height = theme.fit_window(window, 1080, 580, min_width=800, min_height=420)
        self.assertLessEqual(width, 1366)
        self.assertLessEqual(height, 728)
        self.assertGreaterEqual(width, 320)
        window.destroy()

    def test_window_minsize_never_exceeds_work_area(self):
        window = tk.Toplevel(self.root)
        with patch.object(theme, "work_area", return_value=(1024, 600)), \
                patch.object(window, "winfo_fpixels", return_value=192.0):
            theme.fit_window(window, 1480, 920, min_width=1000, min_height=620)
        min_width, min_height = window.minsize()
        self.assertLessEqual(min_width, 1024)
        self.assertLessEqual(min_height, 600)
        window.destroy()


class EndpointTests(unittest.TestCase):
    def test_plain_http_and_missing_host_are_rejected(self):
        for value in ("http://api.deepseek.com", "https://", "api.deepseek.com"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                netdiag.endpoint_parts(value)

    def test_settings_default_matches_diagnostic_default(self):
        self.assertEqual(settings.DEFAULT_BASE_URL, "https://api.deepseek.com")


if __name__ == "__main__":
    unittest.main()
