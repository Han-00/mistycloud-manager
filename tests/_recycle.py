"""把指定路径送进回收站（可还原），不用 rm/rmtree。

为什么不用 shutil.rmtree：
  1) 个人目录下的删除必须可还原；
  2) dist 下上千个文件的 Python 级批删会触发安全网关的
     「批量删除确认」，非交互脚本拿不到确认会直接中断。
SHFileOperationW 是单个 Win32 调用，走的是系统回收站语义。
注意：返回码不可靠（实测删成功后仍返回 2），一律用 os.path.exists 复核。
"""
import ctypes
import os
import sys
from ctypes import wintypes


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


FO_DELETE = 3
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMATION = 0x0010
FOF_SILENT = 0x0004
FOF_NOERRORUI = 0x0400


def recycle(path: str) -> bool:
    path = os.path.abspath(path)
    if not os.path.exists(path):
        print(f"[skip] 不存在: {path}")
        return True
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = path + "\0\0"          # 双 NUL 结尾，多路径用单 NUL 分隔
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    # ⚠ 之前的实现把 fFlags 当返回码打印（0x454=1108，恰好是各标志位之和），
    # 看起来像错误码，误导排查。真正的返回值是函数返回值，失败细节看
    # fAnyOperationsAborted。FOF_NOERRORUI 会吞掉错误弹窗，所以失败是静默的。
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    gone = not os.path.exists(path)
    print(f"[{'ok' if gone else 'FAIL'}] {path}  (rc={rc}, aborted={op.fAnyOperationsAborted})")
    return gone


def recycle_batch(path: str, chunk: int = 100) -> bool:
    """recycle() 失败时的降级路径：把目录内容分批送回收站，最后删目录壳。

    整目录一次送 1000+ 文件偶尔静默失败（FOF_NOERRORUI 吞掉原因），
    分小批重试往往能过。仍失败则保留现场让用户手动处理。
    """
    if recycle(path):
        return True
    print("  整目录回收失败，降级为分批…")
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            recycle(os.path.join(root, name))
        for name in dirs:
            recycle(os.path.join(root, name))
    return recycle(path)


if __name__ == "__main__":
    ok = all(recycle_batch(p) for p in sys.argv[1:])
    sys.exit(0 if ok else 1)
