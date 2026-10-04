"""桌面前端资源加载回归，不读取账号或运行数据。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui_web import _webui_app


class WebUiAssetTests(unittest.TestCase):
    def request(self, path, method="GET", **environ):
        response = {}

        def start(status, headers):
            response.update(status=status, headers=dict(headers))

        body = b"".join(_webui_app({"PATH_INFO": path, "REQUEST_METHOD": method,
                                   **environ}, start))
        return response, body

    def test_current_page_and_resources_are_never_cached(self):
        for path in ("/", "/index.html", "/app.css", "/traffic.css", "/app.js", "/traffic.js"):
            with self.subTest(path=path):
                response, body = self.request(path)
                self.assertEqual(response["status"], "200 OK")
                self.assertIn("no-store", response["headers"]["Cache-Control"])
                self.assertEqual(int(response["headers"]["Content-Length"]), len(body))
        _, body = self.request("/")
        self.assertIn("Pro v3.0".encode(), body)
        self.assertIn(b'"page-traffic"', body)

    def test_old_conditional_request_gets_current_content(self):
        response, body = self.request("/", HTTP_IF_MODIFIED_SINCE="Sat, 03 Oct 2099 00:00:00 GMT")
        self.assertEqual(response["status"], "200 OK")
        self.assertTrue(body)

    def test_only_ui_assets_are_exposed(self):
        for path in ("/../settings.json", "/settings.json", "/accounts.json", "/stats.json", "/main.py"):
            response, body = self.request(path)
            self.assertEqual(response["status"], "404 Not Found")
            self.assertEqual(body, b"")

    def test_head_and_unsupported_method(self):
        response, body = self.request("/", "HEAD")
        self.assertEqual(response["status"], "200 OK")
        self.assertGreater(int(response["headers"]["Content-Length"]), 0)
        self.assertEqual(body, b"")
        response, body = self.request("/", "POST")
        self.assertEqual(response["status"], "405 Method Not Allowed")


if __name__ == "__main__":
    unittest.main()
