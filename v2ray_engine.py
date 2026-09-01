"""v2ray 独立引擎 — 不依赖 Misty，直接管理 v2ray 进程。

已验证：v2ray.exe + v2ctl.exe + geoip.dat + geosite.dat 可独立运行，
改 config.json 的 UUID + 重启进程即可换号（节点服务器固定）。

架构：每个代理实例 = 一份 config + 一个 v2ray 进程 + 一个端口。
默认单实例（10808）；可扩展多实例（多端口）——本版核心是自动换号。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time

from config import Config
from http_client import HttpError


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


class V2RayEngine:
    """v2ray 进程管理器。"""

    REQUIRED_FILES = ("v2ray.exe", "v2ctl.exe", "geoip.dat", "geosite.dat")

    def __init__(self, config: Config):
        self.config = config
        self.proc: subprocess.Popen | None = None
        self.work_dir = ""          # 实例工作目录（含 v2ray.exe 副本）
        self.port = int(config.get("proxy_port", 10808))
        self.current_node: dict | None = None
        self._http_port_actual = 0   # 最近一次写配置的实际 HTTP inbound 端口
        self._discover()

    def _sync_port(self) -> int:
        """同步最新设置，避免 UI 保存端口后仍使用启动时的旧值。"""
        try:
            port = int(self.config.get("proxy_port", self.port))
            if 1 <= port <= 65535:
                self.port = port
        except (TypeError, ValueError):
            pass
        return self.port

    @property
    def http_port(self) -> int:
        """HTTP inbound 端口（系统代理指向它）。

        写过配置则取实际值（模板自带 http inbound 时是模板原端口，
        不是公式值）；未写过配置用公式兜底。
        """
        if self._http_port_actual > 0:
            return self._http_port_actual
        port = self.port
        return port + 1 if port != 10809 else 10810

    # ---- 发现 v2ray 可执行文件 ----
    def _discover(self) -> bool:
        """找 v2ray.exe。优先级：配置 → 默认 Misty 路径 → 常见位置。"""
        candidates = []
        v2ray_dir = str(self.config.get("v2ray_dir", "") or "")
        if v2ray_dir:
            candidates.append(v2ray_dir)
        candidates.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Misty"))
        candidates.append(r"C:\Users\kesai\AppData\Local\Programs\Misty")
        for d in candidates:
            exe = os.path.join(d, "v2ray.exe")
            if os.path.exists(exe):
                self.v2ray_source_dir = d
                return True
        self.v2ray_source_dir = ""
        return False

    # ---- 实例准备：复制运行文件到独立工作目录 ----
    def prepare(self) -> bool:
        """在工作目录放置 v2ray.exe 及运行时文件。返回是否就绪。"""
        if not getattr(self, "v2ray_source_dir", ""):
            return False
        try:
            src = self.v2ray_source_dir
            self.work_dir = os.path.join(_base_dir(), "v2ray_work")
            os.makedirs(self.work_dir, exist_ok=True)
            for f in self.REQUIRED_FILES:
                s = os.path.join(src, f)
                d = os.path.join(self.work_dir, f)
                if not os.path.exists(d) and os.path.exists(s):
                    shutil.copy2(s, d)
            return all(os.path.exists(os.path.join(self.work_dir, f)) for f in self.REQUIRED_FILES)
        except OSError:
            return False

    # ---- 生成 config.json ----
    def _vmess_outbound(self, node: dict) -> dict:
        """从节点 dict 显式构建 vmess outbound（不依赖模板形状）。

        不做模板内"部分替换"：模板 vnext 结构不符时会静默保留旧 UUID，
        新进程带着旧账号的密钥跑 → 假成功且烧别人的流量。
        """
        network = node.get("network") or "tcp"
        security = node.get("tls") or ""
        ss = {"network": network, "security": security}
        if network == "ws":
            ws = {"connectionReuse": True, "path": node.get("ws_path") or "/"}
            if node.get("ws_host"):
                ws["headers"] = {"Host": node["ws_host"]}
            ss["wsSettings"] = ws
        if security == "tls":
            ss["tlsSettings"] = {"allowInsecure": True,
                                 "serverName": node.get("ws_host") or node.get("address", "")}
        return {
            "tag": "proxy",
            "protocol": "vmess",
            "settings": {
                "vnext": [{
                    "address": node["address"],
                    "port": int(node["port"]),
                    "users": [{
                        "id": node["id"],
                        "alterId": int(node.get("aid") or 0),
                        "email": "t@t.tt",
                        "security": node.get("security") or "auto",
                    }],
                }],
            },
            "streamSettings": ss,
            "mux": {"enabled": True, "concurrency": 8},
        }

    def _build_config(self, node: dict, port: int) -> dict:
        """基于 Misty 原版 config 模板生成 v2ray 配置：
        inbound 重新分配端口，vmess outbound 整体替换为当前节点。

        手写简化版 config 曾导致代理不通（漏 mux 等字段）；
        模板部分替换又可能静默保留旧 UUID —— 折中：保留模板的
        log/routing/direct/block，vmess 段整体重建。
        """
        template = os.path.join(self.v2ray_source_dir, "config.json")
        try:
            with open(template, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                raise ValueError("模板不是 JSON 对象")
        except (OSError, json.JSONDecodeError, ValueError):
            return self._fallback_config(node, port)

        # SOCKS inbound 换端口；补 HTTP inbound（模板若已有 HTTP inbound，用其原端口）
        http_port = port + 1 if port != 10809 else 10810  # 10888 → 10889
        inbounds = []
        for i in cfg.get("inbounds", []):
            if not isinstance(i, dict):
                continue
            if i.get("protocol") == "socks":
                i = dict(i)
                i["port"] = port
            inbounds.append(i)
        if not any(i.get("protocol") == "http" for i in inbounds):
            inbounds.append({
                "tag": "http",
                "port": http_port,
                "listen": "127.0.0.1",
                "protocol": "http",
                "settings": {"allowTransparent": False},
            })
        cfg["inbounds"] = inbounds
        # 记录真实 http 端口：模板自带 http inbound 时是模板原端口而非公式值
        # （v2ray 忽略未知字段，已实测）
        cfg["_http_port"] = next((int(i.get("port", http_port)) for i in inbounds
                                  if isinstance(i, dict) and i.get("protocol") == "http"),
                                 http_port)

        # vmess outbound 整体替换；direct/block 等其余保留
        outbounds = [o for o in cfg.get("outbounds", [])
                     if isinstance(o, dict) and o.get("protocol") != "vmess"]
        outbounds.insert(0, self._vmess_outbound(node))
        cfg["outbounds"] = outbounds
        return cfg

    def _fallback_config(self, node: dict, port: int) -> dict:
        """模板缺失时的兜底配置（含 mux）。"""
        http_port = port + 1 if port != 10809 else 10810
        return {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "proxy",
                    "port": port,
                    "listen": "127.0.0.1",
                    "protocol": "socks",
                    "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
                    "settings": {"auth": "noauth", "udp": True},
                },
                {
                    "tag": "http",
                    "port": http_port,
                    "listen": "127.0.0.1",
                    "protocol": "http",
                    "settings": {"allowTransparent": False},
                },
            ],
            "outbounds": [self._vmess_outbound(node),
                          {"tag": "direct", "protocol": "freedom"},
                          {"tag": "block", "protocol": "blackhole"}],
            "routing": {"domainStrategy": "IPIfNonMatch",
                        "rules": [{"type": "field", "inboundTag": ["api"], "outboundTag": "api",
                                   "ip": None, "domain": None},
                                  {"type": "field", "outboundTag": "direct",
                                   "domain": ["mistycapsule.xyz"]}]},
            "_http_port": http_port,
        }

    def write_config(self, node: dict) -> str:
        """写 config.json（原子写），返回路径。"""
        self._sync_port()
        if not self.work_dir or not os.path.isdir(self.work_dir):
            if not self.prepare():
                raise RuntimeError(
                    "v2ray 运行环境未就绪（未找到 Misty 的 v2ray 文件，"
                    "请安装 Misty 到默认路径或在 settings.json 设置 v2ray_dir）")
        cfg = self._build_config(node, self.port)
        hp = next((int(i.get("port", 0)) for i in cfg.get("inbounds", [])
                   if isinstance(i, dict) and i.get("protocol") == "http"), 0)
        if hp > 0:
            self._http_port_actual = hp
        path = os.path.join(self.work_dir, "config.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, path)
        self.current_node = node
        return path

    # ---- 进程管理 ----
    def start(self) -> bool:
        """启动 v2ray（后台）。"""
        self._sync_port()
        self.stop()
        if not self.prepare():
            return False
        cfg = os.path.join(self.work_dir, "config.json")
        if not os.path.exists(cfg):
            return False
        try:
            self.proc = subprocess.Popen(
                [os.path.join(self.work_dir, "v2ray.exe"), "-config", cfg],
                cwd=self.work_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return True
        except OSError:
            return False

    def stop(self):
        """停止 v2ray：杀 self.proc + 按端口清理残留占用进程。"""
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
        # 清理端口残留（防止旧进程占端口导致新实例起不来）
        self._kill_port_owners(self.port)

    @staticmethod
    def _process_image_path(pid: str) -> str:
        """查进程可执行文件路径（ctypes，wmic 在新 Win11 已移除）。"""
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        try:
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = ctypes.c_uint32(1024)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return buf.value
                return ""
            finally:
                k32.CloseHandle(h)
        except Exception:
            return ""

    def _kill_port_owners(self, port: int):
        """杀掉占用指定端口的 v2ray 残留进程。

        只清理「本引擎工作目录下」启动的 v2ray.exe —— 端口撞车时
        （例如用户把 proxy_port 配成 Misty 自己的 10808）绝不能误杀 Misty 的进程。
        """
        if sys.platform != "win32":
            return
        try:
            out = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=10)
            text = (out.stdout or b"").decode("utf-8", errors="ignore")
            import re
            for line in text.splitlines():
                # 精确匹配本地端点，避免 :1088 误匹配 :10888 等端口。
                if re.search(rf"127\.0\.0\.1:{int(port)}\s+", line) and "LISTENING" in line:
                    m = re.search(r"(\d+)\s*$", line.strip())
                    if m:
                        pid = m.group(1)
                        if pid == str(os.getpid()):
                            continue
                        try:
                            proc = subprocess.run(
                                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                capture_output=True, timeout=8,
                            )
                            name = (proc.stdout or b"").decode(
                                "mbcs", errors="ignore").lower()
                            if "v2ray.exe" not in name:
                                continue
                            # 路径校验：只杀自己工作目录里的 v2ray
                            img = self._process_image_path(pid).lower()
                            own = os.path.normcase(os.path.abspath(self.work_dir or ""))
                            if not own or not img or not img.startswith(own):
                                continue
                            subprocess.run(["taskkill", "/F", "/PID", pid],
                                           capture_output=True, timeout=8)
                        except OSError:
                            pass
        except (subprocess.TimeoutExpired, OSError):
            pass

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ---- 端口就绪检查 ----
    def wait_port(self, timeout: float = 15.0) -> bool:
        """轮询端口可连。"""
        self._sync_port()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                return False
            if self._port_open(self.port):
                return True
            time.sleep(0.5)
        return False

    @staticmethod
    def _port_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            return False

    # ---- 出口 IP 验证（Python 原生 SOCKS5，成熟方案）----
    @staticmethod
    def _extract_ip(text: str) -> str:
        import re
        m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", text or "")
        return m.group(1) if m else ""

    def check_exit_ip(self, timeout: float = 10.0) -> str:
        """走 SOCKS 代理拿出口 IP。

        重要：不用 curl——Windows 上 curl 的 --socks5 行为不可靠
        （会绕过代理直连，返回直连 IP 造成假象）。用原生 SOCKS5 握手。
        代理模式下只用国外服务——国内 echo 可能被节点分流直连，
        把直连 IP 误当代理出口（v1.0.9 结论，绝不可加国内服务）。
        """
        self._sync_port()
        import socket
        import struct
        import ssl
        hosts = [("ipinfo.io", "/ip"), ("api.ipify.org", "/"),
                 ("ifconfig.me", "/ip"), ("ip-api.com", "/line/?fields=query")]
        for host, path in hosts:
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
                s.settimeout(timeout)
                # SOCKS5 握手（无认证）
                s.sendall(b"\x05\x01\x00")
                if self._recv_exact(s, 2) != b"\x05\x00":
                    s.close()
                    continue
                # 连接请求（域名）
                hb = host.encode()
                s.sendall(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + struct.pack(">H", 443))
                resp = self._recv_exact(s, 10)
                if len(resp) < 2 or resp[1] != 0:
                    s.close()
                    continue
                # TLS
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                ss = ctx.wrap_socket(s, server_hostname=host)
                ss.sendall(b"GET " + path.encode() + b" HTTP/1.1\r\nHost: "
                           + host.encode() + b"\r\nConnection: close\r\n\r\n")
                data = b""
                while True:
                    chunk = ss.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                ss.close()
                text = data.decode("utf-8", errors="ignore")
                # 只在 body（\r\n\r\n 之后）找 IP，兼容 chunked 等编码
                body = text.split("\r\n\r\n", 1)[-1] if "\r\n\r\n" in text else text
                ip = self._extract_ip(body)
                if ip:
                    return ip
            except (OSError, ssl.SSLError):
                continue
        return ""

    @staticmethod
    def _recv_exact(s, n: int) -> bytes:
        """recv 恰好 n 字节（不足循环补齐，超时/断开返回已收部分）。"""
        buf = b""
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

    def check_direct_ip(self, timeout: float = 8.0) -> str:
        """直连出口 IP（无代理）。

        国内服务优先（对齐 v1.0.9）：大陆直连访问 ipinfo/ipify 常被墙或
        429 限流，导致"直连出口未确认"误报；国内 echo 秒回。
        """
        urls = ("https://myip.ipip.net",          # "当前 IP：x.x.x.x 来自于：..."
                "http://ip.3322.net",             # 纯文本 IP
                "http://members.3322.org/dyndns/getip",
                "https://ipinfo.io/ip",           # 国外兜底
                "http://ip-api.com/line/?fields=query")
        for url in urls:
            try:
                from http_client import get, HttpError
                status, body = get(url, timeout=timeout, retries=1)
                if status == 200:
                    ip = self._extract_ip(body.decode("utf-8", errors="ignore"))
                    if ip:
                        return ip
            except (HttpError, OSError, ValueError):
                continue
        return ""


def find_misty_dir() -> str:
    """返回 Misty 安装目录（供参考，v2ray 源）。"""
    local = os.environ.get("LOCALAPPDATA", "")
    for p in (os.path.join(local, "Programs", "Misty"),
              r"C:\Users\kesai\AppData\Local\Programs\Misty"):
        if os.path.exists(os.path.join(p, "v2ray.exe")):
            return p
    return ""
