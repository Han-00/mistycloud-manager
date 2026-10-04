"""本地流量阶段报告：纯日汇总计算，不查询网络、不修改统计文件。"""
import csv
import io
from datetime import date, datetime, timedelta

from stats import DAILY_KEEP_DAYS, fmt_bytes

SCOPE = "本机管理过的账号合计，按平台累计已用量的正增量记录。"
NOTES = [
    "缺少记录的日期不等于零用量；总量仅包含已记录增量，日均不含今日和缺失日期。",
    "首次查询或计数器回退只建立基线，需再次获得有效增量采样才计为有记录；停机后恢复的增量记在恢复时段，可能包含其他设备的用量。",
    "旧版仍保留的小时桶已迁移为日汇总；已清理的历史无法恢复。日汇总保留365天。",
    "当前套餐余额与历史合计口径不同；历史记录与估算不能代替平台账单。",
]


def _parse_date(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("日期格式应为 YYYY-MM-DD")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise ValueError("请输入有效日期") from None
    if result.isoformat() != value:
        raise ValueError("日期格式应为 YYYY-MM-DD")
    return result


def build_report(snapshot, start=None, end=None, *, today=None):
    """日历日期闭区间，默认近30天；缺失用 None，与实测零值分开。"""
    today = today or date.today()
    end_day = _parse_date(end) if end is not None else today
    start_day = (_parse_date(start) if start is not None
                 else end_day - timedelta(days=29))
    if start_day > end_day:
        raise ValueError("开始日期不能晚于结束日期")
    if end_day > today:
        raise ValueError("不能查询未来日期")
    days = (end_day - start_day).days + 1
    if days > DAILY_KEEP_DAYS:
        raise ValueError(f"单次最多查看{DAILY_KEEP_DAYS}天")

    daily = snapshot.get("daily")
    daily = daily if isinstance(daily, dict) else {}
    valid = {}
    baseline_only = {}
    retention_start = today - timedelta(days=DAILY_KEEP_DAYS - 1)
    for key, value in daily.items():
        try:
            day = _parse_date(key)
            if not retention_start <= day <= today or not isinstance(value, dict):
                continue
            amount = int(value["bytes"])
            samples = int(value.get("samples") or 0)
            intervals = int(value.get("intervals", samples))
            legacy = bool(value.get("legacy"))
            if amount < 0 or samples < 0 or intervals < 0 or (not samples and not legacy):
                continue
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not intervals and not legacy:
            baseline_only[key] = samples
        else:
            valid[key] = {"bytes": amount, "samples": samples, "legacy": legacy}

    def series(first, last):
        result = []
        for offset in range(max(0, (last - first).days + 1)):
            day = first + timedelta(days=offset)
            key = day.isoformat()
            record = valid.get(key)
            result.append({"date": key, "bytes": record["bytes"] if record else None,
                           "samples": record["samples"] if record else baseline_only.get(key, 0),
                           "legacy": record["legacy"] if record else False,
                           "baseline_only": key in baseline_only,
                           "partial": day == today})
        return result

    rows = series(start_day, end_day)
    recorded = [row for row in rows if row["bytes"] is not None]
    complete = [row for row in recorded if not row["partial"]]
    total = sum(row["bytes"] for row in recorded) if recorded else None
    average = (sum(row["bytes"] for row in complete) / len(complete)
               if complete else None)
    peak = max(recorded, key=lambda row: row["bytes"]) if recorded else None

    # 今日未结束，比较时排除今日；两侧任一缺日就不给增长率。
    compare_end = min(end_day, today - timedelta(days=1))
    compare_days = max(0, (compare_end - start_day).days + 1)
    previous_end = start_day - timedelta(days=1)
    previous_start = start_day - timedelta(days=compare_days)
    previous = series(previous_start, previous_end) if compare_days else []
    current = series(start_day, compare_end)
    comparison = {"status": "incomplete" if compare_days else "insufficient",
                  "days": compare_days, "excludes_today": end_day == today,
                  "current_start": start_day.isoformat(),
                  "current_end": compare_end.isoformat() if compare_days else None,
                  "previous_start": previous_start.isoformat() if compare_days else None,
                  "previous_end": previous_end.isoformat() if compare_days else None,
                  "percent": None, "previous_bytes": None, "current_bytes": None}
    if compare_days and all(row["bytes"] is not None for row in current + previous):
        previous_total = sum(row["bytes"] for row in previous)
        current_total = sum(row["bytes"] for row in current)
        comparison.update(status="ready" if previous_total else "zero_baseline",
                          previous_bytes=previous_total, current_bytes=current_total,
                          percent=round((current_total - previous_total) / previous_total * 100, 1)
                          if previous_total else None)

    insights = []
    if not recorded:
        insights.append("所选日期没有有效增量记录。保持客户端运行，首次查询建立基线后，后续成功查询将自动积累历史。")
    else:
        if peak and peak["bytes"] > 0:
            insights.append(f"最高记录用量出现在{peak['date']}，共{fmt_bytes(peak['bytes'])}"
                            + ("（今日尚未结束）。" if peak["partial"] else "。"))
        if comparison["status"] == "ready":
            percent = comparison["percent"]
            change = "持平" if percent == 0 else f"{'增加' if percent > 0 else '减少'}{abs(percent):.1f}%"
            insights.append(f"与前一个等长{compare_days}天周期相比，记录用量{change}"
                            + ("（比较已排除今日）。" if comparison["excludes_today"] else "。"))
        elif comparison["status"] == "zero_baseline":
            insights.append("上期记录用量为零，无法计算增长率。")
        else:
            insights.append("相邻周期记录不足，暂不计算增长率。")

    return {"ok": True, "start": start_day.isoformat(), "end": end_day.isoformat(),
            "today": today.isoformat(), "generated_at": datetime.now().isoformat(timespec="seconds"),
            "days": days, "retention_days": DAILY_KEEP_DAYS,
            "history_start": min(valid) if valid else None, "scope": SCOPE,
            "series": rows,
            "summary": {"total_bytes": total, "average_daily_bytes": average,
                        "average_days": len(complete), "recorded_days": len(recorded),
                        "missing_days": days - len(recorded), "peak": peak},
            "comparison": comparison,
            "insights": insights, "notes": NOTES}


def export_report(report):
    """CSV保留精确字节、统计口径和缺失标记。"""
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["日期", "记录用量(字节)", "采样次数", "状态", "来源", "统计口径"])
    for row in report["series"]:
        writer.writerow([row["date"], "" if row["bytes"] is None else row["bytes"],
                         row["samples"], _row_status(row),
                         "旧小时记录迁移" if row["legacy"] else "平台累计用量增量" if row["bytes"] is not None else "",
                         SCOPE])
    return out.getvalue()


def _row_status(row):
    if row.get("baseline_only"):
        return "仅有基线，等待增量采样"
    if row["bytes"] is None:
        return "无记录"
    return "今日进行中" if row["partial"] else "有记录"
