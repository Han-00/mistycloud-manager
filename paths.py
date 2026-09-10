# -*- coding: utf-8 -*-
"""运行目录解析 — 全项目唯一实现。

为什么要有这个模块：
1. 历史上 config / account_pool / logger / stats / v2ray_engine 各自写了一份
   `_base_dir()`，五份数值一致但改一处漏四处，是典型的复制粘贴债。
2. 打包分发（尤其解压到 C:\\Program Files 这类不可写目录）时，exe 同级目录可能
   写不进去。此时必须五个模块**同步回退**到同一个可写目录，否则会出现
   「settings.json 写在 A 处、app.log 写在 B 处、v2ray_work 又建在 C 处」的分裂，
   表现为配置改了不生效、日志找不到、代理起不来。

路径策略：
- 冻结（PyInstaller）：exe 同级目录可写 → 用它（绿色包，数据跟着程序走）；
  不可写 → %LOCALAPPDATA%/AccountMasterPro2。
- 源码运行：项目目录。

只读资源（webui 页面、内嵌的 v2ray 引擎）另走 resource_dir()，
打包后在 _internal/ 下，**永远不要往里写**。
"""
import os
import sys

_APP_DIR_NAME = "AccountMasterPro2"
_dir_cache: str | None = None


def _writable(d: str) -> bool:
    """真实写测试——os.access 在 Windows 上对 ACL 的判断不可靠。"""
    probe = os.path.join(d, ".write_probe")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("")
        os.remove(probe)
        return True
    except OSError:
        return False


def base_dir() -> str:
    """可写的运行数据目录（settings.json / accounts.json / app.log / stats.json / v2ray_work）。"""
    global _dir_cache
    if _dir_cache:
        return _dir_cache

    if getattr(sys, "frozen", False):
        d = os.path.dirname(sys.executable)
    else:
        d = os.path.dirname(os.path.abspath(__file__))

    if not _writable(d):
        fallback = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
            _APP_DIR_NAME)
        try:
            os.makedirs(fallback, exist_ok=True)
        except OSError:
            pass
        d = fallback

    _dir_cache = d
    return d


def resource_dir() -> str:
    """只读资源目录：onefile=临时解包目录，onedir=exe 同级 _internal；源码=项目目录。"""
    return getattr(sys, "_MEIPASS", "") or os.path.dirname(os.path.abspath(__file__))


def is_portable() -> bool:
    """数据目录是否就落在程序目录（绿色包模式）。用于界面提示/排障。"""
    return base_dir() == os.path.dirname(
        sys.executable if getattr(sys, "frozen", False)
        else os.path.abspath(__file__))
