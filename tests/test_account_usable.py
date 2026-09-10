# -*- coding: utf-8 -*-
"""账号可用性判断（AccountPool.is_usable）测试。

为什么单独测它：这个判断被**三处界面 + 三处账号池内部逻辑**共用
（账号列表是否弱化显示、下拉框列不列出、挑候选、统计可用数、清理过期）。
它原先叫 `_is_valid`，是私有方法却被外部到处调用；改名的同时修掉了一个真问题：
时间戳字段无法解析时会抛 ValueError——ui_web 用 try/except 兜住后退化成
"按 status 猜"，会静默显示错误的可用性；另两个界面则根本没兜底。

隔离：accounts_path 指向临时文件，绝不读写真实 accounts.json。
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import account_pool  # noqa: E402

_TMP_JSON = os.path.join(tempfile.mkdtemp(), "accounts.json")
account_pool.accounts_path = lambda: _TMP_JSON

LIFE = 86400.0
_UNSET = object()


class _Cfg:
    """只提供 get 的配置替身，避免读取真实 settings.json。"""

    def __init__(self, **kw):
        self.d = {"account_lifetime_seconds": LIFE}
        self.d.update(kw)

    def get(self, k, default=None):
        return self.d.get(k, default)


def mk(email, status, created=_UNSET, ce=0):
    d = {"email": email, "password": "x", "last_used_at": 0,
         "status": status, "class_expire": ce}
    d["created_at"] = time.time() if created is _UNSET else created
    return d


def main():
    pool = account_pool.AccountPool(_Cfg())
    now = time.time()

    # ---- [1] 状态短路 ----
    assert pool.is_usable(mk("a", "banned", now)) is False
    assert pool.is_usable(mk("a", "expired", now)) is False
    assert pool.is_usable(mk("a", "ready", now)) is True
    assert pool.is_usable(mk("a", "active", now)) is True
    print("[1] banned/expired 恒不可用，ready/active 视时长而定 ✓")

    # ---- [2] 客户端生命周期 ----
    assert pool.is_usable(mk("a", "ready", now - LIFE + 60)) is True
    assert pool.is_usable(mk("a", "ready", now - LIFE - 1)) is False
    assert pool.is_usable(mk("a", "ready", 0)) is False
    print("[2] 生命周期边界（未到点可用 / 到点不可用 / created=0 不可用）✓")

    # ---- [3] 服务端套餐到期 ----
    assert pool.is_usable(mk("a", "ready", now, ce=now + 100)) is True
    assert pool.is_usable(mk("a", "ready", now, ce=now - 1)) is False
    assert pool.is_usable(mk("a", "ready", now, ce=0)) is True   # 0 = 无到期信息
    print("[3] 套餐到期：未到期可用 / 已到期不可用 / 缺省视为无 ✓")

    # ---- [4] 脏数据不抛异常（本次修复的核心）----
    for bad in ("abc", None, "", [], {}):
        r = pool.is_usable(mk("a", "ready", bad))
        assert r is False, f"脏 created_at={bad!r} 应判不可用，实际 {r}"
    # class_expire 脏 → 视作无套餐到期，回落由 created 决定
    assert pool.is_usable(mk("a", "ready", now, ce="xyz")) is True
    print("[4] 脏时间戳：不抛异常、按不可用/忽略处理 ✓")

    # ---- [5] 配置里的脏 lifetime 回落默认值 ----
    pool2 = account_pool.AccountPool(_Cfg(account_lifetime_seconds="oops"))
    assert pool2.is_usable(mk("a", "ready", now)) is True
    print("[5] 配置项为脏值时回落默认生命周期，不抛 ✓")

    # ---- [6] 记录缺键也不炸 ----
    assert pool.is_usable({}) is False
    assert pool.is_usable({"status": "ready"}) is False
    print("[6] 缺键记录安全返回不可用 ✓")

    # ---- [7] 三个内部调用方与 is_usable 判断一致 ----
    pool.accounts = [
        mk("ok@x.com", "ready", now),
        mk("bad@x.com", "ready", "abc"),      # 脏数据：应判不可用
        mk("old@x.com", "ready", now - LIFE - 1),
    ]
    pool.save()
    assert pool.count_valid() == 1, pool.count_valid()
    picked = pool.pick_next()
    assert picked and picked["email"] == "ok@x.com", picked
    removed = pool.cleanup_expired()
    assert removed == 2, f"脏数据与过期号都应被清理，实删 {removed}"
    assert {a["email"] for a in pool.all()} == {"ok@x.com"}
    print("[7] count_valid / pick_next / cleanup_expired 与 is_usable 一致 ✓")

    print("\n== 账号可用性判断测试通过 ==")


if __name__ == "__main__":
    main()
