# -*- coding: utf-8 -*-
"""事件流 — 换号 / 注册 / 恢复的留痕，append-only JSONL。

为什么单独一份，不复用 app.log：
  app.log 是给人顺着读的流水，信息埋在文本里**不可查询**——想回答
  「这周换号失败了几次、都是什么原因」只能肉眼翻。events.jsonl 每行一个
  结构化事件，按类型/结果过滤是几行代码的事，前端也能直接渲染成时间线。

为什么不用 stats.json 那种整写 JSON：
  事件只增不改，且增长没有天然上限。整写意味着每次追加都要重写全量，
  越攒越慢。JSONL 天生适合追加，只在超过阈值时裁剪一次。

职责边界：只负责「记」和「读」。不做业务判断、不打日志、不弹提示——
那些是调用方的职责。**异常全吞**，与 stats.py 一致：留痕永不该影响
换号本身。

⚠ 调用点只应出现在「结果已确定」的位置。埋在中途会让一次失败留下多条
自相矛盾的记录，比不记还糟。
"""
import json
import os
import threading
import time

from paths import base_dir

_KEEP_LINES = 500            # 裁剪后保留的最近条数
_MAX_BYTES = 512 * 1024      # 超过则触发一次裁剪

_lock = threading.Lock()
_path_override: str | None = None


def events_path() -> str:
    return os.path.join(base_dir(), "events.jsonl")


def set_path(path: str | None) -> None:
    """测试用：重定向到指定文件（传 None 恢复默认位置）。"""
    global _path_override
    _path_override = path


def _current_path() -> str:
    return _path_override or events_path()


def _trim_locked(path: str) -> None:
    """裁剪到最近 _KEEP_LINES 行。调用方须持锁。

    先写临时文件再 os.replace，保证读侧永远看到完整的旧文件或完整的新文件，
    不会撞上「读到一半」的中间态。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= _KEEP_LINES:
            return
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(lines[-_KEEP_LINES:])
        os.replace(tmp, path)
    except OSError:
        pass


def record(kind: str, ok: bool, email: str = "", reason: str = "",
           error: str = "", proxy_ip: str = "", direct_ip: str = "",
           duration: float = 0.0) -> None:
    """记一条事件。任何异常都吞掉——留痕失败不该影响业务。

    kind: switch(单次换号) / register(注册) / restore(恢复原节点) / heal(链路自愈)
    reason: 触发原因（手动触发 / 流量不足 / 代理链路异常…）；手动操作可留空
    """
    try:
        now = time.time()
        ev = {
            "ts": round(now, 3),
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "kind": str(kind or "?"),
            "ok": bool(ok),
        }
        # 空字段不写，省体积也让文件更易读
        if email:
            ev["email"] = str(email)
        if reason:
            ev["reason"] = str(reason)
        if error:
            ev["error"] = str(error)
        if proxy_ip:
            ev["proxy_ip"] = str(proxy_ip)
        if direct_ip:
            ev["direct_ip"] = str(direct_ip)
        if duration:
            ev["duration"] = round(float(duration), 2)

        line = json.dumps(ev, ensure_ascii=False, separators=(",", ":"))
        path = _current_path()
        with _lock:
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
            try:
                if os.path.getsize(path) > _MAX_BYTES:
                    _trim_locked(path)
            except OSError:
                pass
    except Exception:
        pass


def recent(limit: int = 100, kind: str = "") -> list:
    """最近 N 条事件，**新的在前**（前端直接顺序渲染）。

    kind 非空时只取该类型。坏行（半截写入 / 手工改坏 / 非对象）跳过——
    一行坏数据不该让整份历史读不出来。
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    if limit <= 0:
        return []
    try:
        with open(_current_path(), "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, OSError):
        return []

    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(ev, dict):
            continue
        if kind and ev.get("kind") != kind:
            continue
        out.append(ev)
        if len(out) >= limit:
            break
    return out


def count() -> int:
    """有效事件条数（坏行不计）。读取失败返回 0。"""
    try:
        with open(_current_path(), "r", encoding="utf-8") as f:
            n = 0
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    if isinstance(json.loads(line), dict):
                        n += 1
                except (json.JSONDecodeError, ValueError):
                    continue
            return n
    except (FileNotFoundError, OSError):
        return 0


def clear() -> None:
    """清空事件流（供前端「清空」按钮）。"""
    try:
        with _lock:
            with open(_current_path(), "w", encoding="utf-8", newline="\n"):
                pass
    except OSError:
        pass
