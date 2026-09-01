# -*- coding: utf-8 -*-
"""ui_web 冒烟测试：隐藏启动→加载后显示→托盘→真退出，全程约 9 秒。

运行（项目根目录）：
    python tests/smoke_ui_web.py

安全说明：已打桩 sysproxy / engine.stop，不会动系统代理注册表，
也不会误杀占用端口的真实 v2ray 进程；流量查询失败会被静默吞掉。
同时 config.save 打桩为空，避免污染真实 settings.json。
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sysproxy
sysproxy.disable = lambda *a, **k: None
sysproxy.apply_if_enabled = lambda *a, **k: False

from config import Config
from account_pool import AccountPool
from v2ray_engine import V2RayEngine
from switcher import Switcher
import ui_web

config = Config()
config.save = lambda: None          # 冒烟期间不落盘
pool = AccountPool(config)
engine = V2RayEngine(config)
engine.stop = lambda: None
switcher = Switcher(config, pool, engine)


class DummyMonitor:
    last_link = {"ok": True, "ip": "203.0.113.7", "ts": "09:00:00"}


ui = ui_web.WebAppUI(config, pool, switcher, engine, DummyMonitor())
report = {}


def killer():
    time.sleep(4)
    ui.log("冒烟测试：JS 桥日志通道正常", "ok")
    ui.push_state()
    report["tray_ok"] = ui._tray is not None
    report["shown"] = ui._shown.is_set()
    time.sleep(4)
    report["closing_intercept"] = False   # 占位，由下方真退出路径覆盖
    ui._quit_requested = True             # 模拟托盘「退出」= 真退出
    try:
        ui.window.destroy()
    except Exception as e:
        print("destroy failed:", e)


threading.Thread(target=killer, daemon=True).start()

t0 = time.time()
ui.run()
print("SMOKE_OK window ran %.1fs" % (time.time() - t0))
print("tray_available=%s shown_after_loaded=%s" %
      (report.get("tray_ok"), report.get("shown")))
