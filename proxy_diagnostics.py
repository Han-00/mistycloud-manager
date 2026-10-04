"""只读代理诊断：配置预期、Windows 设置、当前 TCP 和引擎历史入站分别报告。

本模块不发起网络请求，不启动引擎，不修改系统设置，不读取账号或引擎配置。
"""
from __future__ import annotations

import base64
import datetime as dt
import fnmatch
import ipaddress
import json
import os
import re
import subprocess
import sys

_MAX_LOG_BYTES = 64 * 1024
_MAX_LOG_LINES = 20
_SHELL_TIMEOUT = 8
_INET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
_PROCESS_QUERY = re.compile(r"[\w .-]{1,64}\Z", re.UNICODE)
_ACCESS = re.compile(
    r"^(?P<timestamp>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+"
    r"\S+\s+accepted\s+(?:(?:tcp|udp):|//)?(?P<destination>\S+)\s+"
    r"\[(?:(?P<inbound>[A-Za-z0-9_.-]{1,64})\s*(?:->|>>)\s*)?"
    r"(?P<outbound>[A-Za-z0-9_.-]{1,64})\](?:\s.*)?$"
)

# 固定脚本：用户输入只在 Python 内过滤，绝不进入 shell。
_CONNECTION_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$processes = @(Get-Process -ErrorAction Stop | Select-Object Id, ProcessName)
$connections = @(Get-NetTCPConnection -ErrorAction Stop | Select-Object LocalAddress, LocalPort, RemoteAddress, RemotePort, @{Name='State'; Expression={[string]$_.State}}, OwningProcess)
@{processes=$processes; connections=$connections} | ConvertTo-Json -Depth 4 -Compress
"""


def _local_now():
    return dt.datetime.now()


def _port(value):
    try:
        number = int(value)
        return number if 1 <= number <= 65535 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _established(value):
    # MSFT_NetTCPConnection.State 的 Established 枚举值为 5；兼容旧快照序列化。
    return str(value).strip().lower() in ("established", "5")


def _engine_pid(engine):
    proc = getattr(engine, "proc", None)
    try:
        if callable(getattr(proc, "poll", None)) and proc.poll() is not None:
            return None
        pid = int(getattr(proc, "pid", 0))
        return pid if pid > 0 else None
    except (TypeError, ValueError, OSError):
        return None


def _listener_covers(listener_address, remote_address):
    try:
        listener = ipaddress.ip_address(str(listener_address))
        remote = ipaddress.ip_address(str(remote_address))
        listener = getattr(listener, "ipv4_mapped", None) or listener
        remote = getattr(remote, "ipv4_mapped", None) or remote
        return listener == remote or (listener.is_unspecified and listener.version == remote.version)
    except ValueError:
        return False


def _loopback(value):
    try:
        address = ipaddress.ip_address(str(value))
        return address.is_loopback or bool(
            getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback
        )
    except ValueError:
        return str(value).lower() == "localhost"


def _destination(value):
    """只保留域名/IP 和有效端口，拒绝 URL、用户信息及其他日志内容。"""
    host, sep, raw_port = str(value).rpartition(":")
    port = _port(raw_port)
    if not sep or not port:
        return None
    host = host.strip("[]").lower().rstrip(".")
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        if len(host) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            return None
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
               for label in host.split(".")):
            return None
    return host, port


def _run_powershell(script):
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, encoding="utf-8", errors="replace", timeout=_SHELL_TIMEOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    if result.returncode:
        raise RuntimeError("connection_collection_failed")
    payload = json.loads(result.stdout.lstrip("\ufeff").strip())
    if not isinstance(payload, dict) or not all(isinstance(payload.get(k), list)
                                              for k in ("processes", "connections")):
        raise ValueError("invalid_connection_snapshot")
    return payload


def _collect_connections():
    if sys.platform != "win32":
        return {"status": "unknown", "reason": "unsupported_platform"}
    try:
        return {"status": "observed", **_run_powershell(_CONNECTION_SCRIPT)}
    except subprocess.TimeoutExpired:
        return {"status": "unknown", "reason": "timeout"}
    except Exception:
        # 不返回 stderr/异常文本，避免夹带进程信息或机器环境。
        return {"status": "unknown", "reason": "collection_failed"}


def _read_system_proxy():
    if sys.platform != "win32":
        return {"status": "unknown", "reason": "unsupported_platform"}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INET_KEY, 0, winreg.KEY_READ) as key:
            def read(name, default):
                try:
                    return winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    return default
            return {"status": "observed", "enable": read("ProxyEnable", 0),
                    "server": read("ProxyServer", ""), "pac": read("AutoConfigURL", ""),
                    "override": read("ProxyOverride", "")}
    except Exception:
        return {"status": "unknown", "reason": "registry_unavailable"}


def _proxy_targets(server):
    targets = {}
    for item in str(server or "").split(";"):
        scheme, sep, endpoint = item.strip().partition("=")
        parsed = _destination(endpoint if sep else scheme)
        if parsed and _loopback(parsed[0]):
            for protocol in ([scheme.lower()] if sep else ["http", "https"]):
                targets[protocol] = parsed[1]
    return targets


def _system_evidence(raw, ports, domain):
    if raw.get("status") != "observed":
        return {"status": "unknown", "reason": raw.get("reason", "registry_unavailable"),
                "message": "无法读取 Windows 当前系统代理设置。"}
    targets = _proxy_targets(raw.get("server"))
    bypass = None
    if domain:
        patterns = [p.strip() for p in str(raw.get("override") or "").lower().split(";")]
        bypass = any((p == "<local>" and "." not in domain) or
                     (p != "<local>" and fnmatch.fnmatchcase(domain, p.strip()))
                     for p in patterns if p.strip())
    try:
        enabled = bool(int(raw.get("enable", 0)))
    except (TypeError, ValueError):
        return {"status": "unknown", "reason": "invalid_registry_value",
                "message": "Windows 系统代理启用值无法确认。"}
    http_points = bool(ports["http"] and targets.get("http") == ports["http"])
    https_points = bool(ports["http"] and targets.get("https") == ports["http"])
    socks_points = bool(ports["socks"] and targets.get("socks") == ports["socks"])
    pac_configured = bool(raw.get("pac"))
    parts = ["Windows 手动系统代理已启用。" if enabled else "Windows 手动系统代理未启用。"]
    parts.extend([
        f"保存的 HTTP 代理{'指向' if http_points else '未指向'}本工具本地 HTTP 端口。",
        f"保存的 HTTPS 代理{'指向' if https_points else '未指向'}本工具本地 HTTP 端口。",
        f"保存的 SOCKS 代理{'指向' if socks_points else '未指向'}本工具本地 SOCKS 端口。",
    ])
    if bypass is not None:
        parts.append("目标域名命中系统代理例外。" if bypass else "目标域名未命中系统代理例外。")
    parts.append("已配置 PAC，实际选择未验证。" if pac_configured else "未配置 PAC URL。")
    parts.append("这些设置不能证明应用遵循系统代理。")
    return {"status": "observed", "enabled": enabled,
            "http_points_to_engine": http_points, "https_points_to_engine": https_points,
            "socks_points_to_engine": socks_points,
            "pac_configured": pac_configured, "domain_bypassed": bypass,
            "message": "".join(parts)}


def _queries(app_query):
    if not app_query:
        return ["Claude", "Antigravity"]
    if not isinstance(app_query, str) or len(app_query) > 256:
        return None
    queries = list(dict.fromkeys(re.sub(r"\.exe$", "", s.strip(), flags=re.IGNORECASE)
                               for s in re.split(r"[,，]", app_query) if s.strip()))
    if not queries or len(queries) > 4 or any(not _PROCESS_QUERY.fullmatch(s) for s in queries):
        return None
    return queries


def _application_evidence(snapshot, queries, ports, engine=None):
    if queries is None:
        return {"status": "invalid_query", "items": [], "message": "应用查询只接受进程名，最多 4 项，用逗号分隔。"}
    if snapshot.get("status") != "observed":
        return {"status": snapshot.get("status", "unknown"), "items": [],
                "reason": snapshot.get("reason", "collection_failed"),
                "message": "未取得当前 TCP 快照，无法判断桌面应用是否连接本工具。"}
    engine_pid = _engine_pid(engine)
    listeners = []
    if engine_pid is not None:
        for conn in snapshot.get("connections", []):
            if not isinstance(conn, dict) or str(conn.get("State")).strip().lower() not in ("listen", "listening", "2"):
                continue
            try:
                if int(conn.get("OwningProcess")) == engine_pid:
                    listeners.append((_port(conn.get("LocalPort")), conn.get("LocalAddress")))
            except (TypeError, ValueError):
                continue
    items = []
    for query in queries:
        processes = {}
        for process in snapshot.get("processes", []):
            if not isinstance(process, dict):
                continue
            name = str(process.get("ProcessName") or "")
            if query.casefold() in name.casefold():
                try:
                    process_id = int(process.get("Id"))
                    if process_id > 0:
                        processes[process_id] = True
                except (TypeError, ValueError):
                    pass
        evidence, external, local = [], 0, 0
        for conn in snapshot.get("connections", []):
            if not isinstance(conn, dict):
                continue
            try:
                process_id = int(conn.get("OwningProcess"))
            except (TypeError, ValueError):
                continue
            if process_id not in processes or not _established(conn.get("State")):
                continue
            remote_port = _port(conn.get("RemotePort"))
            remote = conn.get("RemoteAddress")
            if _loopback(remote):
                protocol = next((key for key, value in ports.items()
                                 if value is not None and value == remote_port), None)
                if protocol:
                    verified = any(port == remote_port and _listener_covers(address, remote)
                                   for port, address in listeners)
                    evidence.append({"pid": process_id, "protocol": protocol,
                                     "remote_port": remote_port, "state": "Established",
                                     "listener_verified": verified})
                else:
                    local += 1
            else:
                # 外部连接可能经 VPN/其他代理，不能据此称为直连。
                external += 1
        listener_verified = bool(evidence) and all(conn["listener_verified"] for conn in evidence)
        if listener_verified:
            message = "观察到连接已核验本引擎监听端口的 TCP；不能关联到登录请求。"
        elif evidence:
            message = "观察到配置端口连接，监听者未核验；不能证明连接本引擎或关联到登录请求。"
        else:
            message = "当前没有连接本工具配置端口的证据；这不等于应用没有使用代理。"
        items.append({"query": query, "status": "observed" if evidence else "no_current_evidence",
                      "process_count": len(processes), "pids": sorted(processes),
                      "listener_verified": listener_verified,
                      "tool_proxy_connections": evidence[:100],
                      "tool_proxy_connection_count": len(evidence),
                      "other_external_connections": external, "other_loopback_connections": local,
                      "message": message})
    return {"status": "observed", "items": items,
            "message": "只读的当前已建立 TCP 快照，合并同名进程；短连接、UDP/QUIC 和应用内部代理可能不在结果中。"}


def _access_evidence(engine, domain=""):
    work_dir = getattr(engine, "work_dir", "") if engine is not None else ""
    if not work_dir:
        return {"status": "unknown", "records": [], "reason": "log_unavailable",
                "message": "引擎工作目录未知，无法读取连接日志。"}
    try:
        with open(os.path.join(work_dir, "access.log"), "rb") as log:
            log.seek(0, os.SEEK_END)
            start = max(0, log.tell() - _MAX_LOG_BYTES)
            log.seek(start)
            tail = log.read(_MAX_LOG_BYTES)
        lines = tail.decode("utf-8", errors="replace").splitlines()
        if start and lines:
            lines = lines[1:]  # 首行可能是被截断的日志，保守舍弃。
    except (OSError, TypeError, ValueError):
        return {"status": "unknown", "records": [], "reason": "log_unavailable",
                "message": "连接日志缺失或不可读；不能据此判断代理是否使用。"}
    actions = {"proxy": "proxy", "misty": "proxy", "direct": "direct", "block": "block"}
    metadata = getattr(engine, "outbound_actions", None)
    if isinstance(metadata, dict):
        actions.update({tag: action for tag, action in metadata.items()
                        if isinstance(tag, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", tag)
                        and tag.lower() != "api" and action in ("proxy", "direct", "block", "unknown")})
    records = []
    # 在已受限的64KB尾部里先过滤域名，再保留最近20条匹配记录。
    for line in reversed(lines):
        match = _ACCESS.fullmatch(line)
        if not match:
            continue
        item = match.groupdict()
        # V2Ray 4.23 的日志只有 [出站标签]，HTTP CONNECT 目标写作 //host:port。
        # 旧格式不提供入站标签，保持空值，不能凭记录推断来源应用。
        item["inbound"] = item["inbound"] or ""
        if "api" in (item["inbound"].lower(), item["outbound"].lower()):
            continue
        destination = _destination(item["destination"])
        if not destination:
            continue
        host, port = destination
        if domain and host != domain and not host.endswith("." + domain):
            continue
        timestamp = item["timestamp"]
        try:
            date = dt.datetime.strptime(timestamp.split(".")[0], "%Y/%m/%d %H:%M:%S")
            age = round((_local_now() - date).total_seconds())
        except ValueError:
            continue
        try:
            ipaddress.ip_address(host)
            display_host, destination_type = "（IP目标）", "ip"
        except ValueError:
            display_host, destination_type = host, "domain"
        records.append({"timestamp": timestamp, "domain": display_host, "port": port,
                        "destination_type": destination_type,
                        "inbound": item["inbound"], "outbound": item["outbound"],
                        "action": actions.get(item["outbound"], actions.get(item["outbound"].lower(), "unknown")),
                        "source": "engine_access_log", "age_seconds": age if age >= -30 else None,
                        "time_status": "historical" if age >= -30 else "unknown"})
        if len(records) >= _MAX_LOG_LINES:
            break
    status = ("observed" if any(r["time_status"] != "unknown" for r in records) else
              "unknown" if records else "no_current_evidence")
    return {"status": status, "records": records,
            "message": "引擎最近日志中的入站与出站路由记录，不代表请求完成，也不能关联到指定进程或当前登录。"}


def _rule_evidence(config, domain):
    if not domain:
        return {"status": "not_requested", "domain": "", "action": "unknown",
                "source": "none", "matched_domain": ""}
    try:
        from routing_rules import normalize_domain, resolve_domain
        normalized = normalize_domain(domain)
        match = resolve_domain(config, normalized)
        action = match.get("action", "unknown")
        # 日志过滤只接受安全域名/IP，禁止 URL/账号等内容进入日志返回值。
        destination = _destination(str(normalized) + ":443")
        if not destination:
            return {"status": "invalid_domain", "domain": "", "action": "unknown",
                    "source": "none", "matched_domain": ""}
        return {"status": "expected" if action in ("proxy", "direct", "block") else "unknown",
                "domain": destination[0], "action": action,
                "source": match.get("source", "unknown"),
                "matched_domain": match.get("matched_domain", ""),
                "message": ("未命中明确域名规则，由引擎模板决定，实际出站尚未确认。" if action == "unknown" else
                            "配置规则的预期结果；只有流量进入引擎后才应用引擎分流。")}
    except ValueError:
        return {"status": "invalid_domain", "domain": "", "action": "unknown",
                "source": "none", "matched_domain": ""}
    except Exception:
        return {"status": "unknown", "domain": "", "action": "unknown",
                "source": "none", "matched_domain": "", "message": "无法确认域名配置匹配。"}


def build_diagnostics(config, engine, app_query="", domain="", include_connections=True):
    """返回纯 JSON 数据；可能执行最多 8 秒的只读 Windows TCP 采集。

    调用方应放到工作线程，不能据返回结果声明账号安全或风控解除。
    """
    ports = {"socks": _port(getattr(engine, "port", None)),
             "http": _port(getattr(engine, "http_port", None))}
    rule = _rule_evidence(config, domain)
    normalized_domain = rule.get("domain", "")
    snapshot = (_collect_connections() if include_connections and _queries(app_query) is not None else
                {"status": "not_requested", "reason": "collection_disabled"})
    access = (_access_evidence(engine, normalized_domain) if rule["status"] != "invalid_domain" else
              {"status": "not_requested", "records": [], "message": "域名无效，未查询连接日志。"})
    return {"generated_at": _local_now().astimezone().isoformat(timespec="seconds"),
            "engine_ports": ports, "rule_expectation": rule,
            "system_proxy": _system_evidence(_read_system_proxy(), ports, normalized_domain),
            "applications": _application_evidence(snapshot, _queries(app_query), ports, engine),
            "engine_access": access,
            "summary": "分别查看配置预期、系统代理设置、当前进程端口连接和引擎历史路由证据。",
            "limitations": ["无证据与采集失败均不能推断为未使用代理。",
                            "监听者未核验时仅证明连接配置端口；即使核验本引擎，也不证明某个登录请求走代理。",
                            "系统代理设置不能证明应用遵循；外部 TCP 连接可能经过 VPN 或其他代理。",
                            "引擎日志可能来自历史会话，记录只证明当时入站及出站选择，不证明请求完成。",
                            "本诊断不验证账号安全、IP 信誉或风控解除，也不发起网站或登录请求。"]}
