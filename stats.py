"""日报统计 — 换号次数 / 流量消耗 / 推送状态，本地持久化。

存 stats.json（原子写，对齐 account_pool）；线程安全；异常全吞，
统计永不影响业务。

日报统计「昨天全天」：日期翻转时把旧日计数固化为 pending_report，
由监控按配置时间推送；程序晚启动也能补发，last_report_date 防重复。
"""
import json
import os
import threading
import time

from paths import base_dir

_DEFAULT = {
    "date": "",                # 当前计数所属日（YYYY-MM-DD）
    "switch_ok": 0,
    "switch_fail": 0,
    "traffic_used": 0,         # 当日消耗流量（字节）
    "traffic_base": {},        # {email: 最近观测的服务端累计已用}，跨天保留
    "hourly": {},              # {"YYYY-MM-DDTHH": 该小时消耗字节}，保留近 8 天
    "last_report_date": "",    # 最近成功推送日报的日期
    "pending_report": None,    # 待推送的上一日全天计数
}

_HOURLY_KEEP_DAYS = 8          # 小时桶保留天数（热力图用 7 天，多留 1 天余量）


def stats_path() -> str:
    return os.path.join(base_dir(), "stats.json")


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

    def _hour_key(self) -> str:
        """当前小时桶键（测试可注入替代，与 _today 同理）。"""
        return time.strftime("%Y-%m-%dT%H")

    def _prune_hourly_locked(self):
        """修剪 8 天前的小时桶（键为定长可排序字符串，按字典序比较）。"""
        cutoff = time.strftime("%Y-%m-%dT%H",
                               time.localtime(time.time()
                                              - _HOURLY_KEEP_DAYS * 86400))
        hourly = self.data.get("hourly")
        if isinstance(hourly, dict):
            self.data["hourly"] = {k: v for k, v in hourly.items()
                                   if k >= cutoff}

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
                        hourly = self.data.setdefault("hourly", {})
                        hk = self._hour_key()
                        hourly[hk] = int(hourly.get(hk) or 0) + delta
                    base[email] = used
                self._prune_hourly_locked()
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
            d["hourly"] = dict(d.get("hourly") or {})
            return d

    # ---- 热力图 / 续航预测（供 UI）----

    def hourly_series(self, days: int = 7) -> list:
        """近 N 天小时桶，[[小时键, 字节], ...] 按时间升序，仅含有数据的桶。"""
        cutoff = time.strftime("%Y-%m-%dT%H",
                               time.localtime(time.time() - days * 86400))
        with self._lock:
            hourly = dict(self.data.get("hourly") or {})
        return sorted(([k, int(v)] for k, v in hourly.items() if k >= cutoff),
                      key=lambda x: x[0])

    def avg_daily_bytes(self, days: int = 3):
        """近 N 天平均日消耗（字节/天）；当天按已流逝时长折算权重。
        无数据返回 None。分母 = 完整天数 + 当天已流逝比例（下限 1 小时），
        避免把"刚过去的半天"当成一整天拉低均值。"""
        cutoff = time.time() - days * 86400
        today = self._today()
        with self._lock:
            hourly = dict(self.data.get("hourly") or {})
        total = 0.0
        dates = set()
        for k, v in hourly.items():
            try:
                ts = time.mktime(time.strptime(k, "%Y-%m-%dT%H"))
            except (ValueError, OverflowError):
                continue
            if ts < cutoff:
                continue
            total += float(v)
            dates.add(k[:10])
        if total <= 0 or not dates:
            return None
        lt = time.localtime()
        elapsed_frac = max((lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec)
                           / 86400.0, 1 / 24)
        effective = sum(elapsed_frac if d == today else 1.0 for d in dates)
        return total / max(effective, elapsed_frac)


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


def hourly_series(days: int = 7) -> list:
    try:
        return get().hourly_series(days)
    except Exception:
        return []


def avg_daily_bytes(days: int = 3):
    try:
        return get().avg_daily_bytes(days)
    except Exception:
        return None
