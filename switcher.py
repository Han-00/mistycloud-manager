"""换号引擎 — 核心流程编排。

切换流程（每步超时 + 失败即报错）：
  选号 → 登录（验证账号）→ 拉订阅 → 解析节点
  → 写 config → 重启 v2ray → 等端口 → 验证出口 IP → 成功

失败策略（对齐 AccountMasterPro switch_guard）：
  - 登录失败(bad_credentials) → 标记 banned
  - 订阅为空（服务端套餐过期）→ 标记 expired
  - 其他失败 → 账号保留 + 进入 15 分钟冷却（不盲目删号、不连烧）
  - 切换失败且原节点可用 → 恢复原节点配置（保护现有链路）
  - 自动换号逐个尝试候选，跳过冷却中的账号，结果全程留痕
"""
import threading
import time

from account_pool import AccountPool
from cloud_api import CloudAccount
from config import Config
import events
from notify import notify_switch
from v2ray_engine import V2RayEngine

# ---- 失败冷却（进程内共享，参照 switch_guard.COOLDOWN_SECONDS）----
COOLDOWN_SECONDS = 900  # 失败账号冷却 15 分钟
_cooldown_lock = threading.Lock()
_cooldown: dict[str, float] = {}


def enter_cooldown(email: str):
    if email:
        with _cooldown_lock:
            _cooldown[email] = time.time()


def is_cooling(email: str) -> bool:
    with _cooldown_lock:
        ts = _cooldown.get(email)
    return ts is not None and (time.time() - ts) < COOLDOWN_SECONDS


def cooldown_remain(email: str) -> int:
    with _cooldown_lock:
        ts = _cooldown.get(email)
    if ts is None:
        return 0
    remain = int(COOLDOWN_SECONDS - (time.time() - ts))
    return max(0, remain)


class SwitchResult:
    def __init__(self):
        self.ok = False
        self.email = ""
        self.node = None
        self.direct_ip = ""
        self.proxy_ip = ""
        self.error = ""
        self.duration = 0.0


class Switcher:
    def __init__(self, config: Config, pool: AccountPool, engine: V2RayEngine, log=None):
        self.config = config
        self.pool = pool
        self.engine = engine
        self.log = log or (lambda msg: None)
        self._switching = False
        self._guard = threading.Lock()

    def is_switching(self) -> bool:
        return self._switching

    # ---- 对外主入口 ----
    def switch_to_email(self, email: str, notify_fail: bool = True,
                        _internal: bool = False, reason: str = "") -> SwitchResult:
        """切换到指定账号。notify_fail=False 时失败不推送飞书（自动换号逐候选尝试时用）。

        并发守卫在本方法内生效（手动切换与自动换号互斥；v1.0.7 教训：
        并发换号会互踩引擎进程）。auto_switch 内部调用走 _internal 通道。

        reason 是「为什么换」——手动触发 / 流量不足 / 代理链路异常…
        它只用于事件流留痕，不参与任何判断。
        """
        result = SwitchResult()
        result.email = email
        start = time.time()
        if not _internal:
            with self._guard:
                if self._switching:
                    result.error = "已有切换进行中"
                    return result
                self._switching = True
        try:
            self._do_switch(email, result)
        except Exception as e:
            result.error = f"切换异常: {e}"
            enter_cooldown(email)
        finally:
            result.duration = time.time() - start
            if not _internal:
                self._switching = False
        # 日报统计：手动+自动、逐候选每次都计数（并发守卫早退走不到这里）
        try:
            from stats import record_switch
            record_switch(result.ok)
        except Exception:
            pass
        # 事件流留痕：与日报计数同步。早退的「已有切换进行中」同样不记——
        # 那不是一次真实尝试，记进去只会污染成功率。
        events.record("switch", result.ok, email=email, reason=reason,
                      error=result.error, proxy_ip=result.proxy_ip,
                      direct_ip=result.direct_ip, duration=result.duration)
        # 通知统一在出口处理：成功必通知；失败仅在调用方要求时通知
        # （自动换号逐候选静默，由 auto_switch 汇总后通知一次）
        if result.ok:
            notify_switch(email, True, result.direct_ip, result.proxy_ip)
        elif notify_fail:
            notify_switch(email, False, error=result.error)
        return result

    def _do_switch(self, email: str, result: SwitchResult):
        """切换主体流程，结果写入 result（失败时 error 非空且 ok=False）。"""
        acc = None
        for a in self.pool.all():
            if a.get("email") == email:
                acc = a
                break
        if acc is None:
            result.error = "账号不在库中"
            return

        cloud = CloudAccount(acc["email"], acc.get("password", ""))
        self.log(f"登录验证: {email}")
        if not cloud.login():
            code = cloud.login_error_code()
            if code == "bad_credentials":
                result.error = "登录失败（账号已失效）"
                self.pool.mark_banned(email)
            elif code == "network":
                result.error = "登录失败（网络错误，稍后重试）"
            else:
                result.error = f"登录失败（{code}）"
            enter_cooldown(email)
            return
        self.log("登录成功")
        # 记录服务端套餐到期时间（有效期判断/过期清理用）
        self.pool.set_class_expire(email, cloud.class_expire)

        self.log("拉取订阅...")
        if not cloud.fetch_subscription():
            result.error = cloud.sub_error or "订阅拉取失败"
            if "为空" in result.error or "订阅地址" in result.error:
                # 订阅空 = 服务端套餐已过期，标记后不再反复选中
                self.pool.mark_expired(email)
                self.log(f"账号 {email} 服务端套餐已过期，标记为 expired")
            enter_cooldown(email)
            return
        node = cloud.node
        if not node or not node.get("id"):
            result.error = "订阅无有效节点"
            enter_cooldown(email)
            return
        self.log(f"节点: {node.get('remarks') or node['address']} ({node['id'][:8]}...)")

        # 保护现有链路：记住切换前的好节点
        prev_node = self.engine.current_node

        self.log("写入 v2ray 配置并重启...")
        self.engine.write_config(node)
        if not self.engine.start():
            result.error = "v2ray 启动失败"
            self._restore_link(prev_node)
            enter_cooldown(email)
            return
        if not self.engine.wait_port(timeout=15):
            result.error = "代理端口 15 秒未就绪"
            self._restore_link(prev_node)
            enter_cooldown(email)
            return
        self.log("代理端口就绪")

        # 验证出口
        direct = self.engine.check_direct_ip()
        proxy = self.engine.check_exit_ip()
        result.direct_ip = direct
        result.proxy_ip = proxy
        if not proxy:
            result.error = "代理出口 IP 获取失败（链路不通）"
            self._restore_link(prev_node)
            enter_cooldown(email)
            return
        if direct and proxy and direct == proxy:
            self.log(f"警告: 直连与代理出口一致 ({direct})，代理可能未生效")

        result.node = node
        result.ok = True
        retired = self.pool.set_active(email)
        msg = f"换号成功: {email}（直连 {direct or '?'} → 代理 {proxy}）"
        if retired:
            msg += f"，已淘汰旧号 {retired}"
        self.log(msg)

    def _restore_link(self, prev_node):
        """切换失败后用原节点恢复代理（不放弃现有链路）。"""
        if not prev_node:
            return
        try:
            self.log("恢复原节点配置，保护现有链路...")
            self.engine.write_config(prev_node)
            if self.engine.start() and self.engine.wait_port(timeout=15):
                self.log("已恢复原节点代理")
                events.record("restore", True, reason="换号失败后回退原节点")
            else:
                self.log("原节点恢复失败（下次换号或监控会重试）")
                events.record("restore", False, reason="换号失败后回退原节点",
                              error="原节点恢复失败")
        except Exception as e:
            self.log(f"恢复原节点异常: {e}")
            events.record("restore", False, reason="换号失败后回退原节点",
                          error=f"恢复异常: {e}")

    # ---- 自动换号：失败换下一个 ----
    def auto_switch(self, reason: str = "") -> SwitchResult:
        """自动换号：选备用账号 → 切换 → 失败换下一个（冷却中的跳过）。"""
        r = SwitchResult()
        with self._guard:
            if self._switching:
                r.error = "已有切换进行中"
                return r
            self._switching = True
        try:
            current = self.pool.get_active()
            exclude = current["email"] if current else ""
            why = reason or "流量/有效期不足"
            self.log(f"触发自动换号: {why}")

            tried: list[str] = []
            last: SwitchResult = r
            max_tries = max(3, int(self.config.get("reserve_accounts", 2)) + 1)
            for _ in range(max_tries):
                acc = self.pool.pick_next(exclude_email=exclude, skip_emails=tried)
                if acc is None:
                    if not tried:
                        cooling = [a["email"] for a in self.pool.all()
                                   if a.get("email") != exclude and is_cooling(a["email"])]
                        if cooling:
                            r.error = "候选账号均在失败冷却期（15 分钟后自动重试）"
                        else:
                            r.error = "备用账号不足（请检查账号库）"
                        self.log(f"自动换号中止: {r.error}")
                        # 一个候选都没试过就中止，没有任何 switch 事件留痕——
                        # 而这恰恰是「自动换号为什么不工作」最常见的原因
                        events.record("abort", False, reason=why, error=r.error)
                    else:
                        self.log("无更多候选账号，结束本轮换号")
                    return last if tried else r
                tried.append(acc["email"])
                if is_cooling(acc["email"]):
                    self.log(f"账号 {acc['email']} 处于失败冷却期（剩 "
                             f"{cooldown_remain(acc['email'])}s），跳过")
                    continue
                self.log(f"尝试候选: {acc['email']}")
                last = self.switch_to_email(acc["email"], notify_fail=False,
                                            _internal=True, reason=why)
                if last.ok:
                    return last
                self.log(f"候选失败: {acc['email']} — {last.error}，换下一个")
            self.log(f"自动换号失败: 已尝试 {len(tried)} 个候选，最后错误: {last.error}")
            notify_switch(last.email or "（无候选）", False, error=last.error)
            return last
        finally:
            self._switching = False
