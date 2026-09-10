"""账号大师 Pro 2.0 — 入口。

全新程序（正常 Python 源码，非字节码修补）：
  自动切换 MistyCloud 账号 + 独立 v2ray 代理引擎。
"""
import os
import sys


def _single_instance() -> bool:
    """单实例保护：Windows 互斥体。返回 False=已有实例在跑。

    注意：必须用 use_last_error=True + ctypes.get_last_error() 读错误码——
    windll.kernel32.GetLastError() 经 ctypes 调用栈后错误码可能被冲掉，
    导致第二个实例误判为"首个实例"（实测双开事故）。
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.CreateMutexW(None, False, "Local\\AccountMasterPro2")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            return False
        # 保持句柄存活
        globals()["_mutex_handle"] = handle
        return True
    except Exception:
        return True


def _load_ui():
    """按 Web → 玻璃 → ttk 顺序加载界面实现。

    返回 (AppUI 类, 实现名, 回退原因列表)。

    回退必须留痕——静默降级会把「界面怎么突然变回旧版了」变成无法定位的玄学：
    pywebview 没装、WebView2 初始化失败、打包漏了模块，三种原因表现完全一样。
    返回原因列表而非直接打日志，是因为此刻 UI 还没建起来，日志无处可去，
    要等 main() 里 _log 挂好之后再补报。

    ⚠ 必须**显式探测依赖**，不能只 import ui_web：
    ui_web.py 是在 run() 里才惰性 `import webview` 的，顶层不引入 pywebview，
    所以光 import 它永远不会失败——依赖缺失要等到建窗口时才炸，
    回退链就形同虚设（实测：屏蔽 pywebview 后仍会选中 Web 版，然后启动即崩）。
    """
    notes = []
    try:
        import webview  # noqa: F401  显式探测，见上方说明
        from ui_web import WebAppUI
        return WebAppUI, "Web 版 (pywebview)", notes
    except Exception as e_web:
        notes.append(f"ui_web 加载失败: {e_web!r}")
    try:
        import customtkinter  # noqa: F401  ui_glass 顶层已引入，这里保持对称
        from ui_glass import GlassAppUI
        return GlassAppUI, "玻璃版 (customtkinter)", notes
    except Exception as e_glass:
        notes.append(f"ui_glass 加载失败: {e_glass!r}")
    from ui import AppUI
    return AppUI, "ttk 版（终极降级）", notes


def main():
    if not _single_instance():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "账号大师 Pro 2.0 已在运行。", "提示", 0x40)
        except Exception:
            pass
        return

    from config import Config
    from account_pool import AccountPool
    from v2ray_engine import V2RayEngine
    from switcher import Switcher
    from monitor import Monitor
    AppUI, ui_impl, ui_notes = _load_ui()

    config = Config()
    pool = AccountPool(config)
    engine = V2RayEngine(config)
    switcher = Switcher(config, pool, engine)
    monitor = Monitor(config, pool, switcher, engine)

    ui = AppUI(config, pool, switcher, engine, monitor)
    # 日志挂钩（ui.log 线程安全；同时写 app.log 文件留痕，UI 关闭后仍可诊断）
    def _log(m, tag=""):
        from logger import filelog
        filelog(m)
        ui.log(m, tag)
    switcher.log = _log
    monitor.log = _log

    # 界面实现留痕：正常时一行白字，发生回退时连同原因标红——
    # 出问题第一个该看的就是这条。
    _log(f"界面实现：{ui_impl}", "err" if ui_notes else "")
    for _note in ui_notes:
        _log(f"  ↳ 回退原因 — {_note}", "err")

    # 系统代理启动对账：开关关着但注册表备份还在 = 上次异常退出（崩溃/断电），
    # 恢复用户原代理设置（优雅退出路径会在 _quit 里还原，这里兜非优雅路径）
    try:
        import sysproxy
        if (sysproxy.available()
                and not config.get("system_proxy", False)
                and sysproxy.has_backup()):
            sysproxy.disable()
            _log("检测到系统代理残留（上次异常退出），已恢复原代理设置", "ok")
    except Exception:
        pass

    # 启动：优先使用已有账号（无则自动注册 + 换号）
    def bootstrap():
        if not engine._discover():
            _log("未找到 v2ray 引擎：请确认程序目录完整（分发版不要只复制 exe，"
                 "需连同 _internal 目录一起），或在 settings.json 里设置 "
                 "v2ray_dir 指向 v2ray.exe 所在目录", "err")
            monitor.start()
            return
        if pool.count() == 0:
            _log("账号库为空，开始自动注册首个账号...")
            acc = pool.register_one(log=_log)
            if acc:
                _log(f"首个账号注册成功: {acc['email']}", "ok")
        active = pool.get_active()
        if not active and engine._discover():
            _log("无在用账号，执行首次换号...")
            r = switcher.auto_switch("首次启动")
            if not r.ok:
                _log(f"首次换号失败: {r.error}", "err")
        elif not engine.is_running() and engine._discover():
            # 恢复：用当前账号重新拉起代理
            active = pool.get_active()
            if active:
                from cloud_api import CloudAccount
                cloud = CloudAccount(active["email"], active.get("password", ""))
                recovery_error = None
                if not cloud.login():
                    try:
                        code = cloud.login_error_code() or "unknown"
                    except Exception:
                        code = "unknown"
                    recovery_error = f"登录失败（{code}）"
                elif not cloud.fetch_subscription():
                    recovery_error = cloud.sub_error or "拉取订阅失败"
                elif not cloud.node:
                    recovery_error = "节点解析失败（订阅无有效节点）"
                else:
                    try:
                        engine.write_config(cloud.node)
                    except Exception as e:
                        recovery_error = f"写入配置失败: {e}"
                    if recovery_error is None and not engine.start():
                        recovery_error = "v2ray 启动失败"

                if recovery_error:
                    _log(f"恢复当前账号代理失败（{recovery_error}），尝试自动换号", "err")
                    r = switcher.auto_switch("恢复失败，尝试换号")
                    if not r.ok:
                        _log(f"自动换号失败: {r.error}", "err")
                else:
                    pool.set_class_expire(active["email"], cloud.class_expire)
                    _log("已恢复当前账号代理", "ok")
        # 系统代理收敛：开关开着且端口就绪则写入注册表（未就绪由监控每轮补齐）
        try:
            import sysproxy
            sysproxy.apply_if_enabled(config, engine,
                                      log=lambda m, *_a: _log(m, "ok"))
        except Exception:
            pass
        monitor.start()

    import threading
    threading.Thread(target=bootstrap, daemon=True).start()
    try:
        ui.run()
    finally:
        from logger import close as _log_close
        _log_close()


if __name__ == "__main__":
    main()
