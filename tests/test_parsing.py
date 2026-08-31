# -*- coding: utf-8 -*-
"""解析与配置回归：vmess aid 陷阱 / 订阅头解析 / config 原子写 / 订阅 URL 拼接。"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base64

from cloud_api import parse_vmess, parse_subscription, parse_subscription_userinfo


def _vmess_link(obj: dict) -> str:
    payload = base64.b64encode(json.dumps(obj).encode()).decode()
    return "vmess://" + payload


def test_aid_zero():
    """aid=0 是 VMess 合法标准值，不得被当成缺失改写。"""
    node = parse_vmess(_vmess_link({
        "add": "1.2.3.4", "port": 443, "id": "abc", "aid": 0, "net": "ws"}))
    assert node and node["aid"] == 0, f"aid=0 被改写: {node}"
    print(f"[1] aid=0 保留 ✓  aid={node['aid']}")


def test_aid_null():
    """aid=null 不应抛异常丢节点（历史 bug: int(None) → 节点被静默丢弃）。"""
    node = parse_vmess(_vmess_link({
        "add": "1.2.3.4", "port": 443, "id": "abc", "aid": None}))
    assert node and node["aid"] == 0, f"aid=null 应按 0 处理: {node}"
    print(f"[2] aid=null 容错 ✓  aid={node['aid']}")


def test_aid_missing():
    node = parse_vmess(_vmess_link({"add": "1.2.3.4", "port": 443, "id": "abc"}))
    assert node and node["aid"] == 0
    print("[3] aid 缺省=0 ✓")


def test_subscription_b64():
    """整段 base64 订阅（标准格式）应能解析出节点。"""
    sub = _vmess_link({"add": "5.6.7.8", "port": 8443, "id": "xyz",
                       "aid": 2, "net": "ws", "path": "/p", "host": "h.com",
                       "tls": "tls", "ps": "测试节点"})
    body = base64.b64encode(sub.encode()).decode()
    node = parse_subscription(body.encode())
    assert node and node["address"] == "5.6.7.8" and node["tls"] == "tls"
    assert node["remarks"] == "测试节点"
    print(f"[4] base64 订阅解析 ✓  remarks={node['remarks']}")


def test_subscription_empty():
    assert parse_subscription(b"") is None
    assert parse_subscription(b"not-a-sub") is None
    print("[5] 空/垃圾订阅返回 None ✓")


def test_userinfo_header():
    info = "upload=0; download=1048576; total=322122547; expire=1790000000"
    tr = parse_subscription_userinfo(info)
    assert tr["total"] == 322122547 and tr["download"] == 1048576
    assert parse_subscription_userinfo("") == {}
    print("[6] Subscription-Userinfo 解析 ✓")


def test_config_atomic_save(tmp=None):
    """config.save 原子写：落盘内容正确且不残留 .tmp。"""
    from config import Config
    tmp = tmp or os.path.join(os.path.dirname(__file__), "_tmp_settings.json")
    cfg = Config(path=tmp)
    cfg.set("min_traffic_mb", 55.5)
    cfg.set("feishu_webhook", "https://example/hook")
    cfg.save()
    assert not os.path.exists(tmp + ".tmp"), "原子写不应残留 .tmp 文件"
    data = json.load(open(tmp, encoding="utf-8"))
    assert data["min_traffic_mb"] == 55.5 and data["feishu_webhook"] == "https://example/hook"
    # 未知字段保留（win_* 等历史字段不被清洗）
    cfg.set("win_x", 100)
    cfg.save()
    data2 = json.load(open(tmp, encoding="utf-8"))
    assert data2["win_x"] == 100 and data2["min_traffic_mb"] == 55.5
    os.remove(tmp)
    print("[7] config 原子写 + 字段保留 ✓")


def test_login_error_code_no_repost():
    """login() 失败后 login_error_code() 应直接返回记录值，不再重复 POST 登录。"""
    from cloud_api import CloudAccount
    acc = CloudAccount("x@y.com", "p")
    calls = []
    orig = CloudAccount._classify_login_resp

    def fake_classify(resp):
        calls.append(1)
        return "bad_credentials"

    CloudAccount._classify_login_resp = staticmethod(fake_classify)
    try:
        # 模拟 login 失败（绕过真实网络：直接置失败码）
        acc.login_fail_code = "bad_credentials"
        code = acc.login_error_code()
        assert code == "bad_credentials"
        assert calls == [], "login() 已记录失败码时不应再发登录请求"
    finally:
        CloudAccount._classify_login_resp = orig
    print("[8] login_error_code 复用记录值（省一次 POST）✓")


def main():
    test_aid_zero()
    test_aid_null()
    test_aid_missing()
    test_subscription_b64()
    test_subscription_empty()
    test_userinfo_header()
    test_config_atomic_save()
    test_login_error_code_no_repost()
    print("\n== 解析与配置回归测试全部通过 ==")


if __name__ == "__main__":
    main()
