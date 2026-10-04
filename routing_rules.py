"""网站分流规则的共享底座；仅解析配置，不读运行数据或访问网络。"""
import ipaddress
import re
from urllib.parse import urlsplit


def normalize_domain(raw) -> str:
    """接受域名、*.子域写法或 HTTP(S) URL，返回安全的 ASCII 后缀。"""
    if not isinstance(raw, str):
        raise ValueError("域名必须是文本")
    value = raw.strip()
    if not value or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("域名不能为空或包含空白")
    if "://" in value:
        try:
            url = urlsplit(value)
            if url.scheme.lower() not in ("http", "https") or url.username or url.password:
                raise ValueError("只支持不含登录信息的 HTTP(S) URL")
            value = url.hostname or ""
            _ = url.port  # 同时拒绝非法端口
        except ValueError as exc:
            raise ValueError("URL 格式无效") from exc
    elif value.startswith("*."):
        value = value[2:]
    if value.endswith("."):
        value = value[:-1]
    try:
        value = value.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("域名编码无效") from exc
    labels = value.split(".")
    if (len(value) > 253 or len(labels) < 2
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p)
                   for p in labels)
            or not re.search(r"[a-z]", labels[-1])):
        raise ValueError("请输入有效域名（不能包含路径、端口或任意通配符）")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError("网站规则不支持 IP 地址")


def normalize_rules(raw) -> list[dict]:
    """严格校验规则列表；同一域名最后一项生效，返回可安全保存的副本。"""
    if not isinstance(raw, list):
        raise ValueError("网站规则必须是列表")
    result = {}
    for item in raw:
        if not isinstance(item, dict) or item.get("action") not in ("direct", "proxy"):
            raise ValueError("规则操作必须为 direct 或 proxy")
        domain = normalize_domain(item.get("domain"))
        result[domain] = {"domain": domain, "action": item["action"]}
    return list(result.values())


def _domains(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        try:
            domain = normalize_domain(item)
        except ValueError:
            continue
        if domain not in result:
            result.append(domain)
    return result


def builtin_domains() -> list[str]:
    # 延迟导入：config 同时通过本模块提供兼容的 get_effective_bypass 入口。
    from config import DEFAULTS
    return _domains(DEFAULTS["proxy_bypass_domains"])


def user_rules(config) -> list[dict]:
    """读取既有配置时忽略损坏项；交互保存仍应调用严格 normalize_rules。"""
    raw = config.get("proxy_domain_rules", [])
    if not isinstance(raw, list):
        return []
    valid = []
    for item in raw:
        try:
            valid.extend(normalize_rules([item]))
        except ValueError:
            continue
    return normalize_rules(valid)


def direct_domains(config) -> list[str]:
    """引擎保留全部内置与旧追加直连，由更靠前的用户规则覆盖。"""
    # 真实 Config 已合并内置；不向 FakeConfig/调用者未声明的清单追加默认值。
    return _domains(config.get("proxy_bypass_domains", []))


def _matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def effective_bypass_domains(config) -> list[str]:
    """仅返回可以安全交给系统 ProxyOverride 的域名后缀。

    系统例外不支持否定/优先级；与强制代理范围相交的整项直连必须移除，
    剩余子域仍由引擎规则直连。更具体的用户 direct 可覆盖 proxy 父域。
    """
    rules = sorted(user_rules(config), key=lambda r: (-len(r["domain"].split(".")), -len(r["domain"])))
    proxies = [r["domain"] for r in rules if r["action"] == "proxy"]
    candidates = list(dict.fromkeys(direct_domains(config) +
                     [r["domain"] for r in rules if r["action"] == "direct"]))
    result = []
    for domain in candidates:
        winner = next((r for r in rules if _matches(domain, r["domain"])), None)
        if winner and winner["action"] != "direct":
            continue
        if any(_matches(proxy, domain) for proxy in proxies):
            continue
        result.append(domain)
    return result


def resolve_domain(config, domain) -> dict:
    """返回配置层规则命中说明；未命中时交给原模板，不推断实际出口。"""
    host = normalize_domain(domain)
    rules = sorted(user_rules(config), key=lambda r: (-len(r["domain"].split(".")), -len(r["domain"])))
    for rule in rules:
        if _matches(host, rule["domain"]):
            return {"action": rule["action"], "source": "user", "matched_domain": rule["domain"]}
    builtins = builtin_domains()
    domains = sorted(direct_domains(config), key=lambda d: (-len(d.split(".")), -len(d)))
    for suffix in domains:
        if _matches(host, suffix):
            return {"action": "direct", "source": "builtin" if suffix in builtins else "legacy",
                    "matched_domain": suffix}
    return {"action": "unknown", "source": "template", "matched_domain": None}


def get_rule_state(config) -> dict:
    """适用于前端展示的配置快照，无连接状态或访问记录。"""
    builtins = builtin_domains()
    return {"rules": user_rules(config), "builtin_domains": builtins,
            "legacy_direct_domains": [d for d in direct_domains(config) if d not in builtins],
            "effective_bypass_domains": effective_bypass_domains(config)}
