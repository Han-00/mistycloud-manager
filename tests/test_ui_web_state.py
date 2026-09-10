# -*- coding: utf-8 -*-
"""Web 前端（ui_web）状态快照 + 删除保护规则测试。

为什么值得测：ui_web 是现在实际在用的界面（回退链第一级），但此前只有
「窗口能起来」的冒烟测试——`build_state` 的输出结构、删除账号的保护规则
这些纯逻辑完全没覆盖，而它们出错**冒烟测试根本发现不了**：窗口照样打开，
只是账号列表少算一个、或者正在用的号被删掉导致代理当场断掉。

做法：`WebAppUI.__init__` 只存引用、不建窗口、不起线程，所以可以直接实例化，
用桩对象控制输入。不弹窗、不连网、不落盘。
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import account_pool  # noqa: E402
import autostart  # noqa: E402
import stats  # noqa: E402

_TMP_JSON = os.path.join(tempfile.mkdtemp(), "accounts.json")
account_pool.accounts_path = lambda: _TMP_JSON

from ui_web import WebAppUI  # noqa: E402

MB = 1024 * 1024


class _Cfg:
    def __init__(self, **kw):
        self.d = {"account_lifetime_seconds": 86400.0,
                  "expiry_threshold_seconds": 1800.0,
                  "min_traffic_mb": 30.0,
                  "auto_switch": True, "system_proxy": False}
        self.d.update(kw)

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v

    def save(self):
        pass


class _Sw:
    def __init__(self, switching=False):
        self._s = switching

    def is_switching(self):
        return self._s


class _Eng:
    port = 10808

    def __init__(self, running=False):
        self._r = running

    def is_running(self):
        return self._r


def mk(email, status, created=None, ce=0):
    return {"email": email, "password": "x", "last_used_at": 0,
            "status": status, "class_expire": ce,
            "created_at": time.time() if created is None else created}


def build(accounts, cfg=None, switching=False, running=False, monitor=None):
    pool = account_pool.AccountPool(_Cfg())
    pool.accounts = accounts
    ui = WebAppUI(cfg or _Cfg(), pool, _Sw(switching), _Eng(running), monitor)
    return ui, pool


def main():
    now = time.time()

    # ---- [1] build_state 输出结构完整 ----
    ui, pool = build([mk("a@x.com", "active", now)])
    st = ui.build_state()
    for key in ("active", "engine_running", "port", "link", "traffic",
                "traffic_heatmap", "fuel_days", "expire_ts", "expiry_threshold_s",
                "auto_on", "sysproxy_on", "valid_count", "switching", "accounts"):
        assert key in st, f"状态快照缺字段: {key}"
    assert st["active"] == "a@x.com", st["active"]
    assert st["port"] == 10808 and st["engine_running"] is False
    assert st["auto_on"] is True and st["sysproxy_on"] is False
    assert st["expiry_threshold_s"] == 1800.0
    print("[1] build_state 字段齐全、基础映射正确 ✓")

    # ---- [2] 账号行字段：有效性 / 剩余时长 / 百分比 ----
    ui, pool = build([
        mk("ok@x.com", "ready", now),            # 有效
        mk("old@x.com", "ready", now - 90000),   # 超生命周期 → 不可用
        mk("bad@x.com", "banned", now),          # 失效
    ])
    st = ui.build_state()
    rows = {a["email"]: a for a in st["accounts"]}
    assert rows["ok@x.com"]["valid"] is True
    assert rows["old@x.com"]["valid"] is False
    assert rows["bad@x.com"]["valid"] is False
    assert st["valid_count"] == 1, st["valid_count"]
    # 剩余时长应随时钟递减、且不为负
    assert 86000 < rows["ok@x.com"]["remain_s"] <= 86400, rows["ok@x.com"]
    assert rows["old@x.com"]["remain_s"] == 0.0, rows["old@x.com"]
    assert 0 <= rows["ok@x.com"]["remain_pct"] <= 100
    print("[2] 账号行 valid / remain_s / remain_pct 计算正确 ✓")

    # ---- [3] 无在用账号 ----
    ui, pool = build([mk("r@x.com", "ready", now)])
    st = ui.build_state()
    assert st["active"] is None, st["active"]
    print("[3] 无在用账号时 active=None ✓")

    # ---- [4] 流量缓存 → 状态（含低流量标记与续航预测）----
    stats.hourly_series = lambda days: [{"date": "2026-09-10", "hours": [0] * 24}]
    stats.avg_daily_bytes = lambda days: 50 * MB
    ui, pool = build([mk("a@x.com", "active", now)])
    ui._traffic = (300 * MB, 200 * MB)          # 剩 100MB
    st = ui.build_state()
    assert st["traffic"]["remain"] == 100 * MB, st["traffic"]
    assert st["traffic"]["low"] is False, "100MB 高于 30MB 阈值，不该标低"
    assert st["fuel_days"] == 2.0, st["fuel_days"]   # 100MB / 50MB每天
    assert st["traffic_heatmap"], "热力图数据应透传"

    ui._traffic = (300 * MB, 290 * MB)          # 剩 10MB < 30MB 阈值
    assert ui.build_state()["traffic"]["low"] is True, "低于阈值应标记 low"
    print("[4] 流量余量 / 低量标记 / 续航预测正确 ✓")

    # ---- [5] 查询失败 → 走 err 分支，不误报流量 ----
    ui._traffic, ui._traffic_err = None, "查询失败（登录失败）"
    st = ui.build_state()
    assert st["traffic"] == {"err": "查询失败（登录失败）"}, st["traffic"]
    assert st["fuel_days"] is None, "无流量数据时不该给出续航预测"
    print("[5] 查询失败走 err 分支、不给续航预测 ✓")

    # ---- [6] 删除保护：在用号不能删 ----
    ui, pool = build([mk("act@x.com", "active", now), mk("r@x.com", "ready", now)])
    ui.delete_account("act@x.com")
    assert {a["email"] for a in pool.all()} == {"act@x.com", "r@x.com"}, \
        "在用号被删掉了！代理会当场断链"
    ui.delete_account("r@x.com")
    assert {a["email"] for a in pool.all()} == {"act@x.com"}, "普通账号应能删掉"
    ui.delete_account("nope@x.com")             # 不存在的账号：只提示，不炸
    print("[6] 删除保护：在用号拒绝删除，普通号可删，不存在不炸 ✓")

    # ---- [7] 切换进行中：拒绝删除 ----
    ui, pool = build([mk("r@x.com", "ready", now)], switching=True)
    ui.delete_account("r@x.com")
    ui.delete_inactive()
    assert pool.count() == 1, "切换进行中不应删除任何账号"
    print("[7] 换号进行中拒绝删除/清理 ✓")

    # ---- [8] 批量清理只动 expired / banned，绝不碰在用号 ----
    ui, pool = build([
        mk("act@x.com", "active", now - 999999),   # 在用（即使已过期）
        mk("exp@x.com", "expired", now),
        mk("ban@x.com", "banned", now),
        mk("ok@x.com", "ready", now),
    ])
    ui.delete_inactive()
    left = {a["email"] for a in pool.all()}
    assert left == {"act@x.com", "ok@x.com"}, f"清理范围错误: {left}"
    print("[8] 批量清理只删 expired/banned，在用号与有效备用保留 ✓")

    # ---- [9] toast 推送：确实把 JS 排进了队列，且成功/失败标记正确 ----
    ui, pool = build([mk("a@x.com", "active", now)])
    while not ui._q.empty():
        ui._q.get()
    ui.toast("换号成功 a@x.com")
    ui.toast("换号失败: 超时", ok=False)
    js = []
    while not ui._q.empty():
        js.append(ui._q.get())
    assert len(js) == 2, f"应推送 2 条 toast，实际 {len(js)}: {js}"
    assert all("App.toast(" in s for s in js), js
    assert js[0].rstrip().endswith("true)"), f"成功提示应为 true: {js[0]}"
    assert js[1].rstrip().endswith("false)"), f"失败提示应为 false: {js[1]}"
    print("[9] toast 推送：入队 2 条、成功/失败标记正确 ✓")

    # ---- [10] get_settings 带 autostart，且与 autostart 模块同源 ----
    ui, pool = build([mk("a@x.com", "active", now)])
    s = ui.get_settings()
    assert "autostart" in s, f"设置里缺 autostart: {s.keys()}"
    assert s["autostart"] == autostart.is_enabled(), \
        "get_settings 的 autostart 必须直接来自 autostart 模块，不能另算一份"
    print("[10] get_settings 含 autostart 且与 autostart 模块同源 ✓")

    # ---- [11] set_autostart：成功 / 底层抛异常都不能崩，且失败要有明确返回 ----
    ui, pool = build([mk("a@x.com", "active", now)])
    _orig_set = autostart.set_enabled
    try:
        autostart.set_enabled = lambda on: None
        assert ui.set_autostart(True) == {"ok": True, "error": None}

        def _boom(_on):
            raise PermissionError("拒绝访问")
        autostart.set_enabled = _boom
        r = ui.set_autostart(True)
        assert r["ok"] is False and r["error"], f"注册表受限应返回失败: {r}"
    finally:
        autostart.set_enabled = _orig_set
    print("[11] set_autostart 成功/受限两条路径都不崩、失败有明确返回 ✓")

    print("\n== Web 前端状态与删除保护测试通过 ==")


if __name__ == "__main__":
    main()
