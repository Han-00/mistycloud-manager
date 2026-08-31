"""MistyCloud API 封装 — 注册/登录/用户信息/订阅拉取/节点解析。

依据：原程序 register.py / switch_api.py 逆向出的完整链路：
  注册（临时邮箱+验证码）→ /app/auth/login（返回 auth 四头）
  → GET /app/user（subInfo=订阅 URL）→ GET /link/{token}?sub=3
  → base64 vmess:// 节点 → 提取 UUID/服务器配置
"""
import base64
import json
import re
import urllib.parse

from http_client import get, get_json, post_json, HttpError

API_HOST = "https://mistycapsule.xyz"

# 订阅头字段（Subscription-Userinfo）
SUB_HEADERS = {
    "Subscription-Userinfo": "upload; download; total; expire",
}


def _api(path: str) -> str:
    return f"{API_HOST}{path}"


class CloudAccount:
    """一个 MistyCloud 账号的登录态 + 订阅信息。"""

    def __init__(self, email: str = "", password: str = ""):
        self.email = email
        self.password = password
        self.auth_uid = ""
        self.auth_key = ""
        self.auth_email = ""
        self.expire_in = 0
        self.sub_url = ""          # /link/{token} 订阅地址
        self.plan = ""             # 套餐名
        self.class_id = 0
        self.class_expire = 0
        self.node_group = 0
        self.node = None           # vmess 节点 dict
        self.traffic = {}          # upload/download/total/expire
        self.sub_error = ""        # 最近一次订阅拉取失败原因（可读）
        self.login_fail_code = ""  # 最近一次 login() 的失败码（''=成功或未尝试）
        self.logged_in = False

    # ---- 登录 ----
    def login(self) -> bool:
        """登录，拿 auth 四头 + 用户信息。失败码记录到 login_fail_code。"""
        try:
            data = {"email": self.email, "passwd": self.password}
            resp = post_json(_api("/app/auth/login"), data=data)
            # 登录成功：ret=1 且顶层 auth 字段
            if isinstance(resp, dict) and (resp.get("ret") == 1 or resp.get("auth")):
                auth = resp.get("auth") or {}
                self.auth_uid = str(auth.get("uid") or auth.get("Uid") or "")
                self.auth_key = str(auth.get("key") or auth.get("Key") or "")
                self.auth_email = str(auth.get("email") or self.email)
                self.expire_in = int(auth.get("expire_in") or 0)
                if self.auth_key:
                    self.logged_in = True
                    self.login_fail_code = ""
                    self._fetch_user_info()
                    return True
            self.login_fail_code = self._classify_login_resp(resp)
            return False
        except (HttpError, OSError, ValueError):
            # 网络错误（超时/连接失败）→ 不标记 banned
            self.login_fail_code = "network"
            return False

    @staticmethod
    def _classify_login_resp(resp) -> str:
        if isinstance(resp, dict):
            if resp.get("ret") == 1 or resp.get("auth"):
                return ""
            msg = str(resp.get("msg", ""))
            if any(k in msg for k in ("密码", "邮箱", "错误", "不存在", "ban", "封禁", "停用")):
                return "bad_credentials"
        return "unknown"

    def login_error_code(self) -> str:
        """登录失败的错误码：'bad_credentials' / 'network' / 'unknown'。

        区分账号失效（服务端明确拒绝）和网络故障（不该标记 banned）。
        login() 失败后已记录原因，直接返回（不再重复 POST 登录）。
        """
        if self.login_fail_code or self.logged_in:
            return self.login_fail_code
        # 未调用过 login() 时现场重试一次
        try:
            data = {"email": self.email, "passwd": self.password}
            resp = post_json(_api("/app/auth/login"), data=data)
            return self._classify_login_resp(resp)
        except (HttpError, OSError, ValueError):
            return "network"

    # ---- 用户信息（拿 subInfo 订阅 URL + 套餐）----
    def _fetch_user_info(self) -> bool:
        headers = {
            "Uid": self.auth_uid,
            "Key": self.auth_key,
            "Email": self.auth_email or self.email,
            "Expire-In": str(self.expire_in),
        }
        try:
            resp = get_json(_api("/app/user"), headers=headers)
            user = resp.get("user") if isinstance(resp, dict) else {}
            if not isinstance(user, dict):
                user = resp.get("data") or {}
            if not user and isinstance(resp, dict):
                user = resp  # 响应本身就是 user 对象
            self.sub_url = user.get("subInfo") or user.get("sub_info") or ""
            self.plan = user.get("plan") or ""
            self.class_id = int(user.get("class") or user.get("class_id") or 0)
            self.node_group = int(user.get("node_group") or user.get("nodeGroup") or 0)
            # class_expire 可能是字符串 "2026-08-12 14:52:16"
            ce = user.get("class_expire") or user.get("classExpire") or 0
            if isinstance(ce, str):
                try:
                    import datetime
                    ce = datetime.datetime.strptime(ce, "%Y-%m-%d %H:%M:%S").timestamp()
                except ValueError:
                    ce = 0
            self.class_expire = int(ce)
            # 流量：transfer_enable/u/d
            total = int(user.get("transfer_enable") or 0)
            used = int(user.get("u") or 0) + int(user.get("d") or 0)
            if total > 0:
                self.traffic = {"total": total, "upload": int(user.get("u") or 0),
                                "download": int(user.get("d") or 0)}
            return bool(self.sub_url)
        except (HttpError, OSError, ValueError):
            return False

    # ---- 拉订阅（重试）----
    def fetch_subscription(self, retries: int = 2, delay: float = 2.0) -> bool:
        """拉订阅 → 解析节点。失败重试；失败原因写入 self.sub_error。

        注意区分：订阅内容为空（服务端套餐过期）≠ 请求失败 ≠ 解析失败，
        前者意味着账号已废，上层据此标记 expired。
        """
        import time
        self.sub_error = ""
        for i in range(retries + 1):
            try:
                if not self.sub_url:
                    self._fetch_user_info()
                if not self.sub_url:
                    self.sub_error = "未获取到订阅地址（用户信息接口无 subInfo）"
                    time.sleep(delay)
                    continue
                url = self.sub_url
                if "?" not in url:
                    url += "?sub=3"
                # retries=1：外层已重试，避免 http_client 指数退避叠加导致单次换号阻塞数分钟
                status, body = get(url, headers={"User-Agent": "ClashForWindows/0.20.39"},
                                   retries=1)
                if status != 200:
                    self.sub_error = f"订阅请求失败（HTTP {status}）"
                    time.sleep(delay)
                    continue
                if not body:
                    self.sub_error = "订阅内容为空（套餐可能已在服务端过期）"
                    time.sleep(delay)
                    continue
                node = parse_subscription(body)
                if node:
                    self.node = node
                    self.sub_error = ""
                    return True
                self.sub_error = "订阅内容解析失败（无有效 vmess 节点）"
                time.sleep(delay)
            except (HttpError, OSError, ValueError) as e:
                self.sub_error = f"订阅请求异常: {e}"
                time.sleep(delay)
        return False

    # ---- 流量查询（订阅头）----
    def fetch_traffic(self) -> dict:
        """读订阅响应头拿流量配额。失败返回空 dict。"""
        try:
            if not self.sub_url:
                return {}
            url = self.sub_url
            if "sub=" not in url:
                url += ("&" if "?" in url else "?") + "sub=3"
            status, body, headers = _raw_get_headers(url)
            if status != 200:
                return {}
            info = headers.get("Subscription-Userinfo", "")
            return parse_subscription_userinfo(info)
        except (HttpError, OSError, ValueError):
            return {}


def _raw_get_headers(url: str):
    """GET 并返回 (status, body, headers)。"""
    from http_client import _request
    return _request("GET", url, headers={"User-Agent": "ClashForWindows/0.20.39"})


def parse_subscription(body: bytes) -> dict | None:
    """解析订阅内容（base64 vmess:// 列表），返回第一个有效节点 dict。

    节点结构：address/port/id/aid/security/network/ws path/host
    """
    try:
        text = body.decode("utf-8").strip()
    except UnicodeDecodeError:
        try:
            text = body.decode("latin-1").strip()
        except Exception:
            return None
    # 可能整段 base64 或直接 vmess:// 明文
    vmess_links = re.findall(r"vmess://[A-Za-z0-9+/=_-]+", text)
    if not vmess_links:
        # 尝试整体 base64 解码
        try:
            decoded = base64.b64decode(text + "=" * (-len(text) % 4)).decode("utf-8", errors="ignore")
            vmess_links = re.findall(r"vmess://[A-Za-z0-9+/=_-]+", decoded)
        except Exception:
            return None
    for link in vmess_links:
        node = parse_vmess(link)
        if node:
            return node
    return None


def parse_vmess(link: str) -> dict | None:
    """解析单个 vmess:// 链接（兼容 URL-safe base64 与 #fragment）。"""
    try:
        payload = link[len("vmess://"):].split("#")[0]
        raw = base64.b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8")
        obj = json.loads(raw)
        node = {
            "address": obj.get("add", ""),
            "port": int(obj.get("port", 0)),
            "id": obj.get("id", ""),
            "aid": int(obj.get("aid") or 0),  # aid=0 是 VMess 合法值，不能当缺失处理
            "security": obj.get("scy", "auto"),
            "network": obj.get("net", "tcp"),
            "ws_path": obj.get("path", ""),
            "ws_host": obj.get("host", ""),
            "remarks": obj.get("ps", ""),
            "tls": obj.get("tls", ""),
        }
        if not node["address"] or not node["id"] or not node["port"]:
            return None
        return node
    except Exception:
        return None


def parse_subscription_userinfo(info: str) -> dict:
    """解析 'upload=0; download=0; total=322122547; expire=...' 头。"""
    result = {}
    if not info:
        return result
    for part in info.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                result[k.strip()] = int(v.strip())
            except ValueError:
                result[k.strip()] = v.strip()
    return result
