"""一键验证 — 检查 MistyCloud 服务端是否恢复（无需任何配置）。

双击运行（或 python test_server.py），30 秒内给出结论。
"""
import socket
import ssl
import struct
import sys

NODE_HOST = "210.13.85.170"
NODE_PORT = 24450

def check_node_tcp():
    """1. 节点服务器 TCP 是否通。"""
    try:
        s = socket.create_connection((NODE_HOST, NODE_PORT), timeout=6)
        s.close()
        return True
    except OSError:
        return False

def check_vmess_tls():
    """2. VMess TLS 握手是否成功（TCP 通但 TLS EOF = 节点拒绝）。"""
    try:
        s = socket.create_connection((NODE_HOST, NODE_PORT), timeout=8)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ss = ctx.wrap_socket(s, server_hostname="jp3.orbitdns.xyz")
        ss.close()
        return True
    except (OSError, ssl.SSLError):
        return False

def check_subscription_api():
    """3. 订阅 API 是否可访问（服务端控制面）。"""
    try:
        s = socket.create_connection(("quantstreamd.com", 443), timeout=6)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ss = ctx.wrap_socket(s, server_hostname="quantstreamd.com")
        ss.close()
        return True
    except (OSError, ssl.SSLError):
        return False

def main():
    print("=" * 46)
    print("  MistyCloud 服务端状态检测")
    print("=" * 46)
    print()
    print(f"节点服务器: {NODE_HOST}:{NODE_PORT}")

    # 1. TCP
    tcp = check_node_tcp()
    print(f"\n[1/3] 节点 TCP 连通: {'✅ 通' if tcp else '❌ 不通'}")

    # 2. TLS
    if tcp:
        tls = check_vmess_tls()
        print(f"[2/3] 节点 VMess 握手: {'✅ 正常（服务端恢复！）' if tls else '❌ 被拒绝（服务端仍异常）'}")
    else:
        tls = False
        print("[2/3] 节点 VMess 握手: ⏭ 跳过（TCP 不通）")

    # 3. 订阅 API
    api = check_subscription_api()
    print(f"[3/3] 订阅 API 连通: {'✅ 通' if api else '❌ 不通'}")

    print()
    print("=" * 46)
    if tls:
        print(" 结论: ✅ 服务端已恢复，可以测新程序！")
    elif tcp:
        print(" 结论: ⚠️ 节点 TCP 通但握手被拒")
        print("       = 服务端仍异常（拒绝所有账号连接）")
    else:
        print(" 结论: ❌ 节点不可达（网络/服务端问题）")
    print("=" * 46)
    print()
    input("按回车退出...")

if __name__ == "__main__":
    main()
