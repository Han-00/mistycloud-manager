# -*- coding: utf-8 -*-
"""系统代理模块离线测试（假注册表，绝不碰真实 Internet Settings）。

验证：
  - 首次 enable 备份用户原代理四元组（含 PAC），并写入目标三项、删 PAC
  - 二次 enable 不覆盖已有备份（崩溃残留自愈的前提）
  - disable 按备份恢复四元组并清掉备份键
  - 无备份时 disable 仅关开关
  - apply_if_enabled：引擎未运行 / 端口不通时拒绝写注册表
  - apply_if_enabled：收敛后重复调用静默（不重复写）
  - 启动对账：开关关但备份残留 → disable 恢复原状
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Desktop_Files\mistycloud_manager")

import sysproxy  # noqa: E402

# ---- 假注册表（(key, name) → value）----
REG = {}


def fake_read(key, name):
    return REG.get((key, name))


def fake_write(key, name, value, kind="sz"):
    REG[(key, name)] = value


def fake_delete(key, name):
    REG.pop((key, name), None)


def fake_delete_key(key):
    for k in [k for k in list(REG) if k[0] == key]:
        del REG[k]


NOTIFIED = []
sysproxy._reg_read = fake_read
sysproxy._reg_write = fake_write
sysproxy._reg_delete = fake_delete
sysproxy._reg_delete_key = fake_delete_key
sysproxy._notify = lambda: NOTIFIED.append(1)
sysproxy.available = lambda: True

INET = sysproxy._INET_KEY
BK = sysproxy._BACKUP_KEY


class FakeConfig:
    def __init__(self, d):
        self.d = d

    def get(self, k, default=None):
        return self.d.get(k, default)


class FakeEngine:
    def __init__(self, running=True, port_open=True, http_port=10809):
        self.running = running
        self.port_open = port_open
        self.http_port = http_port

    def is_running(self):
        return self.running

    def _port_open(self, port):
        return self.port_open


# ---- [1] 首次 enable：备份原状态 + 写入目标 ----
REG.clear()
REG[(INET, "ProxyEnable")] = 1
REG[(INET, "ProxyServer")] = "old.proxy:8080"
REG[(INET, "ProxyOverride")] = "intra.corp"
REG[(INET, "AutoConfigURL")] = "http://pac.example/proxy.js"

assert sysproxy.enable(10809) is True
assert REG[(INET, "ProxyEnable")] == 1
assert REG[(INET, "ProxyServer")] == "127.0.0.1:10809"
assert REG[(INET, "ProxyOverride")] == sysproxy.OVERRIDE_DEFAULT
assert (INET, "AutoConfigURL") not in REG, "PAC 必须被清除"
assert REG[(BK, "taken")] == 1
assert REG[(BK, "server")] == "old.proxy:8080", "备份必须记录用户原代理"
assert REG[(BK, "pac")] == "http://pac.example/proxy.js"
assert NOTIFIED, "必须广播设置变更"
print("[1] enable：备份四元组 + 写目标 + 删 PAC + 广播 ✓")

# ---- [2] 二次 enable 不覆盖备份 ----
assert sysproxy.enable(10810) is True
assert REG[(INET, "ProxyServer")] == "127.0.0.1:10810"
assert REG[(BK, "server")] == "old.proxy:8080", "备份不能被二次 enable 冲掉"
print("[2] 二次 enable 不覆盖已有备份 ✓")

# ---- [3] disable：按备份恢复四元组并清备份 ----
assert sysproxy.disable() is True
assert REG[(INET, "ProxyEnable")] == 1, "用户原本开着代理，恢复后仍应是 1"
assert REG[(INET, "ProxyServer")] == "old.proxy:8080"
assert REG[(INET, "ProxyOverride")] == "intra.corp"
assert REG[(INET, "AutoConfigURL")] == "http://pac.example/proxy.js"
assert not any(k[0] == BK for k in REG), "备份键必须清除"
assert not sysproxy.has_backup()
print("[3] disable 恢复四元组并清备份 ✓")

# ---- [3b] enable 带直连域名：合并进 ProxyOverride ----
REG.clear()
assert sysproxy.enable(10809, ["douyin.com", "*.ixigua.com", "douyin.com", "", None]) is True
assert REG[(INET, "ProxyOverride")] == (
    f"{sysproxy.OVERRIDE_DEFAULT};*.douyin.com;douyin.com;*.ixigua.com;ixigua.com"), \
    "必须展开成 *.域名 + 裸域 并去重"
print("[3b] enable 合并直连域名到 ProxyOverride ✓")

# ---- [4] 无备份时 disable 仅关开关 ----
REG.clear()
REG[(INET, "ProxyEnable")] = 1
REG[(INET, "ProxyServer")] = "127.0.0.1:10809"
assert sysproxy.disable() is True
assert REG[(INET, "ProxyEnable")] == 0
print("[4] 无备份时仅关开关 ✓")

# ---- [5] apply_if_enabled：引擎未运行 / 端口不通 → 拒绝 ----
REG.clear()
cfg = FakeConfig({"system_proxy": True})
assert sysproxy.apply_if_enabled(cfg, FakeEngine(running=False)) is False
assert sysproxy.apply_if_enabled(cfg, FakeEngine(port_open=False)) is False
assert (INET, "ProxyServer") not in REG, "前置不满足时绝不能写注册表"
print("[5] 引擎未运行 / 端口不通时拒绝写注册表 ✓")

# ---- [6] apply_if_enabled：收敛成功 + 重复调用静默 ----
eng = FakeEngine()
assert sysproxy.apply_if_enabled(cfg, eng) is True
assert REG[(INET, "ProxyServer")] == "127.0.0.1:10809"
writes_before = len(REG)
assert sysproxy.apply_if_enabled(cfg, eng) is True, "已一致应返回 True（生效中）"
assert len(REG) == writes_before, "已一致时不得重复写"
# 端口变了 → 重新收敛
eng.http_port = 10889
assert sysproxy.apply_if_enabled(cfg, eng) is True
assert REG[(INET, "ProxyServer")] == "127.0.0.1:10889"
print("[6] 收敛成功 / 重复静默 / 端口变更跟随 ✓")

# ---- [6b] 直连域名：写入 ProxyOverride + 变更触发重新收敛 ----
eng2 = FakeEngine()
cfg_b = FakeConfig({"system_proxy": True, "proxy_bypass_domains": ["douyin.com"]})
assert sysproxy.apply_if_enabled(cfg_b, eng2) is True
assert REG[(INET, "ProxyOverride")] == (
    f"{sysproxy.OVERRIDE_DEFAULT};*.douyin.com;douyin.com")
writes_before = len(REG)
assert sysproxy.apply_if_enabled(cfg_b, eng2) is True
assert len(REG) == writes_before, "域名未变不得重复写"
cfg_b.d["proxy_bypass_domains"] = ["douyin.com", "ixigua.com"]
assert sysproxy.apply_if_enabled(cfg_b, eng2) is True, "域名变了必须重新收敛"
assert "*.ixigua.com" in REG[(INET, "ProxyOverride")]
print("[6b] 直连域名写入 / 未变静默 / 变更重收敛 ✓")

# ---- [7] 开关关时 apply 不做任何事 ----
REG2 = dict(REG)
assert sysproxy.apply_if_enabled(FakeConfig({"system_proxy": False}), eng) is False
assert REG == REG2
print("[7] 开关关闭时 apply 不干预 ✓")

# ---- [8] 启动对账：开关关 + 备份残留（上次异常退出）→ 恢复原状 ----
REG.clear()
REG[(BK, "taken")] = 1
REG[(BK, "enable")] = 0
REG[(INET, "ProxyEnable")] = 1
REG[(INET, "ProxyServer")] = "127.0.0.1:10809"
cfg_off = FakeConfig({"system_proxy": False})
if cfg_off.get("system_proxy") is False and sysproxy.has_backup():
    assert sysproxy.disable() is True
assert REG[(INET, "ProxyEnable")] == 0
assert (INET, "ProxyServer") not in REG or REG.get((INET, "ProxyServer")) == ""
assert not sysproxy.has_backup()
print("[8] 启动对账：异常退出残留被清理 ✓")

print("\n== 系统代理模块测试通过 ==")
