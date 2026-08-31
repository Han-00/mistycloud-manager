# -*- coding: utf-8 -*-
"""场景回归：坏节点切换失败 → 原节点链路恢复；端口变更 → 代理平滑重启。"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _isolated import isolated_accounts
from config import Config
from account_pool import AccountPool
from v2ray_engine import V2RayEngine
from switcher import Switcher


def main():
    # 账号库隔离：测试对账号库的任何修改（含假账号注入）都不落回真实 accounts.json
    real_accounts_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "accounts.json")
    with isolated_accounts():
        cfg = Config()
        pool = AccountPool(cfg)
        try:
            with open(real_accounts_file, encoding="utf-8") as f:
                pool.accounts = json.load(f)  # 快照真实账号到隔离环境
        except (OSError, json.JSONDecodeError):
            pass
        _run(cfg, pool)


def _run(cfg, pool):
    eng = V2RayEngine(cfg)
    sw = Switcher(cfg, pool, eng, log=lambda m: print(f"  [SW] {m}"))

    active = pool.get_active()
    assert active, "需要有效在用账号"
    good_node_cloud_email = active["email"]

    # 1) 先用真实账号建立"现有好链路"
    r = sw.switch_to_email(active["email"], notify_fail=False)
    assert r.ok, f"建立基线链路失败: {r.error}"
    good_node = eng.current_node
    print(f"[1] 基线链路: {r.proxy_ip} 节点={good_node['id'][:8]}...")

    # 2) 构造一个"订阅能过但链路必死"的假节点（随机 UUID → 上游拒绝）
    bad_node = dict(good_node)
    bad_node["id"] = "00000000-0000-0000-0000-000000000000"
    fake_acc_email = "fake-badnode@x.com"
    now = time.time()
    pool.accounts.append({"email": fake_acc_email, "password": "x",
                          "created_at": now, "status": "ready"})

    real_login = sw.pool.set_class_expire  # 保留引用避免误伤
    # 劫持登录/订阅层：假账号直接返回好订阅+坏节点
    from cloud_api import CloudAccount
    orig_init = CloudAccount.__init__

    def fake_init(self, email="", password=""):
        orig_init(self, email, password)

    CloudAccount.__init__ = fake_init
    sw.login_stub = True

    # 更直接：monkeypatch switcher 内部使用的 CloudAccount 行为
    import switcher as sw_mod

    class FakeCloud(CloudAccount):
        def login(self):
            self.sub_url = "https://fake.example/link/x?sub=3"
            self.class_expire = now + 86400
            self.logged_in = True
            return True

        def fetch_subscription(self, retries=0, delay=0.0):
            self.node = bad_node
            return True

    orig_cloud = sw_mod.CloudAccount
    sw_mod.CloudAccount = FakeCloud
    try:
        r2 = sw.switch_to_email(fake_acc_email, notify_fail=False)
        print(f"[2] 坏节点切换结果: ok={r2.ok} error={r2.error!r}")
        assert not r2.ok, "坏节点不应成功"
    finally:
        sw_mod.CloudAccount = orig_cloud
        CloudAccount.__init__ = orig_init

    # 3) 验证链路已恢复到原节点
    time.sleep(1)
    assert eng.is_running(), "恢复后代理应运行"
    restored = eng.current_node
    assert restored["id"] == good_node["id"], \
        f"应恢复原节点 {good_node['id'][:8]}...，实际 {restored['id'][:8]}..."
    ip = eng.check_exit_ip(timeout=12)
    print(f"[3] 链路恢复: 节点={restored['id'][:8]}... 出口={ip}")
    assert ip == r.proxy_ip, f"恢复后出口应与基线一致: {ip} != {r.proxy_ip}"

    # 4) 端口变更 → 平滑重启
    old_port = eng.port
    cfg.set("proxy_port", 10896)
    eng.write_config(restored)
    assert eng.start() and eng.wait_port(15), "新端口启动失败"
    assert eng.port == 10896 and eng._port_open(10896)
    ip2 = eng.check_exit_ip(timeout=12)
    print(f"[4] 端口变更重启: {old_port} → 10896 出口={ip2}")
    assert ip2 == r.proxy_ip

    # 还原端口
    cfg.set("proxy_port", old_port)
    eng.stop()
    print("\n== 场景回归测试通过 ==")


if __name__ == "__main__":
    main()
