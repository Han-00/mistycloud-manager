# -*- coding: utf-8 -*-
"""自愈冒烟（真实网络）：建链 → 杀 v2ray → 监控自动恢复 → UI 显示链路状态。"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _isolated import isolate_global
from config import Config
from account_pool import AccountPool
from v2ray_engine import V2RayEngine
from switcher import Switcher
from monitor import Monitor
from ui import AppUI


def main():
    isolate_global()  # 账号库隔离（真实切换的 set_active 落临时文件）
    cfg = Config()
    pool = AccountPool(cfg)
    real = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "accounts.json")
    try:
        with open(real, encoding="utf-8") as f:
            pool.accounts = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass

    eng = V2RayEngine(cfg)
    sw = Switcher(cfg, pool, eng)
    mon = Monitor(cfg, pool, sw, eng)
    logs = []
    mon.log = logs.append

    active = pool.get_active()
    assert active, "需要有效在用账号"

    # 1) 真实切换建立基线链路
    r = sw.switch_to_email(active["email"], notify_fail=False)
    assert r.ok, f"建立基线链路失败: {r.error}"
    print(f"[1] 基线链路: 出口 {r.proxy_ip}（{r.duration:.1f}s）")

    # 2) 正常探测：last_link 应 OK 且 IP 一致
    mon._check_engine()
    assert mon.last_link.get("ok") and mon.last_link.get("ip") == r.proxy_ip, \
        f"健康探测异常: {mon.last_link}"
    print(f"[2] 健康探测: {mon.last_link}")

    # 3) 模拟 v2ray 死亡（进程被杀，引擎不知情）
    eng.stop()
    assert not eng.is_running()
    mon._check_engine()   # 第 1 轮：确认失败（不动作）
    assert not mon.last_link["ok"]
    print("[3] 链路死亡: 第 1 轮探测失败（等待确认）")

    # 4) 第 2 轮：触发自愈 → 用当前节点重启 v2ray → 出口恢复
    mon._check_engine()
    assert eng.is_running(), "自愈应重启 v2ray"
    assert mon.last_link["ok"], f"自愈后链路应恢复: {mon.last_link}"
    assert mon.last_link["ip"] == r.proxy_ip, \
        f"恢复后出口应一致: {mon.last_link['ip']} != {r.proxy_ip}"
    assert any("恢复" in m for m in logs), f"应有自愈日志: {logs}"
    print(f"[4] 自愈成功: {mon.last_link}")
    print(f"    日志: {[m for m in logs if '恢复' in m or '异常' in m]}")

    # 5) UI 显示链路状态（对齐 v1.0.4 检查时间戳）
    ui = AppUI(cfg, pool, sw, eng, mon)
    ui._refresh_now()
    ui.root.update()
    txt = ui.lbl_proxy.cget("text")
    print(f"[5] UI 代理状态行: {txt!r}")
    assert "检查于" in txt and "出口" in txt, f"UI 应显示链路详情: {txt!r}"

    # 清理
    eng.stop()
    ui.root.destroy()
    print("\n== 自愈冒烟测试通过 ==")


if __name__ == "__main__":
    main()
