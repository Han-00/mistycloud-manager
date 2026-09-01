"""配置管理 — settings.json 读写，启动时加载，热更新支持。

所有配置存 exe/项目同目录的 settings.json；缺失即默认值。
"""
import json
import os
import sys

DEFAULTS = {
    # 自动换号
    "auto_switch": True,            # 默认开启自动换号
    "min_traffic_mb": 30.0,         # 剩余流量低于此值触发换号
    "expiry_threshold_seconds": 1800.0,  # 剩余有效期低于 30min 触发换号
    "account_lifetime_seconds": 86400.0, # 账号生命周期 24h
    # 账号池
    "reserve_accounts": 2,          # 保持备用账号数
    # v2ray
    "v2ray_dir": "",                # v2ray 可执行文件目录（空=自动探测）
    "proxy_port": 10808,            # SOCKS 代理端口
    # 订阅
    "sub_url": "",                  # 手动指定订阅 URL（留空=登录自动获取）
    # 通知（飞书，可选）
    "feishu_webhook": "",           # 群机器人 webhook（留空=不通知）
    "feishu_app_id": "",            # 开放平台应用（三字段齐全时优先于 webhook）
    "feishu_app_secret": "",
    "feishu_open_id": "",
    # 窗口
    "win_x": None, "win_y": None, "win_w": None, "win_h": None,
    # 界面/托盘
    "ui_theme": "浅色云雾",          # 液态玻璃主题：浅色云雾 / 深海蓝 / 暮光紫
    "minimize_to_tray": True,       # 关闭按钮 → 最小化到托盘（托盘不可用时忽略）
    # 系统代理 / 日报
    "system_proxy": False,          # 系统代理开关（期望状态；崩溃恢复的事实源）
    "daily_report_time": "09:00",   # 每日日报推送时间（HH:MM，空串=禁用）
}


def _base_dir() -> str:
    """配置/数据目录：exe 同目录（冻结）或项目目录（源码运行）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def config_path() -> str:
    return os.path.join(_base_dir(), "settings.json")


class Config:
    """线程安全配置对象：启动读一次，_save 时整写（保留未知字段）。"""

    def __init__(self, path: str | None = None):
        self.path = path or config_path()
        self.data: dict = dict(DEFAULTS)
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                # 合并：已知键用文件值，缺失键用默认；保留未知键（如窗口位置）
                merged = dict(DEFAULTS)
                merged.update({k: v for k, v in loaded.items() if k in DEFAULTS or k.startswith("win_")})
                for k, v in loaded.items():
                    if k not in merged:
                        merged[k] = v
                self.data = merged
        except FileNotFoundError:
            pass  # 用默认
        except (json.JSONDecodeError, OSError):
            pass  # 损坏文件用默认

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value

    def save(self):
        """整写 settings.json（tmp + os.replace 原子写），保留全部字段。"""
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass
