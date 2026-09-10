# -*- coding: utf-8 -*-
"""直连分流注入测试（离线，不碰真实 v2ray / 注册表）。

验证 v2ray_engine 把 settings.json 的 proxy_bypass_domains 注入生成配置：
  - _fallback_config 兜底路径：直连规则追加到 routing，域名去重、去「*.」前缀
  - 列表为空时不注入任何规则
  - _apply_bypass_routing 对缺 routing / 缺 direct outbound 的配置能补齐
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Desktop_Files\mistycloud_manager")

from v2ray_engine import V2RayEngine  # noqa: E402

NODE = {"address": "a.example.com", "port": 443, "id": "test-uuid", "aid": 0}


class FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, k, default=None):
        return self.d.get(k, default)


def make_engine(domains) -> V2RayEngine:
    eng = V2RayEngine.__new__(V2RayEngine)   # 跳过 __init__，避免探测 v2ray 文件
    eng.config = FakeConfig({"proxy_bypass_domains": domains})
    return eng


# ---- [1] 兜底配置：直连域名注入 routing，去重、去「*.」 ----
eng = make_engine(["douyin.com", "*.ixigua.com", "douyin.com"])
cfg = eng._fallback_config(NODE, 10808)
first = cfg["routing"]["rules"][0]
assert first["outboundTag"] == "direct"
assert "douyin.com" in first["domain"] and "ixigua.com" in first["domain"]
assert "*.douyin.com" not in first["domain"], "引擎层是后缀匹配，不该保留通配写法"
assert len(first["domain"]) == 2, "重复项必须去掉"
assert any(o.get("tag") == "direct" for o in cfg["outbounds"]), "direct outbound 必须存在"
print("[1] 兜底配置注入直连规则（去重/去通配，置顶）✓")

# ---- [2] 列表为空：不注入 ----
eng0 = make_engine([])
cfg0 = eng0._fallback_config(NODE, 10808)
assert len(cfg0["routing"]["rules"]) == 2, "空列表不得追加直连规则（兜底自带 api+misty 两条）"
print("[2] 空列表不注入 ✓")

# ---- [3] 模板形状的配置：缺 routing / 缺 direct outbound 时补齐 ----
eng = make_engine(["a.com"])
cfg_t = {"inbounds": [], "outbounds": [{"tag": "proxy", "protocol": "vmess"}]}
eng._apply_bypass_routing(cfg_t)
assert any(isinstance(o, dict) and o.get("tag") == "direct"
           for o in cfg_t["outbounds"]), "必须补 direct outbound"
assert cfg_t["routing"]["rules"][-1]["domain"] == ["a.com"]
print("[3] 缺 routing/outbound 时自动补齐 ✓")

print("\n== 直连分流注入测试通过 ==")
