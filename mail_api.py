"""mail.tm 临时邮箱 — 创建邮箱 + 轮询收件箱提取验证码。

邮件 API（mail.tm）：POST /accounts 创建，GET /messages 收信。
"""
import re
import time

from http_client import get_json, post_json, HttpError

MAIL_API = "https://api.mail.tm"


class MailBox:
    """一次性临时邮箱。"""

    def __init__(self, address: str = "", password: str = ""):
        self.address = address
        self.password = password
        self.token = ""

    def create(self) -> bool:
        """创建新临时邮箱。成功返回 True。"""
        try:
            # 获取域名
            domains = get_json(f"{MAIL_API}/domains")
            domain = ""
            if isinstance(domains, dict):
                members = domains.get("hydra:member") or []
                if members:
                    domain = members[0].get("domain", "")
            if not domain:
                return False
            # 生成随机地址
            import random
            import string
            local = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
            address = f"{local}@{domain}"
            resp = post_json(f"{MAIL_API}/accounts", data={
                "address": address,
                "password": self.password or "AutoSw%06d!" % random.randint(0, 999999),
            }, json_body=True)
            if isinstance(resp, dict) and resp.get("id"):
                self.address = resp.get("address", address)
                self.password = resp.get("password", self.password)
                # 登录拿 token
                tok = post_json(f"{MAIL_API}/token", data={
                    "address": self.address,
                    "password": self.password,
                }, json_body=True)
                if isinstance(tok, dict) and tok.get("token"):
                    self.token = tok["token"]
                    return True
        except (HttpError, OSError, ValueError):
            pass
        return False

    def fetch_code(self, timeout: float = 120.0) -> str:
        """轮询收件箱，提取 6 位验证码。超时返回空串。"""
        deadline = time.time() + timeout
        headers = {"Authorization": f"Bearer {self.token}"}
        seen = set()
        while time.time() < deadline:
            try:
                msgs = get_json(f"{MAIL_API}/messages", headers=headers)
                members = msgs.get("hydra:member") or []
                for m in members:
                    mid = m.get("id")
                    if mid in seen:
                        continue
                    seen.add(mid)
                    # 拉取邮件正文
                    detail = get_json(f"{MAIL_API}/messages/{mid}", headers=headers)
                    text = ""
                    if isinstance(detail, dict):
                        text = detail.get("text", "") or ""
                        if isinstance(text, list):
                            text = "".join(str(x) for x in text)
                        intro = detail.get("intro", "")
                        text = f"{intro}\n{text}"
                    mch = re.search(r"\b(\d{6})\b", text)
                    if mch:
                        return mch.group(1)
            except (HttpError, OSError, ValueError):
                pass
            time.sleep(3)
        return ""
