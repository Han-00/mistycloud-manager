# -*- coding: utf-8 -*-
"""set_active 旧号淘汰 + 在用号保护测试（隔离临时库，不碰真实 accounts.json）。

验证 v2.0 语义：换号成功后，被替换的旧在用号直接从库中删除，不再降为
ready 混入备用池——杜绝重复换号与补号计数虚高。另覆盖两个健壮性边界：
目标不在库中时不淘汰旧在用号（避免无 active 空窗）；remove() 在锁内
原子拒绝删除在用号（杜绝删除确认弹窗期间的竞态）。
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _isolated import isolated_accounts  # noqa: E402
from config import Config  # noqa: E402
from account_pool import AccountPool  # noqa: E402


def mk(email, status, created=None):
    return {"email": email, "password": "x",
            "created_at": created if created is not None else time.time(),
            "last_used_at": 0, "status": status, "class_expire": 0}


def main():
    with isolated_accounts():
        cfg = Config()
        pool = AccountPool(cfg)
        now = time.time()

        # 场景1：换到新号 → 旧 active 直接删除，返回被淘汰 email
        pool.accounts = [
            mk("old@x.com", "active", created=now),
            mk("new@x.com", "ready", created=now),
            mk("spare@x.com", "ready", created=now),
        ]
        pool.save()
        retired = pool.set_active("new@x.com")
        emails = {a["email"] for a in pool.all()}
        assert retired == "old@x.com", f"应返回被淘汰旧号 old@x.com，实得 {retired!r}"
        assert "old@x.com" not in emails, "旧在用号应被删除，不应残留"
        assert pool.get_active()["email"] == "new@x.com"
        assert "spare@x.com" in emails, "无关备用号不应被误删"
        print("[1] 换号淘汰旧在用号 ✓")

        # 场景2：旧号已删 → 不进候选池（不重复换号）、count_valid 不虚高（补号不误判）
        nxt = pool.pick_next(exclude_email="new@x.com")
        assert nxt["email"] == "spare@x.com", f"候选应为 spare，实得 {nxt['email']}"
        assert pool.count_valid() == 2, f"有效数应为 2，实得 {pool.count_valid()}"
        print("[2] 旧号不进候选池 / count_valid 不虚高 ✓")

        # 场景3：切换前后是同一账号 → 不删自己，返回 None
        pool.accounts = [
            mk("same@x.com", "active", created=now),
            mk("other@x.com", "ready", created=now),
        ]
        pool.save()
        retired = pool.set_active("same@x.com")
        assert retired is None, f"切自己应返回 None，实得 {retired!r}"
        assert len(pool.all()) == 2, "切自己不应删除任何号"
        print("[3] 切换同一账号不删自己 ✓")

        # 场景4：无旧 active（首次启动）→ 正常设主号，返回 None
        pool.accounts = [mk("first@x.com", "ready", created=now)]
        pool.save()
        retired = pool.set_active("first@x.com")
        assert retired is None
        assert pool.get_active()["email"] == "first@x.com"
        print("[4] 无旧 active 时正常设主号 ✓")

        # 场景5：目标不在库中（切换进行中被删）→ 不淘汰旧在用号，避免无 active 空窗
        pool.accounts = [
            mk("keep@x.com", "active", created=now),
            mk("spare@x.com", "ready", created=now),
        ]
        pool.save()
        retired = pool.set_active("ghost@x.com")
        assert retired is None, f"目标不存在不应淘汰旧号，实得 {retired!r}"
        active = pool.get_active()
        assert active and active["email"] == "keep@x.com", "旧在用号必须保留"
        assert len(pool.all()) == 2, "不应有任何账号被误删"
        print("[5] 目标不在库中时不动旧在用号 ✓")

        # 场景6：remove() 原子保护在用号 / 不存在返回 False / 正常删除返回 True
        pool.accounts = [
            mk("act@x.com", "active", created=now),
            mk("spare@x.com", "ready", created=now),
        ]
        pool.save()
        assert pool.remove("act@x.com") is False, "在用号不允许直接删除"
        assert pool.get_active()["email"] == "act@x.com", "在用号删除尝试不得生效"
        assert pool.remove("nobody@x.com") is False, "不存在的账号应返回 False"
        assert pool.remove("spare@x.com") is True, "备用号应可正常删除"
        assert {a["email"] for a in pool.all()} == {"act@x.com"}
        print("[6] remove 原子保护在用号 / 返回值准确 ✓")

    print("\n== set_active 旧号淘汰测试通过 ==")


if __name__ == "__main__":
    main()
