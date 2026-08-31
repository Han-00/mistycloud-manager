# -*- coding: utf-8 -*-
"""云 API 自测：登录 → 用户信息 → 订阅拉取 → 节点解析 → 流量头。"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Desktop_Files\mistycloud_manager")

from account_pool import AccountPool, accounts_path
from config import Config
from cloud_api import CloudAccount


def main():
    cfg = Config()
    pool = AccountPool(cfg)
    accs = pool.all()
    print(f"[0] 账号库 {len(accs)} 个（来自 {accounts_path()}）")
    # 挑最新的账号（最后创建的）
    acc = max(accs, key=lambda a: float(a.get("created_at") or 0))
    print(f"[1] 测试账号: {acc['email']} status={acc['status']}")

    cloud = CloudAccount(acc["email"], acc.get("password", ""))
    ok = cloud.login()
    print(f"[2] login: {ok}")
    print(f"    uid={cloud.auth_uid!r} key={cloud.auth_key[:6]!r}... expire_in={cloud.expire_in}")
    print(f"    sub_url={cloud.sub_url!r}")
    print(f"    plan={cloud.plan!r} class_expire={cloud.class_expire} traffic={cloud.traffic}")
    if not ok:
        print(f"    login_error_code: {cloud.login_error_code()!r}")

    if cloud.sub_url:
        ok2 = cloud.fetch_subscription(retries=1, delay=1)
        print(f"[3] fetch_subscription: {ok2}")
        print(f"    node: {json.dumps(cloud.node, ensure_ascii=False)}")
        tr = cloud.fetch_traffic()
        print(f"[4] fetch_traffic: {tr}")

    print("\n== 云 API 自测完成 ==")

if __name__ == "__main__":
    main()
