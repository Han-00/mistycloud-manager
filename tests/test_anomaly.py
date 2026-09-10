# -*- coding: utf-8 -*-
"""异常消耗检测离线测试（隔离临时文件，不碰真实 stats.json / events.jsonl）。

验证：
  - 仅阈值判定（基线样本不足时退化）
  - 阈值+基线双确认（偏离同时段均值 N 倍）
  - 常态高消耗不误报（天天这个点都烧这么多 ≠ 异常）
  - 低于阈值不报；多个异常按消耗降序
  - 账号关联：桶内换号实测 / 无换号按最近成功换号推断 / 失败事件不计
  - 空数据与脏数据容错
"""
import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Desktop_Files\mistycloud_manager")

import stats   # noqa: E402
import events  # noqa: E402
import anomaly # noqa: E402

_MB = 1024 * 1024
_TMP = tempfile.mkdtemp()
now = time.time()


def hk(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H", time.localtime(ts))


def fresh_stats(hourly: dict) -> None:
    """把 stats 单例指到临时实例并塞入小时桶（键必须是「近期」小时）。"""
    fake = stats.Stats(os.path.join(_TMP, f"stats_anom_{time.time_ns()}.json"))
    fake.data["hourly"] = hourly
    stats._inst = fake


def write_events(rows: list) -> None:
    """把事件行写进临时 events 文件并重定向 events 读取。"""
    path = os.path.join(_TMP, f"events_{time.time_ns()}.jsonl")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    events.set_path(path)


# ---- [1] 仅阈值：单日 500MB，无基线样本 → basis=threshold ----
t0 = now - 2 * 3600
fresh_stats({hk(t0): 500 * _MB})
r = anomaly.detect(days=7)
assert len(r) == 1, r
assert r[0]["bytes"] == 500 * _MB and r[0]["basis"] == "threshold", r[0]
assert r[0]["baseline"] is None and r[0]["ratio"] is None, r[0]
print("[1] 仅阈值判定（基线样本不足时退化）✓")

# ---- [2] 阈值+基线双确认：平时 10MB，今天 400MB → basis=both ----
hour_today = hk(now)
hour_d1 = hk(now - 1 * 86400)
hour_d2 = hk(now - 2 * 86400)
fresh_stats({hour_d2: 10 * _MB, hour_d1: 10 * _MB, hour_today: 400 * _MB})
r = anomaly.detect(days=7)
assert len(r) == 1, r
assert r[0]["hour"] == hour_today and r[0]["basis"] == "both", r[0]
assert r[0]["baseline"] == 10 * _MB and r[0]["ratio"] == 40.0, r[0]
print("[2] 阈值+基线双确认（同时段均值 3 倍以上）✓")

# ---- [3] 常态高消耗不误报：同小时每天都 300MB（过阈值但没偏离基线）----
fresh_stats({hour_d2: 300 * _MB, hour_d1: 300 * _MB, hour_today: 300 * _MB})
assert anomaly.detect(days=7) == [], "天天如此的高消耗不该报异常"
print("[3] 常态高消耗不误报（基线条件生效）✓")

# ---- [4] 低于阈值不报 + 降序排列 ----
a_big = hk(now - 3 * 3600)            # 今天另一小时 500MB（无基线）
b_mid = hk(now - 86400 - 5 * 3600)    # 昨天 400MB（无基线）
fresh_stats({a_big: 500 * _MB, b_mid: 400 * _MB, hk(t0): 40 * _MB})
r = anomaly.detect(days=7)
assert [x["bytes"] for x in r] == [500 * _MB, 400 * _MB], r
print("[4] 低于阈值不报 + 多异常按消耗降序 ✓")

# ---- [5] 账号关联：桶内换号 → 实测归属 ----
hs = time.mktime(time.strptime(a_big, "%Y-%m-%dT%H"))
write_events([
    {"ts": hs + 600, "kind": "switch", "ok": True,
     "email": "in-hour@x.com"},
])
fresh_stats({a_big: 500 * _MB})
r = anomaly.detect(days=7)
assert r[0]["accounts"] == ["in-hour@x.com"] and r[0]["inferred"] is False, r[0]
print("[5] 账号关联：桶内换号实测归属 ✓")

# ---- [6] 账号关联：桶内没换号 → 最近一次成功换号推断；失败事件不计 ----
write_events([
    {"ts": hs + 600, "kind": "switch", "ok": False,
     "email": "failed@x.com"},                     # 桶内失败换号，不计
    {"ts": hs - 4 * 3600, "kind": "switch", "ok": True,
     "email": "was-active@x.com"},                 # 桶前最近一次成功换号
])
fresh_stats({a_big: 500 * _MB})
r = anomaly.detect(days=7)
assert r[0]["accounts"] == ["was-active@x.com"], r[0]
assert r[0]["inferred"] is True, r[0]
print("[6] 无换号按最近成功换号推断（失败事件不计）✓")

# ---- [7] 桶内多个换号：去重全列，保持顺序 ----
write_events([
    {"ts": hs + 100, "kind": "switch", "ok": True, "email": "a@x.com"},
    {"ts": hs + 1200, "kind": "switch", "ok": True, "email": "b@x.com"},
    {"ts": hs + 1800, "kind": "switch", "ok": True, "email": "a@x.com"},
])
fresh_stats({a_big: 500 * _MB})
r = anomaly.detect(days=7)
assert r[0]["accounts"] == ["a@x.com", "b@x.com"], r[0]
assert r[0]["inferred"] is False
events.set_path(None)                  # 还原 events 读取位置
print("[7] 桶内多换号去重全列 ✓")

# ---- [8] 空数据 / 无异常 ----
fresh_stats({})
assert anomaly.detect(days=7) == []
fresh_stats({hk(now): 5 * _MB})
assert anomaly.detect(days=7) == []
print("[8] 空数据 / 无异常 → 空表 ✓")

# ---- [9] 脏数据容错：非数字桶值跳过不抛 ----
fresh_stats({hk(now): "garbage", hk(now - 3600): 300 * _MB})
r = anomaly.detect(days=7)
assert len(r) == 1 and r[0]["bytes"] == 300 * _MB, r
print("[9] 脏数据容错（坏桶值跳过）✓")

# ---- [10] days 窗口：范围外的桶不参与 ----
old = hk(now - 9 * 86400)              # 9 天前
fresh_stats({old: 500 * _MB, hk(now): 10 * _MB})
assert anomaly.detect(days=7) == [], "9 天前的桶不应参与 7 天扫描"
print("[10] days 窗口外桶不参与 ✓")

print("\n== 异常消耗检测测试通过 ==")
