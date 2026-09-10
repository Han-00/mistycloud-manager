# -*- coding: utf-8 -*-
"""main.py 接线等效测试：switcher.log → ui.log 挂钩 + 后台线程跑真实换号。

修复前：ui.log 内 root.after 在线程中必抛 RuntimeError → 换号必失败
        （"切换异常: main thread is not in main loop"）+ 流量面板永不刷新。
修复后：队列轮询，全程无异常，UI 收到完整日志、流量标签正常更新。

测的是**接线纪律**（与 UI 长相无关），不是某个控件的具体样式：
后台线程调 ui.log 不炸、换号全过程日志不丢、异步刷新能更新界面。
故控件文本一律经 _label_text 容错读取——UI 改版（旧版 lbl_traffic →
现版 card_traffic_val）不该让它变成一串假失败；真正硬的断言是
「引擎确实在跑」这类与 UI 无关的事实。

注意：本测试会**真实连网换号并启动 v2ray**（占用代理端口）。
跑之前请先退出正在运行的账号大师 Pro——两边共用同一个 v2ray_work 目录，
端口清理逻辑有可能会把正在跑的那个 v2ray 一起带走。
"""
import json
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Desktop_Files\mistycloud_manager")

from config import Config
from account_pool import AccountPool
from v2ray_engine import V2RayEngine
from switcher import Switcher
from monitor import Monitor
from ui import AppUI
from _isolated import isolate_global


def _label_text(ui, *names):
    """按候选名读控件文本；都读不到返回 None（调用方据此跳过该断言）。

    存在的意义：UI 控件命名随重构变化（lbl_traffic → card_traffic_val），
    写死一个名字会让「接线测试」在每次 UI 改版后假失败，最后没人再跑它。
    """
    for n in names:
        w = getattr(ui, n, None)
        if w is None:
            continue
        try:
            return str(w.cget("text"))
        except Exception:
            continue
    return None


def main():
    isolate_global()  # 进程级账号库隔离
    cfg = Config()
    pool = AccountPool(cfg)
    # 隔离环境下播种真实账号快照（账号库修改只落临时文件）
    _real = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "accounts.json")
    try:
        with open(_real, encoding="utf-8") as f:
            pool.accounts = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    eng = V2RayEngine(cfg)
    sw = Switcher(cfg, pool, eng)
    mon = Monitor(cfg, pool, sw, eng)

    ui = AppUI(cfg, pool, sw, eng, mon)
    # 与 main.py 完全一致的接线
    sw.log = lambda m: ui.log(m)
    mon.log = lambda m: ui.log(m)

    active = pool.get_active()
    if not active:
        print("!! 无在用账号，先注册一个")
        acc = pool.register_one(log=ui.log)
        assert acc, "注册失败"
        active = acc

    result = {}
    errors = []

    # 从后台线程跑真实换号（main.py bootstrap/_manual_switch 的场景）
    def work():
        try:
            r = sw.switch_to_email(active["email"], notify_fail=False)
            result["r"] = r
        except Exception as e:
            errors.append(repr(e))

    t0 = time.time()
    th = threading.Thread(target=work, daemon=True)
    th.start()
    # 主线程泵 UI（真实 mainloop 的替代）
    while th.is_alive() or time.time() - t0 < 3:
        ui.root.update()
        time.sleep(0.02)
        if time.time() - t0 > 120:
            break
    pump_until = time.time() + 2
    while time.time() < pump_until:
        ui.root.update()
        time.sleep(0.02)

    r = result.get("r")
    assert r is not None, f"换号线程未返回: errors={errors}"
    assert not errors, f"线程内异常: {errors}"
    print(f"[1] 线程内真实换号: ok={r.ok} proxy={r.proxy_ip!r} error={r.error!r} "
          f"({r.duration:.1f}s)")
    assert r.ok, f"换号失败: {r.error}"

    # UI 日志应收到换号全过程
    content = ui.log_text.get("1.0", "end")
    for kw in ("登录验证", "登录成功", "拉取订阅", "节点:", "写入 v2ray", "换号成功"):
        assert kw in content, f"UI 日志缺少 [{kw}]，内容:\n{content}"
    print("[2] UI 日志完整接收换号过程 ✓")
    assert "main thread is not in main loop" not in content, "日志中出现跨线程异常"

    # 等流量异步刷新（_schedule_refresh 每 30s，主动触发一次）
    ui._refresh_now()
    deadline = time.time() + 25
    traffic_txt = None
    while time.time() < deadline:
        ui.root.update()
        time.sleep(0.02)
        traffic_txt = _label_text(ui, "card_traffic_val", "lbl_traffic")
        if traffic_txt and ("剩余" in traffic_txt or "查询失败" in traffic_txt):
            break

    link_txt = _label_text(ui, "card_link_val", "lbl_proxy")
    expire_txt = _label_text(ui, "card_expire_val", "lbl_expire")
    print(f"[3] 流量: {traffic_txt!r} | 有效期: {expire_txt!r} | 链路: {link_txt!r}")

    # 硬断言：与 UI 无关的事实
    assert ui.engine.is_running(), "代理应在运行"
    # 控件文本断言：读得到就查，读不到只提示（UI 改版不算接线故障）
    if traffic_txt is None:
        print("    · 未找到流量控件（UI 命名已变），跳过文本断言")
    else:
        assert "剩余" in traffic_txt, f"流量标签未更新: {traffic_txt!r}"
    if link_txt is not None:
        assert ("运行中" in link_txt or "正常" in link_txt), f"链路状态异常: {link_txt!r}"

    # 清理
    eng.stop()
    ui.root.destroy()
    print("\n== main.py 接线等效测试通过 ==")


if __name__ == "__main__":
    main()
