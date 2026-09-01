"""Windows 系统代理开关 — 一键把系统代理指向本程序的 HTTP 端口。

读写 HKCU\\...\\Internet Settings 的 ProxyEnable / ProxyServer / ProxyOverride /
AutoConfigURL；用户原代理四元组备份在注册表专用键 HKCU\\Software\\AccountMasterPro2
（不存 settings.json——Config.save() 是内存整写，会把文件里的备份冲掉导致无法恢复）。

要点：
  - 用户若用 PAC（AutoConfigURL），只写 ProxyEnable/ProxyServer 会被 PAC 压住，
    开关看似无效——备份四元组、enable 时删 AutoConfigURL、disable 时按备份恢复。
  - 备份只在不存在时创建——崩溃后重启再 enable，不能拿「已被自己改过的注册表」
    当用户原状态覆盖已有备份（崩溃残留自愈的关键）。
  - 每次改注册表后广播 InternetSetOption，让运行中的浏览器即时生效。
  - 所有异常吞掉返回 False——代理操作永不阻塞业务（对齐 notify 的哲学）。
"""
import sys

_INET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
_BACKUP_KEY = r"Software\AccountMasterPro2"
OVERRIDE_DEFAULT = "localhost;127.*;<local>"


def available() -> bool:
    return sys.platform == "win32"


# ---- 注册表薄封装（便于测试 monkeypatch）----

def _reg_read(key: str, name: str):
    """读值；键/值不存在或异常返回 None。"""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as k:
            v, _t = winreg.QueryValueEx(k, name)
            return v
    except OSError:
        return None


def _reg_write(key: str, name: str, value, kind: str = "sz") -> None:
    import winreg
    t = winreg.REG_DWORD if kind == "dword" else winreg.REG_SZ
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, t, value)


def _reg_delete(key: str, name: str) -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def _reg_delete_key(key: str) -> None:
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    except OSError:
        pass


def _notify() -> None:
    """广播代理设置变更，运行中的浏览器/WinINET 应用即时生效。"""
    try:
        import ctypes
        wininet = ctypes.WinDLL("wininet", use_last_error=True)
        wininet.InternetSetOptionW(0, 39, None, 0)   # INTERNET_OPTION_SETTINGS_CHANGED
        wininet.InternetSetOptionW(0, 37, None, 0)   # INTERNET_OPTION_REFRESH
    except Exception:
        pass


# ---- 状态读取 ----

def _read_state() -> dict:
    """当前 Internet Settings 四元组。"""
    en = _reg_read(_INET_KEY, "ProxyEnable")
    try:
        en = int(en)
    except (TypeError, ValueError):
        en = 0

    def s(name: str) -> str:
        v = _reg_read(_INET_KEY, name)
        return "" if v is None else str(v)

    return {"enable": en, "server": s("ProxyServer"),
            "override": s("ProxyOverride"), "pac": s("AutoConfigURL")}


# ---- 备份（注册表专用键，taken 标记区分「无备份」与「备份了空原状态」）----

def has_backup() -> bool:
    if not available():
        return False
    try:
        return _reg_read(_BACKUP_KEY, "taken") is not None
    except Exception:
        return False


def _take_backup() -> None:
    """快照用户原代理四元组；已存在备份则不动。"""
    if _reg_read(_BACKUP_KEY, "taken") is not None:
        return
    st = _read_state()
    _reg_write(_BACKUP_KEY, "taken", 1, "dword")
    _reg_write(_BACKUP_KEY, "enable", st["enable"], "dword")
    if st["server"]:
        _reg_write(_BACKUP_KEY, "server", st["server"])
    if st["override"]:
        _reg_write(_BACKUP_KEY, "override", st["override"])
    if st["pac"]:
        _reg_write(_BACKUP_KEY, "pac", st["pac"])


def _pop_backup() -> dict | None:
    """读取并删除备份。无备份返回 None。"""
    if _reg_read(_BACKUP_KEY, "taken") is None:
        return None
    st = {"enable": _reg_read(_BACKUP_KEY, "enable") or 0,
          "server": _reg_read(_BACKUP_KEY, "server") or "",
          "override": _reg_read(_BACKUP_KEY, "override") or "",
          "pac": _reg_read(_BACKUP_KEY, "pac") or ""}
    for name in ("taken", "enable", "server", "override", "pac"):
        _reg_delete(_BACKUP_KEY, name)
    _reg_delete_key(_BACKUP_KEY)
    try:
        st["enable"] = int(st["enable"])
    except (TypeError, ValueError):
        st["enable"] = 0
    return st


# ---- 开/关 ----

def enable(http_port: int) -> bool:
    """把系统代理指向 127.0.0.1:{http_port}。先备份用户原状态（只备份一次）。"""
    if not available():
        return False
    try:
        _take_backup()
        _reg_write(_INET_KEY, "ProxyEnable", 1, "dword")
        _reg_write(_INET_KEY, "ProxyServer", f"127.0.0.1:{int(http_port)}")
        _reg_write(_INET_KEY, "ProxyOverride", OVERRIDE_DEFAULT)
        _reg_delete(_INET_KEY, "AutoConfigURL")
        _notify()
        return True
    except (OSError, TypeError, ValueError):
        return False


def disable() -> bool:
    """关闭系统代理并恢复用户原设置（无备份则仅关闭）。"""
    if not available():
        return False
    try:
        prev = _pop_backup()
        if prev is not None:
            _reg_write(_INET_KEY, "ProxyEnable", prev["enable"], "dword")
            if prev["server"]:
                _reg_write(_INET_KEY, "ProxyServer", prev["server"])
            else:
                _reg_delete(_INET_KEY, "ProxyServer")
            if prev["override"]:
                _reg_write(_INET_KEY, "ProxyOverride", prev["override"])
            else:
                _reg_delete(_INET_KEY, "ProxyOverride")
            if prev["pac"]:
                _reg_write(_INET_KEY, "AutoConfigURL", prev["pac"])
            else:
                _reg_delete(_INET_KEY, "AutoConfigURL")
        else:
            _reg_write(_INET_KEY, "ProxyEnable", 0, "dword")
        _notify()
        return True
    except OSError:
        return False


# ---- 收敛入口（唯一事实源 = config["system_proxy"]）----

def apply_if_enabled(config, engine, log=None) -> bool:
    """对齐注册表与配置开关，返回系统代理当前是否实际生效。

    守卫链：平台可用 → 开关为开 → 引擎进程在跑 → HTTP 端口实测可连。
    注册表已一致则静默；不一致才改写并出日志（防每轮刷屏）。
    端口未就绪时不写注册表（指过去等于全网断网），等下轮收敛。
    """
    if not available() or engine is None:
        return False
    if not bool(config.get("system_proxy", False)):
        return False
    try:
        if not engine.is_running():
            return False
        hp = int(getattr(engine, "http_port", 0) or 0)
        if hp <= 0 or not engine._port_open(hp):
            return False
        st = _read_state()
        target = f"127.0.0.1:{hp}"
        if st["enable"] == 1 and st["server"] == target:
            return True   # 已一致，静默
        if not enable(hp):
            return False
        if log:
            log(f"系统代理已指向 127.0.0.1:{hp}", "ok")
        return True
    except Exception:
        return False
