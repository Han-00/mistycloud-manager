"""日报统计 — 换号次数 / 流量消耗 / 推送状态，本地持久化。

存 stats.json（原子写，对齐 account_pool）；线程安全；异常全吞，
统计永不影响业务。

日报统计「昨天全天」：日期翻转时把旧日计数固化为 pending_report，
由监控按配置时间推送；程序晚启动也能补发，last_report_date 防重复。
"""
import json
import os
import sys
import threading
import time

_DEFAULT = {
    "date": "",                # 当前计数所属日（YYYY-MM-DD）
    "switch_ok": 0,
    "switch_fail": 0,
    "traffic_used": 0,         # 当日消耗流量（字节）
    "traffic_base": {},        # {email: 最近观测的服务端累计已用}，跨天保留
    "last_report_date": "",    # 最近成功推送日报的日期
    "pending_report": None,    # 待推送的上一日全天计数
}


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def stats_path() -> str:
    return os.path.join(_base_dir(), "stats.json")


def _atomic_write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Stats:
    def __init__(self, path: str | None = None):
        self.path = path or stats_path()
        self._lock = threading.Lock()
        self.data: dict = dict(_DEFAULT)
        self.data["traffic_base"] = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                for k in _DEFAULT:
                    if k in loaded:
                        self.data[k] = loaded[k]
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    def _save_locked(self):
        try:
            _atomic_write_json(self.path, self.data)
        except OSError:
            pass

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d")

    def _rollover_locked(self):
        """日期翻转：旧日计数固化进 pending（已有未推送的则保留旧的），
        重置当日三项计数；流量基线与 last_report_date 跨天保留。"""
        today = self._today()
        if self.data.get("date") == today:
            return
        if self.data.get("date") and not self.data.get("pending_report"):
            self.data["pending_report"] = {
                "date": self.data["date"],
                "switch_ok": int(self.data.get("switch_ok") or 0),
                "switch_fail": int(self.data.get("switch_fail") or 0),
                "traffic_used": int(self.data.get("traffic_used") or 0),
            }
        self.data["date"] = today
        self.data["switch_ok"] = 0
        self.data["switch_fail"] = 0
        self.data["traffic_used"] = 0
        self._save_locked()

    def touch(self):
        """仅触发日期翻转（保证无采样日也能产出上一日待推送计数）。"""
        with self._lock:
            try:
                self._rollover_locked()
            except Exception:
                pass

    def record_switch(self, ok: bool):
        with self._lock:
            try:
                self._rollover_locked()
                key = "switch_ok" if ok else "switch_fail"
                self.data[key] = int(self.data.get(key) or 0) + 1
                self._save_locked()
            except Exception:
                pass

    def add_traffic(self, email: str, used):
        """按同账号服务端累计已用的增量累加当日消耗（幂等）。

        首次观测只设基线不计数；换号重置基线不计数（新号 used≈0，
        误差可忽略）；计数器回退（delta<0）只重置基线不记负。
        """
        try:
            used = int(used or 0)
        except (TypeError, ValueError):
            return
        if not email or used < 0:
            return
        with self._lock:
            try:
                self._rollover_locked()
                base = self.data.setdefault("traffic_base", {})
                prev = base.get(email)
                if prev is None:
                    base[email] = used
                else:
                    delta = used - int(prev)
                    if delta > 0:
                        self.data["traffic_used"] = (
                            int(self.data.get("traffic_used") or 0) + delta)
                    base[email] = used
                self._save_locked()
            except Exception:
                pass

    def mark_reported(self):
        """日报推送成功后调用：记录日期、清待推送。"""
        with self._lock:
            try:
                self.data["last_report_date"] = self._today()
                self.data["pending_report"] = None
                self._save_locked()
            except Exception:
                pass

    # ---- 只读快照（供日报组装）----

    def get_pending(self) -> dict | None:
        with self._lock:
            p = self.data.get("pending_report")
            return dict(p) if isinstance(p, dict) else None

    def is_reported_today(self) -> bool:
        with self._lock:
            return self.data.get("last_report_date") == self._today()

    def snapshot(self) -> dict:
        with self._lock:
            d = dict(self.data)
            d["traffic_base"] = dict(d.get("traffic_base") or {})
            return d


def fmt_bytes(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    if n >= 1024 ** 3:
        return f"{n/1024**3:.2f}GB"
    if n >= 1024 ** 2:
        return f"{n/1024**2:.1f}MB"
    if n >= 1024:
        return f"{n/1024:.1f}KB"
    return f"{int(n)}B"


# ---- 模块级单例 + 直通函数（switcher/monitor/UI 直接 import 调用）----

_inst: Stats | None = None
_inst_lock = threading.Lock()


def get() -> Stats:
    global _inst
    with _inst_lock:
        if _inst is None:
            _inst = Stats()
        return _inst


def record_switch(ok: bool):
    try:
        get().record_switch(ok)
    except Exception:
        pass


def add_traffic(email: str, used):
    try:
        get().add_traffic(email, used)
    except Exception:
        pass
