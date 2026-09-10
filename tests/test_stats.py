# -*- coding: utf-8 -*-
"""日报统计离线测试（隔离临时文件，不碰真实 stats.json）。

验证：
  - 换号成功/失败计数
  - 流量增量累加：首观设基线不计数 / 增量幂等 / 回退不记负
  - 换号（新账号）重置基线不计数
  - 日期翻转：旧日计数固化进 pending、当日重置、基线与防重日期保留
  - mark_reported 防重复推送；无采样日 touch 也产出 pending
  - 持久化：重开文件能读回状态
  - fmt_bytes 格式化
"""
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Desktop_Files\mistycloud_manager")

import stats  # noqa: E402

_TMP = tempfile.mkdtemp()
P1 = os.path.join(_TMP, "stats1.json")

s = stats.Stats(P1)
s._today = lambda: "2026-08-30"

# ---- [1] 换号计数 ----
s.touch()
s.record_switch(True)
s.record_switch(True)
s.record_switch(False)
d = s.snapshot()
assert d["switch_ok"] == 2 and d["switch_fail"] == 1, f"计数错误: {d}"
print("[1] 换号成功/失败计数 ✓")

# ---- [2] 流量增量累加 ----
s.add_traffic("a@x.com", 1000)          # 首次观测：只设基线不计数
assert s.snapshot()["traffic_used"] == 0, "首观不应计数"
s.add_traffic("a@x.com", 1500)          # +500
assert s.snapshot()["traffic_used"] == 500
s.add_traffic("a@x.com", 1500)          # 幂等：重复采样增量为 0
assert s.snapshot()["traffic_used"] == 500
s.add_traffic("a@x.com", 800)           # 计数器回退：只重置基线不记负
d = s.snapshot()
assert d["traffic_used"] == 500 and d["traffic_base"]["a@x.com"] == 800
s.add_traffic("a@x.com", 900)           # +100（基于新基线）
assert s.snapshot()["traffic_used"] == 600
print("[2] 流量增量：首观基线 / 幂等 / 回退不记负 ✓")

# ---- [3] 换号：新账号重置基线不计数 ----
s.add_traffic("b@x.com", 50)            # 新号首观，不计数
assert s.snapshot()["traffic_used"] == 600
s.add_traffic("b@x.com", 70)            # +20
assert s.snapshot()["traffic_used"] == 620
print("[3] 换号重置基线不计数 ✓")

# ---- [4] 日期翻转：固化 pending + 重置当日 + 保留基线 ----
s._today = lambda: "2026-08-31"
s.touch()
d = s.snapshot()
assert d["switch_ok"] == 0 and d["switch_fail"] == 0 and d["traffic_used"] == 0
assert d["pending_report"] == {"date": "2026-08-30", "switch_ok": 2,
                               "switch_fail": 1, "traffic_used": 620}, d["pending_report"]
assert d["traffic_base"].get("b@x.com") == 70, "流量基线必须跨天保留"
# 新一天继续计数
s.record_switch(True)
s.add_traffic("b@x.com", 200)           # 200-70 = +130
d = s.snapshot()
assert d["switch_ok"] == 1 and d["traffic_used"] == 130
print("[4] 日期翻转：固化上一日 / 重置当日 / 基线保留 ✓")

# ---- [5] mark_reported 防重复推送 ----
assert s.get_pending() is not None
assert not s.is_reported_today()
s.mark_reported()
assert s.is_reported_today()
assert s.get_pending() is None
print("[5] mark_reported 清待推送 + 防重 ✓")

# ---- [6] 无采样日：touch 也产出 pending ----
P2 = os.path.join(_TMP, "stats2.json")
s2 = stats.Stats(P2)
s2._today = lambda: "2026-09-01"
s2.touch()
s2.record_switch(True)
s2._today = lambda: "2026-09-02"
s2.touch()                              # 当天无任何换号/流量采样
p = s2.get_pending()
assert p and p["date"] == "2026-09-01" and p["switch_ok"] == 1, p
print("[6] 无采样日 touch 仍产出上一日 pending ✓")

# ---- [7] 持久化：重开文件读回状态 ----
s3 = stats.Stats(P1)                    # 不注入 _today，用真实日期
s3._today = lambda: "2026-08-31"
assert s3.is_reported_today(), "防重日期必须持久化"
d = s3.snapshot()
assert d["switch_ok"] == 1 and d["traffic_used"] == 130
assert d["traffic_base"].get("b@x.com") == 200
print("[7] 持久化读回 ✓")

# ---- [8] 模块级直通函数（单例指到临时文件）----
P3 = os.path.join(_TMP, "stats3.json")
stats._inst = None
stats.stats_path = lambda: P3
stats.record_switch(True)
stats.add_traffic("c@x.com", 10)
stats.add_traffic("c@x.com", 30)
assert stats.get().snapshot()["traffic_used"] == 20
print("[8] 模块级直通函数 ✓")

# ---- [9] fmt_bytes ----
assert stats.fmt_bytes(512) == "512B"
assert stats.fmt_bytes(2048) == "2.0KB"
assert stats.fmt_bytes(5 * 1024 * 1024) == "5.0MB"
assert stats.fmt_bytes(int(1.5 * 1024 ** 3)) == "1.50GB"
assert stats.fmt_bytes("abc") == "-"
print("[9] fmt_bytes ✓")

# ---- [10] 小时桶：增量入桶 / 幂等不入桶 / 回退不入桶 ----
import time as _time

P4 = os.path.join(_TMP, "stats4.json")
s4 = stats.Stats(P4)
now = _time.time()
hk_now = _time.strftime("%Y-%m-%dT%H", _time.localtime(now))
hk_prev = _time.strftime("%Y-%m-%dT%H", _time.localtime(now - 3600))
s4._hour_key = lambda: hk_prev
s4.add_traffic("h@x.com", 100)          # 首观只设基线
s4.add_traffic("h@x.com", 250)          # +150 → 上一小时桶
s4._hour_key = lambda: hk_now
s4.add_traffic("h@x.com", 250)          # 幂等：增量 0，不入桶
s4.add_traffic("h@x.com", 100)          # 回退：不记负，不入桶
s4.add_traffic("h@x.com", 140)          # +40 → 当前小时桶
snap = s4.snapshot()
assert snap["hourly"] == {hk_prev: 150, hk_now: 40}, snap["hourly"]
print("[10] 小时桶：增量入桶 / 幂等 / 回退 ✓")

# ---- [11] 小时桶持久化 + series 升序 ----
s5 = stats.Stats(P4)
series = s5.hourly_series(7)
assert series == [[hk_prev, 150], [hk_now, 40]], series
print("[11] 小时桶持久化 + series 升序 ✓")

# ---- [12] 修剪：8 天前的桶被清掉 ----
hk_old = _time.strftime("%Y-%m-%dT%H",
                        _time.localtime(now - 9 * 86400))
s5.data["hourly"][hk_old] = 999
s5._hour_key = lambda: hk_now
s5.add_traffic("h@x.com", 150)          # +10，触发修剪
snap = s5.snapshot()
assert hk_old not in snap["hourly"], "过期桶应被修剪"
assert snap["hourly"].get(hk_now) == 50, snap["hourly"]
print("[12] 过期小时桶修剪 ✓")

# ---- [13] avg_daily_bytes：当天按已流逝折算 / 无数据 None ----
P5 = os.path.join(_TMP, "stats5.json")
s6 = stats.Stats(P5)
assert s6.avg_daily_bytes(3) is None, "无数据应为 None"
lt = _time.localtime(now)
frac = max((lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec) / 86400.0, 1/24)
s6.data["hourly"] = {hk_now: 1000}      # 仅今天有数据
avg = s6.avg_daily_bytes(3)
# 测试与实现各自取 localtime，存在秒级漂移 → 用 1% 相对容差
assert abs(avg - 1000 / frac) / (1000 / frac) < 0.01, (avg, frac)
# 加昨天一整天 2000 → (2000 + 1000) / (1 + frac)
hk_yday = _time.strftime("%Y-%m-%dT%H",
                         _time.localtime(now - 86400))
s6.data["hourly"][hk_yday] = 2000
avg = s6.avg_daily_bytes(3)
assert abs(avg - 3000 / (1 + frac)) / (3000 / (1 + frac)) < 0.01, avg
print("[13] avg_daily_bytes 折算 ✓")

print("\n== 日报统计测试通过 ==")
