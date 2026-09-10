# -*- coding: utf-8 -*-
"""设置校验测试 —— settings_schema 是三个 UI 共用的唯一校验实现。

为什么值得单测：这段逻辑原先在 ui.py / ui_glass.py / ui_web.py 里各抄了一遍，
而且已经漂移（同一种错误，三个界面的提示文案都不一样）。集中之后这里就是唯一
事实来源——把它测住，三个界面的行为一起被锁死。

纯逻辑：不建窗口、不落盘、不连网、零副作用。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings_schema as ss  # noqa: E402


class _FakeConfig:
    """只实现 set/get 的配置替身，避免碰真实 settings.json。"""

    def __init__(self):
        self.data = {}

    def set(self, k, v):
        self.data[k] = v

    def get(self, k, default=None):
        return self.data.get(k, default)


def _expect_error(raw, keyword=""):
    """断言 parse_settings 抛 ValueError，且消息含关键字。"""
    try:
        ss.parse_settings(raw)
    except ValueError as e:
        if keyword:
            assert keyword in str(e), f"错误文案不含 {keyword!r}: {e}"
        return str(e)
    raise AssertionError(f"应当校验失败但通过了: {raw}")


def main():
    # ---- [1] 全量合法输入 ----
    values = ss.parse_settings({
        "min_traffic_mb": "30",
        "expiry_threshold_minutes": "30",
        "account_lifetime_hours": "24",
        "reserve_accounts": "2",
        "proxy_port": "10808",
        "feishu_webhook": "  https://hook/x  ",
        "feishu_app": " appid / secret / openid ",
        "daily_report_time": "9:05",
    })
    assert values["min_traffic_mb"] == 30.0, values
    assert values["expiry_threshold_seconds"] == 1800.0, values      # 分钟→秒
    assert values["account_lifetime_seconds"] == 86400.0, values     # 小时→秒
    assert values["reserve_accounts"] == 2, values
    assert values["proxy_port"] == 10808, values
    assert values["feishu_webhook"] == "https://hook/x", values      # 去空白
    assert values["feishu_app_id"] == "appid", values
    assert values["feishu_app_secret"] == "secret", values
    assert values["feishu_open_id"] == "openid", values
    assert values["daily_report_time"] == "09:05", values            # 补零
    print("[1] 全量合法输入 → 单位换算/去空白/拆分/补零 ✓")

    # ---- [2] 端口边界 ----
    for ok in ("1", "10808", "65535", 65535):
        assert ss.parse_settings({"proxy_port": ok})["proxy_port"] == int(ok)
    for bad in ("0", "-1", "65536", "99999"):
        _expect_error({"proxy_port": bad}, "代理端口")
    print("[2] 端口 1~65535 含端点，越界拒绝 ✓")

    # ---- [3] 生命周期必须为正数 ----
    for bad in ("0", "-1"):
        _expect_error({"account_lifetime_hours": bad}, "必须为正数")
    print("[3] 账号生命周期 <=0 拒绝 ✓")

    # ---- [4] 日报时间格式（三个 UI 曾经文案不一致的地方）----
    assert ss.parse_settings({"daily_report_time": ""})["daily_report_time"] == ""
    assert ss.parse_settings({"daily_report_time": "   "})["daily_report_time"] == ""
    assert ss.parse_settings({"daily_report_time": "00:00"})["daily_report_time"] == "00:00"
    assert ss.parse_settings({"daily_report_time": "23:59"})["daily_report_time"] == "23:59"
    assert ss.parse_settings({"daily_report_time": "8:07"})["daily_report_time"] == "08:07"
    for bad in ("24:00", "09:60", "abc", "9-00", "0900"):
        _expect_error({"daily_report_time": bad}, "HH:MM")
    print("[4] 日报时间：空=禁用、边界合法、非法拒绝 ✓")

    # ---- [5] 整数与下限 ----
    assert ss.parse_settings({"reserve_accounts": "-5"})["reserve_accounts"] == 0
    assert ss.parse_settings({"reserve_accounts": "2.0"})["reserve_accounts"] == 2
    _expect_error({"reserve_accounts": "2.5"}, "必须是整数")
    _expect_error({"proxy_port": "10808.5"}, "必须是整数")
    print("[5] 备用账号数负数归零、小数拒绝 ✓")

    # ---- [6] 非数字与空值 ----
    _expect_error({"min_traffic_mb": "abc"}, "必须是数字")
    _expect_error({"proxy_port": ""}, "不能为空")
    # 阈值允许负数：沿用既有宽松行为（负值等于关闭该项阈值判断），
    # 重构只做集中、不夹带行为变更
    assert ss.parse_settings({"min_traffic_mb": "-1"})["min_traffic_mb"] == -1.0
    print("[6] 非数字/空值给出明确文案；阈值负值沿用宽松行为 ✓")

    # ---- [7] 缺省字段跳过（三个 UI 暴露的字段并不完全相同）----
    assert ss.parse_settings({}) == {}
    only = ss.parse_settings({"proxy_port": "10809"})
    assert only == {"proxy_port": 10809}, only
    # 只给了部分字段时，不应凭空写入其他键
    assert "min_traffic_mb" not in only
    print("[7] 只处理出现过的字段，不凭空补键 ✓")

    # ---- [8] 飞书三字段拆分（多/少/空）----
    partial = ss.parse_settings({"feishu_app": "onlyid"})
    assert partial["feishu_app_id"] == "onlyid"
    assert partial["feishu_app_secret"] == "" and partial["feishu_open_id"] == ""
    empty = ss.parse_settings({"feishu_app": ""})
    assert empty["feishu_app_id"] == empty["feishu_app_secret"] == ""
    print("[8] 飞书三字段：缺项补空、单项可用 ✓")

    # ---- [9] apply_settings 真的写进 config，且返回写入的键值 ----
    cfg = _FakeConfig()
    wrote = ss.apply_settings(cfg, {"proxy_port": "10809", "min_traffic_mb": "50"})
    assert cfg.data["proxy_port"] == 10809 and cfg.data["min_traffic_mb"] == 50.0
    assert wrote["proxy_port"] == 10809
    # 校验失败时不应写入任何东西（先全量校验、后统一落值）
    cfg2 = _FakeConfig()
    try:
        ss.apply_settings(cfg2, {"proxy_port": "70000", "min_traffic_mb": "50"})
        raise AssertionError("越界端口应当报错")
    except ValueError:
        pass
    assert cfg2.data == {}, f"校验失败却已部分写入: {cfg2.data}"
    print("[9] apply_settings 写入正确，且失败时不产生半截配置 ✓")

    print("\n== 设置校验测试通过 ==")


if __name__ == "__main__":
    main()
