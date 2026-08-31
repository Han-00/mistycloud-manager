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
    from ui import AppUI

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

    # 启动：优先使用已有账号（无则自动注册 + 换号）
    def bootstrap():
        if not engine._discover():
            _log("未找到 v2ray：请安装 Misty 客户端到默认路径，"
                 "或在 settings.json 里设置 v2ray_dir 指向 v2ray.exe 所在目录", "err")
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
