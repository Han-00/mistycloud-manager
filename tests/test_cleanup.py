# -*- coding: utf-8 -*-
"""过期自动删除测试（隔离临时库，不碰真实 accounts.json）。

验证：
  - expired / banned 账号被删除
  - ready 但生命周期到点 / 服务端套餐到期 → 删除
  - active 在用号即使已过期也【不删】（交给换号流程）
  - 有效 ready 备用保留
  - 返回删除数量正确；空库/无过期时返回 0
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Desktop_Files\mistycloud_manager")

import account_pool  # noqa: E402
from config import Config  # noqa: E402

# 隔离：把 accounts_path 指到临时文件，绝不读写真实 accounts.json
_TMP = tempfile.mkdtemp()
_TMP_JSON = os.path.join(_TMP, "accounts.json")
account_pool.accounts_path = lambda: _TMP_JSON

cfg = Config()
pool = account_pool.AccountPool(cfg)


def mk(email, status, created=None, ce=0):
    return {"email": email, "password": "x",
            "created_at": created if created is not None else time.time(),
            "last_used_at": 0, "status": status, "class_expire": ce}


now = time.time()
pool.accounts = [
    mk("active@x.com", "active", created=now),            # 在用，必须保留
    mk("ready1@x.com", "ready", created=now),              # 有效备用，保留
    mk("expired1@x.com", "expired", created=now - 100000),  # 已过期，删
    mk("expired2@x.com", "expired", created=now),           # 已过期(标记)，删
    mk("banned1@x.com", "banned", created=now),             # 失效，删
    mk("readyold@x.com", "ready", created=now - 100000),    # 生命周期到点→删
    mk("readyce@x.com", "ready", created=now, ce=now - 10),  # 套餐到期→删
]
pool.save()

removed = pool.cleanup_expired()
emails = {a["email"] for a in pool.all()}

print(f"removed = {removed}")
print("remaining:", sorted(emails))

assert removed == 5, f"应删 5 个，实删 {removed}"
assert "active@x.com" in emails, "在用账号被误删！"
assert "ready1@x.com" in emails, "有效备用被误删！"
assert "expired1@x.com" not in emails, "过期账号未删！"
assert "banned1@x.com" not in emails, "失效账号未删！"
assert "readyold@x.com" not in emails, "生命周期到点的备用未删！"
assert "readyce@x.com" not in emails, "套餐到期的备用未删！"
print("[1] 过期/失效/到期账号删除 ✓  在用号与有效备用保留 ✓")

# 再跑一次：已无过期，删 0
assert pool.cleanup_expired() == 0, "无过期时应返回 0"
print("[2] 幂等：无过期时返回 0 ✓")

# 关键：active 即使生命周期到点也不被 cleanup 删除（交给换号流程）
pool.accounts = [
    mk("deadactive@x.com", "active", created=now - 999999),
]
pool.save()
assert pool.cleanup_expired() == 0, "active 不应被 cleanup 删除"
assert pool.get_active()["email"] == "deadactive@x.com", "在用号必须仍在"
print("[3] 在用号即使过期也不被 cleanup 删除（防代理被抽走）✓")

# 空库不报错
pool.accounts = []
pool.save()
assert pool.cleanup_expired() == 0
print("[4] 空库安全 ✓")

print("\n== 过期自动删除测试通过 ==")
