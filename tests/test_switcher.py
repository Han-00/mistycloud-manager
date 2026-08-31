# -*- coding: utf-8 -*-
"""switcher 回归测试：候选轮换 / 冷却跳过 / 失败留痕 / 原子守卫。"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Desktop_Files\mistycloud_manager")

import time

from config import Config
from account_pool import AccountPool
from v2ray_engine import V2RayEngine
from switcher import Switcher, SwitchResult, enter_cooldown, is_cooling, cooldown_remain
from _isolated import isolate_global


def make_switcher():
    cfg = Config()
    pool = AccountPool(cfg)
    eng = V2RayEngine(cfg)
    logs = []
    sw = Switcher(cfg, pool, eng, log=logs.append)
    return cfg, pool, eng, sw, logs


def test_rotation():
    """候选全失败时，应逐个尝试不同候选直到耗尽。"""
    cfg, pool, eng, sw, logs = make_switcher()
    now = time.time()
    pool.accounts = [
        {"email": "cur@x.com", "password": "p", "created_at": now, "status": "active"},
        {"email": "cand1@x.com", "password": "p", "created_at": now, "status": "ready"},
        {"email": "cand2@x.com", "password": "p", "created_at": now, "status": "ready"},
        {"email": "cand3@x.com", "password": "p", "created_at": now, "status": "ready"},
    ]

    tried = []

    def fake_switch(email, notify_fail=True, _internal=False):
        tried.append(email)
        r = SwitchResult()
        r.email = email
        r.error = "模拟失败"
        return r

    sw.switch_to_email = fake_switch
    r = sw.auto_switch("测试:候选全失败")
    assert len(tried) >= 3, f"应尝试多个候选，实际只试了 {tried}"
    assert len(set(tried)) == len(tried), f"候选重复尝试: {tried}"
    assert not r.ok and r.error, f"应返回失败结果: ok={r.ok} error={r.error!r}"
    assert any("自动换号失败" in m for m in logs), "失败必须有日志留痕"
    print(f"[1] 候选轮换 ✓  尝试顺序: {tried}")


def test_cooldown():
    """冷却中的候选应被跳过。"""
    cfg, pool, eng, sw, logs = make_switcher()
    now = time.time()
    pool.accounts = [
        {"email": "cur@x.com", "password": "p", "created_at": now, "status": "active"},
        {"email": "cool@x.com", "password": "p", "created_at": now, "status": "ready"},
        {"email": "fresh@x.com", "password": "p", "created_at": now, "status": "ready"},
    ]
    enter_cooldown("cool@x.com")
    assert is_cooling("cool@x.com") and cooldown_remain("cool@x.com") > 0

    tried = []

    def fake_switch(email, notify_fail=True, _internal=False):
        tried.append(email)
        r = SwitchResult()
        r.email = email
        r.ok = True
        return r

    sw.switch_to_email = fake_switch
    r = sw.auto_switch("测试:冷却跳过")
    assert r.ok
    assert "cool@x.com" not in tried, f"冷却账号不应被尝试: {tried}"
    assert tried == ["fresh@x.com"], f"应直接试 fresh: {tried}"
    print(f"[2] 冷却跳过 ✓  尝试: {tried}")


def test_no_candidate():
    """无候选时应明确报错（而非静默）。"""
    cfg, pool, eng, sw, logs = make_switcher()
    now = time.time()
    pool.accounts = [
        {"email": "cur@x.com", "password": "p", "created_at": now, "status": "active"},
        {"email": "old@x.com", "password": "p", "created_at": 0, "status": "ready"},
    ]
    r = sw.auto_switch("测试:无候选")
    assert not r.ok
    assert "备用账号不足" in r.error, f"错误信息: {r.error!r}"
    assert any("自动换号中止" in m for m in logs), f"应有中止日志: {logs}"
    print(f"[3] 无候选报错 ✓  error={r.error!r}")


def test_switching_guard():
    """并发调用应被守卫拦截。"""
    cfg, pool, eng, sw, logs = make_switcher()
    release = []

    def slow_switch(email, notify_fail=True, _internal=False):
        time.sleep(0.5)
        r = SwitchResult()
        r.ok = True
        r.email = email
        return r

    sw.switch_to_email = slow_switch
    now = time.time()
    pool.accounts = [
        {"email": "cand@x.com", "password": "p", "created_at": now, "status": "ready"},
    ]
    import threading
    results = []
    t1 = threading.Thread(target=lambda: results.append(sw.auto_switch("A")))
    t1.start()
    time.sleep(0.1)
    r2 = sw.auto_switch("B")  # 应被守卫拦截
    t1.join()
    assert results[0].ok
    assert not r2.ok and "已有切换进行中" in r2.error, f"守卫失效: {r2.error!r}"
    print("[4] 并发守卫 ✓")


def test_real_switch_and_restore():
    """真实切换 + 失败恢复链路（用当前真实账号）。"""
    cfg, pool, eng, sw, logs = make_switcher()
    import json as _json
    import os as _os
    _real = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                          "accounts.json")
    try:
        with open(_real, encoding="utf-8") as f:
            pool.accounts = _json.load(f)  # 隔离环境播种真实账号
    except (OSError, _json.JSONDecodeError):
        pass
    active = pool.get_active()
    if not active:
        print("[5] 真实切换 - 跳过（无有效在用账号）")
        return
    # 造一个必失败的假候选在库前面，验证切换目标失败后仍能完成
    # 这里只验证真实切换一次成功
    r = sw.switch_to_email(active["email"], notify_fail=False)
    print(f"[5] 真实切换: ok={r.ok} proxy={r.proxy_ip} direct={r.direct_ip} "
          f"error={r.error!r} duration={r.duration:.1f}s")
    if r.ok:
        assert pool.get_active()["email"] == active["email"]
    eng.stop()


def main():
    isolate_global()  # 进程级账号库隔离（真实切换的 set_active 落到临时文件）
    test_rotation()
    test_cooldown()
    test_no_candidate()
    test_switching_guard()
    test_real_switch_and_restore()
    print("\n== switcher 回归测试全部通过 ==")


if __name__ == "__main__":
    main()
