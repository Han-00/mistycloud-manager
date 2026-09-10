# -*- coding: utf-8 -*-
"""异常消耗检测 — 从小时桶里找出「流量烧得不对劲」的时段。

数据来源全是现成的：stats 的 hourly 小时桶（谁在几点烧了多少）+
events 的换号留痕（那个时段在用哪个号）。本模块只做**只读分析**，
不写任何状态、不打日志——它是查询层，不是业务流程的一环。

判定（双条件，缺一不可）：
  1) 绝对阈值：单小时消耗 ≥ threshold_mb（默认 200MB）。这类一次性账号
     单个总额才 ~300MB，一小时烧 200MB 起就值得看一眼；
  2) 偏离基线：≥ 同一时刻（一天内第几小时）其他天均值的 ratio 倍
     （默认 3 倍）。作用是把「每晚这个点本来就烧这么多」的常态排除掉——
     没有基线条件的话，规律性重度用户会被天天误报。

  基线样本不足（同小时其他天样本 < 2）时退化成仅按阈值判定，
  basis 标出来让前端区分展示：样本不足时阈值命中只是「值得怀疑」，
  不是「确认偏离」。

为什么基线用均值不用中位数：均值会被异常日抬高、让后续同小时检测
变钝，但它对「整体水平」更诚实；中位数在只有 2-3 个样本时基本等于
随机取一个。小时桶最多保留 8 天（stats._HOURLY_KEEP_DAYS），样本
天生就少，选简单可解释的那个。

账号关联：某小时桶内发生过的换号直接归属该桶（可能多个）；桶内没
换过号，则归属「最近一次成功换号」的账号——即当时正在用的号，
inferred 标记区分「实测」和「推断」。失败事件不计（没换过去）。
"""
import time

_MB = 1024 * 1024
THRESHOLD_MB = 200.0        # 单小时绝对阈值（MB）
RATIO = 3.0                 # 相对同时段均值的倍数
MIN_BASELINE_SAMPLES = 2    # 基线至少需要的「其他天同小时」样本数


def _parse_hour(key: str) -> float:
    """小时桶键 → 该小时起点的时间戳。解析失败返回 0（关联会得到空结果）。"""
    try:
        return time.mktime(time.strptime(key, "%Y-%m-%dT%H"))
    except (ValueError, TypeError, OverflowError):
        return 0.0


def _switch_events(days: int) -> list:
    """近 N 天的换号事件，按时间升序。读不到就返回空——账号关联是
    锦上添花的信息，缺了不该拖垮整个检测。"""
    try:
        import events
        evs = events.recent(limit=500, kind="switch")
    except Exception:
        return []
    cutoff = time.time() - days * 86400
    out = []
    for e in evs:                  # recent() 新的在前，反转成时间线顺序
        if not isinstance(e, dict):
            continue
        ts = e.get("ts")
        if not isinstance(ts, (int, float)) or ts < cutoff:
            continue
        out.append(e)
    out.reverse()
    return out


def _accounts_for(hour_start: float, switches: list) -> tuple:
    """归属某小时桶的账号。返回 ([email, ...], 是否推断)。"""
    if hour_start <= 0:
        return [], True
    hour_end = hour_start + 3600
    in_hour, seen = [], set()
    for e in switches:
        ts = e.get("ts") or 0
        # 失败换号没换过去，不改变「当时在用谁」，不能用于归属
        if hour_start <= ts < hour_end and e.get("ok") and e.get("email"):
            email = str(e["email"])
            if email not in seen:          # 去重且保持出现顺序
                seen.add(email)
                in_hour.append(email)
    if in_hour:
        return in_hour, False
    for e in reversed(switches):           # 桶内没换号 → 最近一次成功换号
        if ((e.get("ts") or 0) < hour_start and e.get("ok")
                and e.get("email")):
            return [str(e["email"])], True
    return [], True


def detect(days: int = 7, threshold_mb: float = THRESHOLD_MB,
           ratio: float = RATIO) -> list:
    """扫描近 N 天小时桶，返回异常时段列表（消耗大的在前）。

    每项：{hour, bytes, baseline, ratio, accounts, inferred, basis}
    basis: "both"（阈值+基线双确认）| "threshold"（仅阈值，基线样本不足）。
    纯只读，任何一步读不到数据都返回空表，不抛异常。
    """
    try:
        import stats
        series = stats.hourly_series(days)
    except Exception:
        return []
    if not series:
        return []
    try:
        threshold = max(float(threshold_mb or 0), 0) * _MB
        ratio = max(float(ratio or 0), 0)
    except (TypeError, ValueError):
        threshold, ratio = THRESHOLD_MB * _MB, RATIO

    # 同小时（一天内第几小时）的全部样本，判定时剔除自己算基线
    samples = {}
    for k, v in series:
        try:
            samples.setdefault(str(k)[11:13], []).append(float(v))
        except (TypeError, ValueError):
            continue

    switches = _switch_events(days)
    out = []
    for k, v in series:
        try:
            used = float(v)
        except (TypeError, ValueError):
            continue
        if used < threshold:
            continue
        others = list(samples.get(str(k)[11:13]) or [])
        try:
            others.remove(used)            # 只剔除一个自己（值相同也无所谓，均值不受顺序影响）
        except ValueError:
            pass
        baseline = (sum(others) / len(others)) if len(others) >= MIN_BASELINE_SAMPLES else None
        # 过了阈值但没偏离基线 → 这是这个用户的常态，不算异常
        if baseline is not None and used < baseline * ratio:
            continue
        accounts, inferred = _accounts_for(_parse_hour(str(k)), switches)
        out.append({
            "hour": str(k),
            "bytes": int(used),
            "baseline": round(baseline) if baseline is not None else None,
            "ratio": round(used / baseline, 1) if baseline else None,
            "accounts": accounts,
            "inferred": inferred,
            "basis": "both" if baseline is not None else "threshold",
        })
    out.sort(key=lambda a: a["bytes"], reverse=True)
    return out
