"""配置管理 — settings.json 读写，启动时加载，热更新支持。

所有配置存 exe/项目同目录的 settings.json；缺失即默认值。
"""
import json
import os

from paths import base_dir

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
    # 内置直连域名（国内站走代理反而卡）：与 settings.json 的同名键去重合并生效
    "proxy_bypass_domains": [
        # 抖音/字节系
        "douyin.com", "douyinpic.com", "douyinvod.com", "snssdk.com",
        "bytecdn.cn", "zjcdn.com", "bytedance.com", "byteimg.com",
        "pstatp.com", "ixigua.com", "amemv.com", "doubao.com",
        # 飞书/钉钉
        "feishu.cn", "larkoffice.com", "larksuite.com", "dingtalk.com",
        # 网课平台（学习通/智慧树/雨课堂/中国大学MOOC）
        "chaoxing.com", "zhihuishu.com", "yuketang.cn", "icourse163.org",
        # AI（DeepSeek/Kimi/智谱等；文心/通义/元宝随百度/阿里/腾讯域名覆盖）
        "deepseek.com", "moonshot.cn", "bigmodel.cn", "chatglm.cn", "zhipuai.cn",
        # 腾讯/QQ/微信
        "qq.com", "tencent.com", "gtimg.com", "idqqimg.com", "qpic.cn",
        # 阿里/淘宝/支付宝/高德
        "taobao.com", "tmall.com", "alicdn.com", "tbcdn.cn", "alipay.com",
        "alipayobjects.com", "aliyun.com", "aliyuncs.com", "amap.com",
        # 百度
        "baidu.com", "bdstatic.com", "bcebos.com", "baidubce.com",
        # 网易（邮箱/云音乐/游戏）
        "163.com", "126.com", "126.net", "netease.com", "ydstatic.com",
        # B站
        "bilibili.com", "hdslb.com", "bilivideo.com", "biliapi.net",
        # 微博/知乎/小红书
        "weibo.com", "weibo.cn", "sina.com.cn", "sinaimg.cn",
        "zhihu.com", "zhimg.com", "xiaohongshu.com", "xhscdn.com",
        # 京东/视频/直播/音乐
        "jd.com", "360buyimg.com", "youku.com", "ykimg.com", "iqiyi.com",
        "iqiyipic.com", "kuaishou.com", "huya.com", "douyu.com",
        "kugou.com", "kuwo.cn",
        # 办公/校园/政务/生活
        "wps.cn", "kdocs.cn", "csdn.net", "juejin.cn", "gitee.com",
        "edu.cn", "gov.cn", "12306.cn", "mihoyo.com",
    ],
    "daily_report_time": "09:00",   # 每日日报推送时间（HH:MM，空串=禁用）
}


def config_path() -> str:
    return os.path.join(base_dir(), "settings.json")


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
                # 直连域名是「追加」语义：内置默认 + settings.json 里追加的，去重合并。
                # （程序运行中会整写本文件，仅靠文件保存会丢内置域名）
                if not isinstance(merged.get("proxy_bypass_domains"), list):
                    merged["proxy_bypass_domains"] = list(DEFAULTS["proxy_bypass_domains"])
                else:
                    merged["proxy_bypass_domains"] = list(dict.fromkeys(
                        [*DEFAULTS["proxy_bypass_domains"],
                         *merged["proxy_bypass_domains"]]))
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
