# -*- coding: utf-8 -*-
"""事件流离线测试（隔离临时文件，不碰真实 events.jsonl）。

验证：
  - record / recent 往返，**新的在前**（前端直接顺序渲染）
  - 空字段不落盘（文件更小也更易读）
  - 坏行容错：一行坏数据不该让整份历史读不出来
  - kind 过滤、limit 截断、文件不存在返回空
  - 超阈值自动裁剪到最近 N 条，且裁剪是原子替换
  - 多线程并发 append 不丢行、不撕裂
  - 任何异常都不外抛（目录不可写 / 路径非法）

跑法：python tests/test_events.py
"""
import json
import os
import sys
import tempfile
import threading

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Desktop_Files\mistycloud_manager")

import events  # noqa: E402

_TMP = tempfile.mkdtemp()
P = os.path.join(_TMP, "events.jsonl")
events.set_path(P)

try:
    # ---- [1] 往返 + 倒序 ----
    events.record("switch", True, email="a@x.com", reason="手动触发",
                  proxy_ip="1.1.1.1", direct_ip="2.2.2.2", duration=1.234)
    events.record("switch", False, email="b@x.com", error="端口未就绪")
    events.record("register", True, email="c@x.com")
    got = events.recent(10)
    assert [e["kind"] for e in got] == ["register", "switch", "switch"], \
        f"应按时间倒序（新的在前）: {[e['kind'] for e in got]}"
    assert got[1]["ok"] is False and got[1]["error"] == "端口未就绪", got[1]
    assert got[2]["email"] == "a@x.com" and got[2]["proxy_ip"] == "1.1.1.1"
    assert got[2]["duration"] == 1.23, got[2]
    assert got[2]["ok"] is True
    assert "time" in got[0] and len(got[0]["time"]) == 19, got[0]
    print("[1] record/recent 往返正确，新的在前 ✓")

    # ---- [2] 空字段不写 ----
    events.record("heal", True)
    last = events.recent(1)[0]
    for k in ("email", "reason", "error", "proxy_ip", "direct_ip", "duration"):
        assert k not in last, f"空字段 {k} 不该落盘: {last}"
    assert set(last) == {"ts", "time", "kind", "ok"}, f"多余字段: {last}"
    print("[2] 空字段不落盘（仅 ts/time/kind/ok）✓")

    # ---- [3] 坏行容错 ----
    with open(P, "a", encoding="utf-8", newline="\n") as f:
        f.write("这不是 JSON\n")
        f.write("\n")                    # 空行
        f.write("[1,2,3]\n")             # 合法 JSON 但不是对象
        f.write('{"kind":"switch","ok":true,"time":"x","ts":1}\n')
    got = events.recent(50)
    assert len(got) == 5, f"坏行应被跳过而非中断读取: {len(got)}"
    assert all(isinstance(e, dict) for e in got)
    print("[3] 坏行（非 JSON / 空行 / 非对象）跳过，其余照读 ✓")

    # ---- [4] kind 过滤 ----
    # 注意：[3] 追加的那行也是 switch，所以这里是 3 条而非 2 条。
    assert len(events.recent(50, kind="switch")) == 3
    assert len(events.recent(50, kind="register")) == 1
    assert len(events.recent(50, kind="heal")) == 1
    assert events.recent(50, kind="abort") == []
    print("[4] kind 过滤 ✓")

    # ---- [5] limit 截断 + 非法 limit 兜底 ----
    assert len(events.recent(2)) == 2
    assert events.recent(0) == []
    assert events.recent(-5) == []
    assert len(events.recent("abc")) == 5, "非法 limit 应回落到默认值"
    print("[5] limit 截断；0/负数/非法值不抛 ✓")

    # ---- [6] count() 只数有效行 ----
    assert events.count() == 5, events.count()
    print("[6] count() 只计有效事件行 ✓")

    # ---- [7] 文件不存在 ----
    events.set_path(os.path.join(_TMP, "nope.jsonl"))
    assert events.recent(10) == [] and events.count() == 0
    events.set_path(P)
    print("[7] 文件不存在 → recent 返回 []、count 返回 0 ✓")

    # ---- [8] 超阈值自动裁剪 ----
    # 直接灌一个超 _MAX_BYTES 的文件（比真写几千条快），再记一条触发裁剪。
    big = os.path.join(_TMP, "big.jsonl")
    filler = "x" * 900
    with open(big, "w", encoding="utf-8", newline="\n") as f:
        for i in range(700):
            f.write(json.dumps({"ts": i, "time": "t", "kind": "switch",
                                "ok": True, "pad": filler}) + "\n")
    assert os.path.getsize(big) > events._MAX_BYTES, "测试前提：文件需超过阈值"
    events.set_path(big)
    events.record("heal", True)
    n = events.count()
    assert n == events._KEEP_LINES, \
        f"裁剪后应恰好保留 {events._KEEP_LINES} 条（最后一条是新写的）: {n}"
    assert events.recent(1)[0]["kind"] == "heal", "新写的那条必须被保留"
    assert not os.path.exists(big + ".tmp"), "裁剪的临时文件应已改名替换掉"
    events.set_path(P)
    print(f"[8] 超 {events._MAX_BYTES // 1024}KB 自动裁剪至最近 "
          f"{events._KEEP_LINES} 条，新事件保留 ✓")

    # ---- [9] 并发 append：不丢行、不撕裂 ----
    # 换号由后台线程触发、注册由 UI 线程触发，天然并发。若没有锁，
    # 两个线程各写半行就会造出坏行——而坏行会被 recent 静默跳过，
    # 表现为「事件莫名少了几条」，比报错难查得多。
    pc = os.path.join(_TMP, "concurrent.jsonl")
    events.set_path(pc)
    N_THREADS, PER = 8, 60
    errs = []

    def worker(tid):
        try:
            for i in range(PER):
                events.record("switch", True, email=f"t{tid}-{i}@x.com")
        except Exception as e:                       # pragma: no cover
            errs.append(e)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs, f"并发写入抛异常: {errs}"
    total = events.count()
    assert total == N_THREADS * PER, \
        f"并发丢行或产生坏行: 期望 {N_THREADS * PER}，实得 {total}"
    with open(pc, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                json.loads(ln)          # 任何一行不合法就在这里炸
    events.set_path(P)
    print(f"[9] 并发写入 {N_THREADS}×{PER} 条无丢失、无撕裂行 ✓")

    # ---- [10] 异常全吞 ----
    events.set_path(os.path.join(_TMP, "no_such_dir", "deep", "e.jsonl"))
    events.record("switch", True, email="x@x.com")   # 目录不存在，不得抛
    assert events.recent(5) == []
    events.set_path(_TMP)                             # 路径是目录，写入必失败
    events.record("switch", True)
    events.set_path(P)
    print("[10] 目录不存在 / 路径非法均静默吞掉，不外抛 ✓")

    # ---- [11] clear() ----
    events.clear()
    assert events.count() == 0 and events.recent(5) == []
    events.record("switch", True, email="after@x.com")
    assert events.count() == 1, "清空后仍应能继续记录"
    print("[11] clear() 清空且之后仍可正常记录 ✓")

finally:
    events.set_path(None)      # 无论成败都恢复，避免影响同进程的其它测试

print("\n== 事件流测试通过 ==")
