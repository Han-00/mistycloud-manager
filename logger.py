"""轻量文件日志 — UI 之外的后台活动留痕（对齐 AccountMasterPro switch.log）。

app.log 写在程序同目录；供换号/监控等后台流程诊断。
线程安全；写失败静默（日志永不影响业务）。
"""
import os
import threading
import time

from paths import base_dir

_lock = threading.Lock()
_fp = None
_path = ""


def log_path() -> str:
    return os.path.join(base_dir(), "app.log")


def filelog(msg: str) -> None:
    global _fp
    try:
        with _lock:
            if _fp is None:
                _path = log_path()
                # 超过 2MB 轮换，避免无限增长
                try:
                    if os.path.exists(_path) and os.path.getsize(_path) > 2 * 1024 * 1024:
                        old = _path + ".old"
                        if os.path.exists(old):
                            os.remove(old)
                        os.replace(_path, old)
                except OSError:
                    pass
                _fp = open(_path, "a", encoding="utf-8")
            _fp.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            _fp.flush()
    except Exception:
        pass


def close():
    global _fp
    try:
        with _lock:
            if _fp:
                _fp.close()
                _fp = None
    except Exception:
        pass
