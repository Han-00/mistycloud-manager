# -*- coding: utf-8 -*-
"""设置项校验与规范化 —— 全项目唯一实现。

为什么要有这个模块：
ui.py（ttk 版）/ ui_glass.py（玻璃版）/ ui_web.py（Web 版）原先各写了一遍同一套
校验（端口范围、生命周期正数、日报时间正则、飞书三字段拆分……）。三份不仅重复，
**而且已经开始漂移**——同一类错误的提示文案就不一致（「日报时间须为 HH:MM」vs
「日报推送时间格式应为 HH:MM」）。改一处漏两处，漏掉的那个界面就是颗哑雷：
比如哪天产品说「端口要放开到 65535 以上」，你改了 Web 版，老界面就还在拦。

职责边界（重要，别把 UI 的事搬进来）：
- 本模块只做「原始输入 → 规范化的 config 键值」。
- **不落盘、不重启引擎、不弹提示、不打日志**——那些是调用方的职责，
  因为「什么时候存、端口变了要不要重启代理、错误怎么展示」各 UI 各不相同。
- 校验失败抛 ValueError，文案面向用户，可直接展示。

输入约定：
raw 的键与界面字段同名，值允许 str / int / float。未出现的键直接跳过
（三个 UI 暴露的字段并不完全相同，例如只有玻璃版有托盘/自启开关）。
"""
import re

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _num(raw: dict, key: str, label: str, *,
         integer: bool = False, minimum=None, maximum=None):
    """取一个数值字段；字段不存在返回 None。非法输入抛 ValueError。"""
    if key not in raw:
        return None
    text = str(raw[key]).strip()
    if text == "":
        raise ValueError(f"{label}不能为空")
    try:
        n = float(text)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须是数字") from None
    if integer:
        if n != int(n):
            raise ValueError(f"{label}必须是整数")
        n = int(n)
    if minimum is not None and n < minimum:
        raise ValueError(f"{label}不能小于 {minimum:g}")
    if maximum is not None and n > maximum:
        raise ValueError(f"{label}不能大于 {maximum:g}")
    return n


def normalize_time(value) -> str:
    """日报推送时间：空 = 禁用；否则须为 HH:MM 且时分合法，返回补零后的规范形式。"""
    text = str(value or "").strip()
    if not text:
        return ""
    m = _TIME_RE.match(text)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("日报时间须为 HH:MM（如 09:00）")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_settings(raw: dict) -> dict:
    """校验并规范化，返回可直接 config.set 的键值字典。

    抛 ValueError 表示某一项不合格（消息可展示给用户）。
    """
    out: dict = {}

    # 下面只有「端口范围」和「生命周期为正」是原实现就有的校验，其余一律不新增
    # 下限——重构不夹带行为变更。例如阈值原实现允许负数（那等于关闭该项阈值判断），
    # 要收紧应当单独提，别混在重构里。
    v = _num(raw, "min_traffic_mb", "自动换号阈值(MB)")
    if v is not None:
        out["min_traffic_mb"] = v

    v = _num(raw, "expiry_threshold_minutes", "有效期预警(分钟)")
    if v is not None:
        out["expiry_threshold_seconds"] = v * 60

    v = _num(raw, "account_lifetime_hours", "账号生命周期(小时)")
    if v is not None:
        if v <= 0:
            raise ValueError("账号生命周期必须为正数")
        out["account_lifetime_seconds"] = v * 3600

    # 负数静默归零 —— 沿用既有行为
    v = _num(raw, "reserve_accounts", "备用账号数", integer=True)
    if v is not None:
        out["reserve_accounts"] = max(0, int(v))

    v = _num(raw, "proxy_port", "代理端口", integer=True, minimum=1, maximum=65535)
    if v is not None:
        out["proxy_port"] = v

    if "feishu_webhook" in raw:
        out["feishu_webhook"] = str(raw["feishu_webhook"]).strip()

    # 飞书应用三字段用一行「id/secret/open_id」输入，拆开分别存
    if "feishu_app" in raw:
        parts = [x.strip() for x in str(raw["feishu_app"]).split("/")]
        for i, k in enumerate(("feishu_app_id", "feishu_app_secret",
                               "feishu_open_id")):
            out[k] = parts[i] if i < len(parts) else ""

    if "daily_report_time" in raw:
        out["daily_report_time"] = normalize_time(raw["daily_report_time"])

    return out


def apply_settings(config, raw: dict) -> dict:
    """parse_settings + 写入 config（**不落盘**）。

    返回写入的键值，供调用方判断端口是否变化、要不要重启引擎。
    """
    values = parse_settings(raw)
    for k, v in values.items():
        config.set(k, v)
    return values
