# -*- coding: utf-8 -*-
"""monitor 自愈回归：链路死 → 节点重启自愈 / 换号兜底 / 尊重开关 / 流量与有效期触发。"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time

from _isolated import isolate_global
from config import Config
from account_pool import AccountPool
from v2ray_engine import V2RayEngine
from switcher import Switcher, SwitchResult
from monitor import Monitor


class FakeEngine:
    """可控的引擎桩：模拟 进程存活/端口/出口探测。"""

    def __init__(self):
        self.running = True
        self.port = 10888
        self.port_open = True
        self.exit_ip = "1.2.3.4"
        self.current_node = {"id": "node-aaa", "address": "a", "port": 1}
        self.restart_calls = 0
        self.start_after_restart_ok = True

    def is_running(self):
        return self.running

    def _port_open(self, port):
        return self.port_open

    def check_exit_ip(self, timeout=8):
        return self.exit_ip

    def write_config(self, node):
        pass

    def start(self):
        self.restart_calls += 1
        if self.start_after_restart_ok:
            self.running = True
            self.port_open = True
            return True
        return False

    def wait_port(self, timeout=15):
        return self.running and self.port_open


class FakeSwitcher:
    def __init__(self):
        self.switching = False
        self.auto_calls = []
        self.auto_result = None

    def is_switching(self):
        return self.switching

    def auto_switch(self, reason):
        self.auto_calls.append(reason)
        return self.auto_result


def make_monitor(auto=True):
    cfg = Config()
    cfg.set("auto_switch", auto)
    pool = AccountPool(cfg)
    eng = FakeEngine()
    sw = FakeSwitcher()
    logs = []
    mon = Monitor(cfg, pool, sw, eng, log=logs.append)
    return cfg, pool, eng, sw, mon, logs


def test_healthy_noop():
    """链路正常时：不重启、不换号，last_link 记录 OK。"""
    cfg, pool, eng, sw, mon, logs = make_monitor()
    mon._check_engine()
    assert mon.last_link["ok"] and mon.last_link["ip"] == "1.2.3.4"
    assert eng.restart_calls == 0 and sw.auto_calls == []
    print(f"[1] 链路正常无动作 ✓  last_link={mon.last_link}")


def test_restart_self_heal():
    """第一轮探测失败（瞬时）不动作；第二轮失败 → 当前节点重启自愈，不换号。"""
    cfg, pool, eng, sw, mon, logs = make_monitor()
    eng.running = False  # 引擎死了
    mon._check_engine()
    assert not mon.last_link["ok"]
    assert eng.restart_calls == 0, "首轮失败不应立即动手"
    mon._check_engine()
    assert eng.restart_calls == 1, "连续两轮失败应触发重启自愈"
    assert sw.auto_calls == [], "重启自愈成功就不该换号"
    assert mon.last_link["ok"], "自愈后 last_link 应恢复 OK"
    assert any("自愈" in m or "恢复" in m for m in logs), f"应有恢复留痕: {logs}"
    print(f"[2] 节点重启自愈 ✓  restarts={eng.restart_calls} last_link={mon.last_link}")


def test_switch_fallback():
    """重启也救不活（无节点/启动失败）→ 走自动换号兜底。"""
    cfg, pool, eng, sw, mon, logs = make_monitor()
    eng.current_node = None       # 无节点可重启
    sw.auto_result = SwitchResult()
    sw.auto_result.ok = True
    eng.running = False
    mon._check_engine()
    mon._check_engine()
    assert eng.restart_calls == 0
    assert sw.auto_calls == ["代理链路异常"], f"应换号兜底: {sw.auto_calls}"
    print(f"[3] 换号兜底 ✓  auto_calls={sw.auto_calls}")


def test_auto_disabled_respects_toggle():
    """自动换号关闭时：链路死只做节点重启自愈，不擅自换号，并给出明确提示。"""
    cfg, pool, eng, sw, mon, logs = make_monitor(auto=False)
    eng.current_node = None
    eng.running = False
    mon._check_engine()
    mon._check_engine()
    assert sw.auto_calls == [], "自动换号关闭时不得擅自换号"
    assert any("自动换号已关闭" in m for m in logs), f"应提示用户: {logs}"
    print("[4] 尊重自动换号开关 ✓")


def test_skip_while_switching():
    """换号进行中不做健康检查（避免与切换流程互踩）。"""
    cfg, pool, eng, sw, mon, logs = make_monitor()
    sw.switching = True
    eng.running = False
    mon._check_engine()
    assert mon.last_link == {}, "切换中不应探测"
    assert eng.restart_calls == 0
    print("[5] 换号期间跳过探测 ✓")


def test_traffic_fallback_to_subheader():
    """/app/user 缺流量字段时，应兜底订阅响应头并触发换号。"""
    cfg, pool, eng, sw, mon, logs = make_monitor()

    class FakeCloud:
        login_fail_code = ""
        logged_in = True
        class_expire = 0
        traffic = {}  # /app/user 没给流量字段

        def __init__(self, email, password):
            pass

        def login(self):
            return True

        def fetch_traffic(self):
            return {"total": 30 * 1024 * 1024, "upload": 5 * 1024 * 1024,
                    "download": 20 * 1024 * 1024}  # 剩 5MB < 30MB 阈值

    sw.auto_result = SwitchResult()
    sw.auto_result.ok = True
    now = time.time()
    pool.accounts = [{"email": "a@x.com", "password": "p",
                      "created_at": now, "status": "active"}]
    orig_cloud = sys.modules["monitor"].CloudAccount
    sys.modules["monitor"].CloudAccount = FakeCloud
    try:
        mon._check_switch()
    finally:
        sys.modules["monitor"].CloudAccount = orig_cloud
    assert sw.auto_calls and "流量不足" in sw.auto_calls[0], f"应触发换号: {sw.auto_calls}"
    print(f"[6] 订阅头流量兜底触发换号 ✓  reason={sw.auto_calls[0]}")


def test_class_expire_triggers_switch():
    """服务端套餐即将到期（早于客户端 24h 生命周期）→ 触发换号。"""
    cfg, pool, eng, sw, mon, logs = make_monitor()
    cfg.set("account_lifetime_seconds", 86400)
    sw.auto_result = SwitchResult()
    sw.auto_result.ok = True
    now = time.time()
    # 客户端生命周期刚过 1 小时，服务端套餐 10 分钟后到期
    pool.accounts = [{"email": "a@x.com", "password": "p",
                      "created_at": now - 3600, "status": "active",
                      "class_expire": now + 600}]
    sw.auto_calls = []

    class FakeCloud:
        login_fail_code = ""
        logged_in = True
        class_expire = now + 600
        traffic = {"total": 300 * 1024 * 1024, "upload": 0, "download": 1024}

        def __init__(self, email, password):
            pass

        def login(self):
            return True

        def fetch_traffic(self):
            return {}

    orig_cloud = sys.modules["monitor"].CloudAccount
    sys.modules["monitor"].CloudAccount = FakeCloud
    try:
        mon._check_switch()
    finally:
        sys.modules["monitor"].CloudAccount = orig_cloud
    assert sw.auto_calls and "有效期不足" in sw.auto_calls[0], \
        f"服务端套餐到期应触发换号: {sw.auto_calls}"
    print(f"[7] class_expire 到期触发换号 ✓  reason={sw.auto_calls[0]}")


def main():
    isolate_global()  # 进程级账号库隔离（save 永不落真实文件）
    test_healthy_noop()
    test_restart_self_heal()
    test_switch_fallback()
    test_auto_disabled_respects_toggle()
    test_skip_while_switching()
    test_traffic_fallback_to_subheader()
    test_class_expire_triggers_switch()
    print("\n== monitor 自愈回归测试全部通过 ==")


if __name__ == "__main__":
    main()
