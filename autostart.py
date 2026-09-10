# -*- coding: utf-8 -*-
"""开机自启 —— 全项目唯一实现（HKCU\\...\\Run）。

为什么单独成模块：
开机自启原先只写在 ui_glass.py 里。Web 版补这个功能时如果直接照抄，
就变成两份复制——正是设置校验（settings_schema）当年踩过的坑：
同一件事两处实现，改一处漏一处，漏掉的那个界面变成哑雷。
现在 ui_glass 与 ui_web 共用这一份。

职责边界：只读写注册表，**不落盘、不打日志、不弹提示**——那些是调用方的职责
（三个界面的日志/提示方式各不相同）。失败抛 OSError 由调用方决定怎么呈现。

为什么用 HKCU 而不是启动文件夹 / 计划任务：
- 只影响当前用户，不需要管理员权限；
- 用户在「任务管理器 → 启动」里看得见、关得掉，是 Windows 认的自启方式。
"""
import os
import sys

AUTOSTART_NAME = "AccountMasterPro2"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _command() -> str:
    """自启命令行。

    打包后指向 exe 本身；源码运行时用 pythonw.exe 拉起 main.py——
    必须用 pythonw 而非 python，否则每次开机都会弹一个黑框。
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = os.path.join(sys.base_prefix, "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable          # 兜底：pythonw 不在就退回当前解释器
    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return f'"{pyw}" "{main_py}"'


def is_enabled() -> bool:
    """当前是否已设为开机自启。

    读不到（键不存在 / 注册表受限 / 非 Windows）一律返回 False——
    「查不到」对用户而言就是「没开」，不该抛异常打断设置页加载。
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            cmd, _ = winreg.QueryValueEx(k, AUTOSTART_NAME)
            return bool(cmd)
    except Exception:
        return False


def set_enabled(enable: bool) -> None:
    """开 / 关开机自启。注册表不可写时抛 OSError。"""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY,
                        0, winreg.KEY_SET_VALUE) as k:
        if enable:
            winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(k, AUTOSTART_NAME)
            except FileNotFoundError:
                pass                  # 本来就是关的，幂等
