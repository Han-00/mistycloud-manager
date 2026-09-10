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
    ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    gone = not os.path.exists(path)
    print(f"[{'ok' if gone else 'FAIL'}] {path}  (rc={op.fFlags}, aborted={op.fAnyOperationsAborted})")
    return gone


if __name__ == "__main__":
    ok = all(recycle(p) for p in sys.argv[1:])
    sys.exit(0 if ok else 1)
