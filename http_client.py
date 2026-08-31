"""统一 HTTP 客户端 — 成熟模式：默认超时 + 指数退避重试。

所有 API 请求走这里，杜绝"无 timeout 卡死"（旧程序病根）。
仅用标准库（urllib），零第三方依赖，可 PyInstaller 直接打包。
"""
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 15
MAX_RETRIES = 3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# 禁用系统代理：urllib 默认读 HTTP_PROXY/HTTPS_PROXY 环境变量，
# 本机环境变量指向 127.0.0.1:10809（Misty 的 privoxy），Misty 关闭时
# 代理不存在 → 请求全失败。API 实际直连可达，显式走直连。
# HTTPS 走 urllib 默认上下文（证书校验开启；build_opener 含默认 HTTPSHandler）。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

UA = "ClashForWindows/0.20.39"


class HttpError(Exception):
    def __init__(self, status, body: bytes = b""):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}")


def _request(method: str, url: str, *, data=None, headers=None, timeout=DEFAULT_TIMEOUT,
             retries=MAX_RETRIES, json_body=False) -> tuple[int, bytes, dict]:
    """发请求，带超时 + 退避重试。返回 (status, body_bytes, response_headers)。"""
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    if data is not None:
        if json_body:
            body = json.dumps(data).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        elif isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        else:
            body = data
    else:
        body = None

    attempt = 0
    while True:
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with _OPENER.open(req, timeout=timeout) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            if e.code in RETRYABLE_STATUS and attempt < retries:
                attempt += 1
                time.sleep(min(2 ** attempt, 8))
                continue
            raise HttpError(e.code, e.read())
        except (urllib.error.URLError, socket.timeout, ConnectionError, ssl.SSLError) as e:
            if attempt < retries:
                attempt += 1
                time.sleep(min(2 ** attempt, 8))
                continue
            raise HttpError(0, str(e).encode())


def get(url: str, *, headers=None, timeout=DEFAULT_TIMEOUT, retries=MAX_RETRIES):
    """GET 请求，返回 (status, body_bytes)。需要 JSON 时用 get_json。"""
    status, body, _ = _request("GET", url, headers=headers, timeout=timeout, retries=retries)
    return status, body


def get_json(url: str, *, headers=None, timeout=DEFAULT_TIMEOUT, retries=MAX_RETRIES):
    status, body, _ = _request("GET", url, headers=headers, timeout=timeout, retries=retries)
    if 200 <= status < 300:
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpError(status, body)
    raise HttpError(status, body)


def post(url: str, data=None, *, headers=None, json_body=False,
         timeout=DEFAULT_TIMEOUT, retries=MAX_RETRIES):
    status, body, _ = _request("POST", url, data=data, headers=headers, json_body=json_body,
                               timeout=timeout, retries=retries)
    return status, body


def post_json(url: str, data=None, *, headers=None, json_body=False,
              timeout=DEFAULT_TIMEOUT, retries=MAX_RETRIES):
    status, body, _ = _request("POST", url, data=data, headers=headers, json_body=json_body,
                               timeout=timeout, retries=retries)
    if 200 <= status < 300:
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpError(status, body)
    raise HttpError(status, body)
