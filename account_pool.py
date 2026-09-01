"""账号池管理 — 注册/预注册/有效期/流量查询，本地持久化。

账号存 accounts.json：{email, password, created_at, last_used_at, status,
class_expire(可选，服务端套餐到期秒级时间戳)}
status: ready(可用) / active(在用) / expired(过期) / banned(失效)
"""
import json
import os
import random
import string
import sys
import time
import threading

from config import Config
from cloud_api import CloudAccount, HttpError
from http_client import get, get_json, post_json


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def accounts_path() -> str:
    return os.path.join(_base_dir(), "accounts.json")


def _atomic_write_json(path: str, data) -> None:
    """tmp + os.replace 原子写，杜绝半写损坏覆盖全库（对齐 KNOWN_ISSUES P0-2）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class AccountPool:
    def __init__(self, config: Config):
        self.config = config
        self._lock = threading.Lock()
        self.accounts: list[dict] = []
        self._load()

    # ---- 持久化 ----
    def _load(self):
        try:
            with open(accounts_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.accounts = data
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self.accounts = []

    def save(self):
        with self._lock:
            try:
                _atomic_write_json(accounts_path(), self.accounts)
            except OSError:
                pass

    # ---- 查询 ----
    def all(self) -> list[dict]:
        with self._lock:
            return list(self.accounts)

    def count(self) -> int:
        return len(self.accounts)

    def _is_valid(self, acc: dict) -> bool:
        """未过期（客户端生命周期 + 服务端套餐到期）+ 未失效。"""
        if acc.get("status") in ("banned", "expired"):
            return False
        created = float(acc.get("created_at") or 0)
        if created <= 0:
            return False
        lifetime = float(self.config.get("account_lifetime_seconds", 86400))
        if (time.time() - created) >= lifetime:
            return False
        ce = float(acc.get("class_expire") or 0)
        if ce > 0 and time.time() > ce:
            return False
        return True

    def count_valid(self) -> int:
        return sum(1 for a in self.accounts if self._is_valid(a))

    def get_active(self) -> dict | None:
        """当前在用账号（status=active）。"""
        for a in self.accounts:
            if a.get("status") == "active":
                return a
        return None

    def pick_next(self, exclude_email: str = "", skip_emails=()) -> dict | None:
        """从备用池挑一个候选（未过期、非在用、非排除、非本轮已试）。"""
        skip = set(skip_emails)
        for a in self.accounts:
            if not self._is_valid(a):
                continue
            if a.get("status") == "active":
                continue
            email = a.get("email", "")
            if (exclude_email and email == exclude_email) or email in skip:
                continue
            return a
        return None

    # ---- 修改 ----
    def add(self, email: str, password: str) -> dict:
        acc = {
            "email": email,
            "password": password,
            "created_at": time.time(),
            "last_used_at": 0,
            "status": "ready",
        }
        with self._lock:
            # 去重
            self.accounts = [a for a in self.accounts if a.get("email") != email]
            self.accounts.append(acc)
        self.save()
        return acc

    def set_active(self, email: str) -> str | None:
        """将指定账号设为在用，并直接淘汰被替换的旧在用号。

        v2.0 语义：换号成功即放弃旧号——旧 active 不再降为 ready 混入备用池
        （否则会被 pick_next 重新选中导致重复换号，且 count_valid 虚高使
        补号失准）。被换下的旧 active 直接从库中删除，返回其 email；切换
        前后是同一账号时返回 None。目标不在库中时不做任何淘汰（返回 None），
        保证不会落得无在用号的空窗。
        """
        retired = None
        with self._lock:
            if not any(a.get("email") == email for a in self.accounts):
                # 目标不在库中（如切换进行中被手动删除）：不动旧在用号，
                # 避免旧号被淘汰、新号又没进库导致无 active 的空窗
                return None
            # 旧在用号（active 且非目标）直接淘汰，不再降为 ready
            keep = []
            for a in self.accounts:
                if a.get("status") == "active" and a.get("email") != email:
                    if retired is None:
                        retired = a.get("email")
                    continue
                keep.append(a)
            self.accounts = keep
            for a in self.accounts:
                if a["email"] == email:
                    a["status"] = "active"
                    a["last_used_at"] = time.time()
        self.save()
        return retired

    def mark_banned(self, email: str):
        with self._lock:
            for a in self.accounts:
                if a["email"] == email:
                    a["status"] = "banned"
        self.save()

    def mark_expired(self, email: str):
        """服务端套餐已到期（订阅拉空）时标记，避免反复选中。"""
        with self._lock:
            for a in self.accounts:
                if a["email"] == email:
                    a["status"] = "expired"
        self.save()

    def set_class_expire(self, email: str, ts: float):
        """记录服务端套餐到期时间（秒级时间戳；0/无效值忽略）。"""
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            return
        if ts <= 0:
            return
        with self._lock:
            for a in self.accounts:
                if a["email"] == email:
                    a["class_expire"] = ts
        self.save()

    def remove(self, email: str) -> bool:
        """删除账号，返回是否真删了。

        在用号（active）受保护：状态检查与删除在同一把锁内完成，杜绝
        「检查时是备用、删除时已升为在用」的竞态（其淘汰统一由换号流程
        的 set_active 接管）。账号不存在时返回 False。
        """
        with self._lock:
            acc = next((a for a in self.accounts if a.get("email") == email), None)
            if acc is None or acc.get("status") == "active":
                return False
            self.accounts = [a for a in self.accounts if a.get("email") != email]
        self.save()
        return True

    def cleanup_expired(self) -> int:
        """删除已过期/失效账号，返回删除数量。

        ready 但已过期（客户端生命周期到点或服务端套餐到期）→ 先标 expired；
        所有 expired / banned（非 active）→ 直接从库中删除。
        active 账号不在此处理：它由监控的换号流程接管，避免把正在服务代理的
        在用号从脚下抽走。被换号淘汰的旧在用号由 set_active 直接删除，不再
        经此函数。
        """
        with self._lock:
            for a in self.accounts:
                if a.get("status") == "ready" and not self._is_valid(a):
                    a["status"] = "expired"
            before = len(self.accounts)
            self.accounts = [a for a in self.accounts
                             if a.get("status") not in ("expired", "banned")]
            removed = before - len(self.accounts)
        if removed:
            self.save()
        return removed

    # ---- 注册 ----
    def register_one(self, log=None) -> dict | None:
        """注册一个全新账号。返回账号 dict 或 None。

        链路：mail.tm 建邮箱 → 发验证码 → 轮询收码 → 注册 MistyCloud → 登录。
        """
        from mail_api import MailBox
        try:
            # 1. 临时邮箱
            box = MailBox(password=self._rand_pw())
            if not box.create():
                if log: log("注册失败: 无法创建临时邮箱")
                return None
            if log: log(f"临时邮箱: {box.address}")

            # 2. 发送验证码（原程序: POST /app/auth/send with email）
            try:
                send_resp = post_json("https://mistycapsule.xyz/app/auth/send",
                                      data={"email": box.address},
                                      json_body=False)
                # ret=1 表示成功（"验证码发送成功"）；ret=0 表示失败
                if isinstance(send_resp, dict) and send_resp.get("ret") == 0:
                    if log: log(f"发送验证码失败: {send_resp.get('msg', '')}")
                    return None
            except HttpError as e:
                if log: log(f"发送验证码异常: HTTP {e.status}")
                return None
            if log: log("验证码已发送，等待邮件...")

            # 3. 轮询验证码
            code = box.fetch_code(timeout=120)
            if not code:
                if log: log("注册失败: 未收到验证码")
                return None
            if log: log(f"收到验证码: {code}")

            # 4. 正式注册（原程序参数: signupDevice=pc, version=1.0, app=misty）
            reg = post_json(_reg_api(), data={
                "email": box.address,
                "emailcode": code,
                "passwd": box.password,
                "repasswd": box.password,
                "name": box.address.split("@")[0],
                "code": "",
                "inviteCode": "",
                "signupDevice": "pc",
                "version": "1.0",
                "channel": "",
                "app": "misty",
            })
            if isinstance(reg, dict) and (reg.get("ret") == 1 or reg.get("auth") or reg.get("user")):
                acc = self.add(box.address, box.password)
                if log: log(f"注册成功: {box.address}")
                # 注册后调 login 建立会话（原程序行为）
                try:
                    post_json("https://mistycapsule.xyz/app/auth/login",
                              data={"email": box.address, "passwd": box.password})
                except (HttpError, OSError, ValueError):
                    pass
                return acc
            # 可能已注册或需要其他字段
            msg = reg.get("msg", "") if isinstance(reg, dict) else ""
            if log: log(f"注册失败: {msg or '未知错误'}")
            return None
        except Exception as e:
            if log: log(f"注册异常: {e}")
            return None

    def _rand_pw(self) -> str:
        return "AutoSw%06d!" % random.randint(0, 999999)

    def topup(self, log=None) -> int:
        """补齐备用账号，返回新增数量。"""
        reserve = int(self.config.get("reserve_accounts", 2))
        target = reserve + 1  # 备用 + 当前在用
        added = 0
        while self.count_valid() < target:
            acc = self.register_one(log=log)
            if acc is None:
                break  # 注册失败，等下次
            added += 1
        return added


def _reg_api() -> str:
    return "https://mistycapsule.xyz/app/auth/register"
