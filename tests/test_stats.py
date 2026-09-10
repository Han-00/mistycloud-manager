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

# ---- [14] 链路可用率：探测计数 ----
sa = stats.Stats(os.path.join(_TMP, "stats_avail.json"))
sa.record_probe(True)
sa.record_probe(True)
sa.record_probe(False)
av = sa.availability(24)
assert av["ok"] == 2 and av["total"] == 3, av
assert av["pct"] == round(2 / 3 * 100, 1) == 66.7, av
print("[14] 可用率：分母是探测次数（2/3 → 66.7%）✓")

# ---- [15] 无数据时 pct 必须是 None，不能是 0 ----
# 「没测过」和「全挂」是两件事：混成 0% 会让新装的用户一开机就看到
# 一个红色的「0% 可用率」，据此误判链路坏了。
empty = stats.Stats(os.path.join(_TMP, "stats_empty.json"))
assert empty.availability(24) == {"pct": None, "ok": 0, "total": 0}, empty.availability(24)
assert empty.snapshot().get("probe") is None or empty.snapshot()["probe"] == {}
print("[15] 无探测数据 → pct=None（区别于 0%）✓")

# ---- [16] 窗口边界：25 小时前的桶不计入，当前桶计入 ----
now = _time.time()
hk_now = _time.strftime("%Y-%m-%dT%H", _time.localtime(now))
hk_old = _time.strftime("%Y-%m-%dT%H", _time.localtime(now - 25 * 3600))
sa.data["probe"] = {hk_now: {"ok": 1, "n": 2}, hk_old: {"ok": 0, "n": 10}}
av = sa.availability(24)
assert av == {"pct": 50.0, "ok": 1, "total": 2}, av
print("[16] 24h 窗口外的桶不计入（旧桶的 0/10 未拖低可用率）✓")

# ---- [17] 时钟回拨产生的「未来桶」不计入 ----
hk_future = _time.strftime("%Y-%m-%dT%H", _time.localtime(now + 3 * 3600))
sa.data["probe"] = {hk_now: {"ok": 1, "n": 1}, hk_future: {"ok": 0, "n": 50}}
av = sa.availability(24)
assert av == {"pct": 100.0, "ok": 1, "total": 1}, av
print("[17] 未来桶（系统时钟回拨）不计入 ✓")

# ---- [18] 脏数据容错：桶不是 dict / 键不是合法时间 → 跳过而非抛 ----
sa.data["probe"] = {
    hk_now: {"ok": 1, "n": 4},
    "not-a-time": {"ok": 0, "n": 9},
    "2026-13-45T99": {"ok": 0, "n": 9},
    hk_old: "garbage",
}
av = sa.availability(24)
assert av == {"pct": 25.0, "ok": 1, "total": 4}, av
print("[18] 可用率对脏数据健壮（坏键/非 dict 桶均跳过）✓")

# ---- [19] probe 随小时桶一起被修剪 ----
# 可用率与流量共用一个 8 天保留期，否则 probe 会无上限增长。
s7 = stats.Stats(os.path.join(_TMP, "stats_prune_probe.json"))
hk_gone = _time.strftime("%Y-%m-%dT%H",
                         _time.localtime(_time.time() - 30 * 86400))
s7.data["probe"] = {hk_gone: {"ok": 0, "n": 5}}
s7.record_probe(True)          # 触发一次修剪
assert hk_gone not in (s7.snapshot().get("probe") or {}), \
    f"30 天前的 probe 桶未被修剪: {s7.snapshot().get('probe')}"
print("[19] 过期 probe 桶随修剪一并清除 ✓")

# ---- [20] 多实例隔离（回归：_DEFAULT 浅拷贝导致嵌套字典跨实例共享）----
# 曾经的写法 `self.data = dict(_DEFAULT)` 只复制了顶层：hourly / probe 这几个
# {} 是所有实例共享的同一个对象，B 往里面写一桶 A 立刻"看见"，保存时还会把
# 对方数据写进自己的文件。生产只有单例所以一直没露头，是 [15] 顺带撞出来的。
ia = stats.Stats(os.path.join(_TMP, "stats_iso_a.json"))
ib = stats.Stats(os.path.join(_TMP, "stats_iso_b.json"))
ia.record_probe(True)
ia.add_traffic("a@x.com", 100)
ib.record_probe(False)
ib.add_traffic("b@x.com", 100)
assert ib.availability(24)["total"] == 1, f"实例间 probe 串了: {ib.availability(24)}"
assert sum(ib.snapshot()["hourly"].values()) == 0 or \
    len(ib.snapshot()["hourly"]) <= 1, f"实例间 hourly 串了: {ib.snapshot()['hourly']}"
# 反向再确认一次：改 B 不应影响 A
a_before = dict(ia.snapshot()["hourly"])
ib.data["hourly"]["2099-01-01T00"] = 99999
assert ia.snapshot()["hourly"] == a_before, "B 改 hourly 污染了 A"
assert "2099-01-01T00" not in ia.snapshot()["hourly"]
print("[20] 多实例隔离：probe / hourly 不再跨实例共享 ✓")

print("\n== 日报统计测试通过 ==")
