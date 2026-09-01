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

print("\n== 日报统计测试通过 ==")
