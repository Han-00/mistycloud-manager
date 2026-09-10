# -*- coding: utf-8 -*-
"""autostart 模块测试（离线、零副作用）。

关键约束：**绝不写真实注册表**。
HKCU\\...\\Run 是用户的开机启动项，测试往里写值 = 给用户的机器留一个
指向测试目录的自启项，测试跑完也不会自己消失。所以这里全程用内存版
winreg 假模块顶掉真 winreg（autostart 的函数内部才 `import winreg`，
替换 sys.modules['winreg'] 即可拦下）。

覆盖：
- _command() 在打包态 / 源码态分别生成什么命令行
- is_enabled() 对「键不存在」「注册表不可用」「非 Windows」一律 False 且不抛
- set_enabled() 开→读得到、关→读不到、重复关幂等
- 注册表不可写时 set_enabled() 抛 OSError（由调用方决定怎么呈现）
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import autostart  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============ 内存版 winreg ============
class _FakeKey:
    def __init__(self, store):
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeWinreg:
    """够 autostart 用的最小实现。store 是字典，模拟整个 Run 键。"""

    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 0x0002
    REG_SZ = 1

    def __init__(self, *, readable=True, writable=True):
        self.store = {}
        self.readable = readable
        self.writable = writable

    def OpenKey(self, root, path, reserved=0, access=None):
        if not self.readable and access:
            raise PermissionError("拒绝访问")
        if not self.readable:
            raise FileNotFoundError("键不存在")
        return _FakeKey(self.store)

    def QueryValueEx(self, key, name):
        if name not in key._store:
            raise FileNotFoundError(f"值 {name} 不存在")
        return key._store[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, typ, value):
        if not self.writable:
            raise PermissionError("注册表访问受限")
        key._store[name] = value

    def DeleteValue(self, key, name):
        if name not in key._store:
            raise FileNotFoundError(f"值 {name} 不存在")
        del key._store[name]


class _Ctx:
    """临时替换 sys.modules['winreg']，退出时还原。"""

    def __init__(self, mod):
        self.mod = mod

    def __enter__(self):
        self.saved = sys.modules.get("winreg", "MISSING")
        sys.modules["winreg"] = self.mod
        return self.mod

    def __exit__(self, *a):
        if self.saved == "MISSING":
            sys.modules.pop("winreg", None)
        else:
            sys.modules["winreg"] = self.saved
        return False


def main():
    # ---- [1] _command()：打包态指向 exe 本身 ----
    saved_exec = sys.executable
    saved_frozen = getattr(sys, "frozen", None)
    saved_prefix = sys.base_prefix
    try:
        sys.frozen = True
        sys.executable = r"C:\Apps\账号大师Pro2\账号大师Pro2.exe"
        cmd = autostart._command()
        assert cmd == r'"C:\Apps\账号大师Pro2\账号大师Pro2.exe"', f"打包态命令错误: {cmd}"
        print("[1] 打包态 _command() 指向 exe 本身 ✓")

        # ---- [2] _command()：源码态用 pythonw + main.py ----
        # 伪造 base_prefix 到一个含 pythonw.exe 的临时目录，避免依赖真实环境
        # （不是每套 Python 都装了 pythonw.exe，靠环境会得到时对时错的测试）。
        del sys.frozen
        with tempfile.TemporaryDirectory() as fake_prefix:
            open(os.path.join(fake_prefix, "pythonw.exe"), "w").close()
            sys.base_prefix = fake_prefix
            sys.executable = os.path.join(fake_prefix, "python.exe")
            cmd = autostart._command()
            assert "pythonw.exe" in cmd, f"应优先用 pythonw.exe（否则开机弹黑框）: {cmd}"
            assert not cmd.startswith(f'"{sys.executable}"'), \
                f"不该用 python.exe（会弹黑框）: {cmd}"
            assert "main.py" in cmd, f"源码态命令没指向 main.py: {cmd}"
            assert cmd.count('"') == 4, f"pyw 与 main.py 两段路径都该加引号: {cmd}"
            assert os.path.abspath(os.path.join(ROOT, "main.py")) in cmd, \
                f"main.py 路径不对: {cmd}"
        print("[2] 源码态 _command() 用 pythonw.exe + main.py，两段路径都带引号 ✓")

        # ---- [2b] pythonw.exe 缺失时兜底，不能拼出空路径 ----
        with tempfile.TemporaryDirectory() as empty_prefix:
            sys.base_prefix = empty_prefix
            sys.executable = os.path.join(empty_prefix, "python.exe")
            cmd = autostart._command()
            assert '""' not in cmd, f"兜底后不该出现空路径: {cmd}"
            assert "main.py" in cmd, f"兜底后仍要指向 main.py: {cmd}"
        print("[2b] pythonw.exe 缺失时兜底为当前解释器，不拼出空路径 ✓")
    finally:
        sys.executable = saved_exec
        sys.base_prefix = saved_prefix
        if saved_frozen is None:
            if hasattr(sys, "frozen"):
                del sys.frozen
        else:
            sys.frozen = saved_frozen

    # ---- [3] 键不存在时 is_enabled() 返回 False，不抛 ----
    with _Ctx(_FakeWinreg()) as w:
        assert autostart.is_enabled() is False
        assert w.store == {}, "只读不该写任何东西"
    print("[3] 未设置时 is_enabled()=False 且不写注册表 ✓")

    # ---- [4] 开 → 读得到；值就是 _command() ----
    with _Ctx(_FakeWinreg()) as w:
        autostart.set_enabled(True)
        assert w.store.get(autostart.AUTOSTART_NAME), f"没写进 Run 键: {w.store}"
        assert autostart.is_enabled() is True
        assert w.store[autostart.AUTOSTART_NAME] == autostart._command(), \
            "写入的命令行必须与 _command() 一致"
    print("[4] set_enabled(True) 写入 Run 键，is_enabled() 立即为 True ✓")

    # ---- [5] 关 → 读不到；重复关幂等 ----
    with _Ctx(_FakeWinreg()) as w:
        autostart.set_enabled(True)
        autostart.set_enabled(False)
        assert autostart.is_enabled() is False, "关闭后仍读得到"
        assert autostart.AUTOSTART_NAME not in w.store
        autostart.set_enabled(False)   # 本来就是关的，不能抛
    print("[5] set_enabled(False) 删除自启项，重复关闭不抛（幂等） ✓")

    # ---- [6] 注册表读不了：is_enabled() 返回 False 而非抛异常 ----
    # 设置页加载时会读它。若这里抛异常，整个设置页会打不开——
    # 「查不到」对用户而言就是「没开」，不该拖垮界面。
    with _Ctx(_FakeWinreg(readable=False)):
        assert autostart.is_enabled() is False
    print("[6] 注册表不可读时 is_enabled()=False，不拖垮设置页 ✓")

    # ---- [7] 注册表写不了：set_enabled() 抛，由调用方决定怎么呈现 ----
    with _Ctx(_FakeWinreg(writable=False)):
        try:
            autostart.set_enabled(True)
        except OSError:
            pass
        else:
            raise AssertionError("写不进去时必须抛 OSError，否则调用方会误报成功")
    print("[7] 注册表不可写时 set_enabled() 抛 OSError，不静默成功 ✓")

    # ---- [8] 完全没有 winreg（非 Windows / 被裁剪）：读返回 False ----
    saved = sys.modules.get("winreg", "MISSING")
    sys.modules["winreg"] = None      # import 会抛 ImportError
    try:
        assert autostart.is_enabled() is False
    finally:
        if saved == "MISSING":
            sys.modules.pop("winreg", None)
        else:
            sys.modules["winreg"] = saved
    print("[8] 无 winreg 环境下 is_enabled()=False，不崩 ✓")

    print("\n== autostart 测试通过 ==（全程未触碰真实注册表）")


if __name__ == "__main__":
    main()
