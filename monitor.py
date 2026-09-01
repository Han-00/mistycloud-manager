"""自动换号监控 — 链路自愈 + 流量/有效期检查 + 补号。

策略（对齐 AccountMasterPro 监控 + 引擎刷新 v1.0.4）：
  1. 每 60s 链路健康检查（任何情况都做，不依赖自动换号开关）：
     v2ray 未运行/出口不通 → 先用当前节点重启自愈，仍不通且开启自动换号 → 换号
  2. 每 60s 检查当前账号：流量 < 阈值 或 有效期 < 阈值 → 换号
  3. 每 5 分钟检查备用池：不足 → 自动注册补足
  4. 每 10 分钟心跳留痕，证明监控线程存活
"""
import re
import threading
import time

from cloud_api import CloudAccount


class Monitor:
    def __init__(self, config, pool, switcher, engine=None, log=None):
        self.config = config
        self.pool = pool
        self.switcher = switcher
        self.engine = engine if engine is not None else getattr(switcher, "engine", None)
        self.log = log or (lambda msg: None)
        self.last_link: dict = {}   # {"ok": bool, "ip": str, "ts": str} 供 UI 展示
        self._fail_streak = 0
        self._report_fail_ts = 0.0    # 日报推送失败日志节流（30 分钟一次）
        self._report_hint_date = ""   # 「未配置飞书」提示每天只出一次
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        last_topup = 0.0
        tick = 0
        while not self._stop.is_set():
            try:
                tick += 1
                removed = self.pool.cleanup_expired()
                if removed:
                    self.log(f"清理过期账号: 删除 {removed} 个")
                self._check_engine()
                if self.last_link.get("ok", True) and self.config.get("auto_switch", True):
                    # 链路已死时跳过流量检查（自愈流程已接管，避免同轮重复换号）
                    self._check_switch()
                self._sysproxy_reconcile()
                self._maybe_daily_report()
                # 补号（5 分钟一次）
                if time.time() - last_topup >= 300:
                    last_topup = time.time()
                    self._topup_if_needed()
                # 心跳：每 10 分钟留痕，证明监控线程存活
                if tick % 10 == 0:
                    active = self.pool.get_active()
                    self.log(f"监控心跳: 第 {tick} 轮，在用 "
                             f"{active['email'] if active else '无'}，"
                             f"有效账号 {self.pool.count_valid()}")
            except Exception as e:
                self.log(f"监控异常: {e}")
            self._stop.wait(60)

    # ---- 链路健康检查 + 自愈 ----
    def _check_engine(self):
        """v2ray 未运行/出口不通 → 用当前节点重启；仍不通则换号（尊重开关）。"""
        if self.engine is None or self.switcher.is_switching():
            return
        ok, ip = self._probe()
        self.last_link = {"ok": ok, "ip": ip, "ts": time.strftime("%H:%M:%S")}
        if ok:
            self._fail_streak = 0
            return
        self._fail_streak += 1
        if self._fail_streak < 2:
            # 单次失败可能是瞬时抖动，下一轮确认后再动手
            self.log("链路探测失败，下一轮确认后自愈")
            return
        self._fail_streak = 0
        self.log("代理链路异常（连续 2 轮探测失败），尝试恢复...")
        node = self.engine.current_node
        if node:
            try:
                self.engine.write_config(node)
                if self.engine.start() and self.engine.wait_port(timeout=15):
                    ip2 = self.engine.check_exit_ip(timeout=8)
                    if ip2:
                        self.log(f"代理已用当前节点恢复（出口 {ip2}）")
                        self.last_link = {"ok": True, "ip": ip2,
                                          "ts": time.strftime("%H:%M:%S")}
                        return
            except Exception as e:
                self.log(f"节点恢复异常: {e}")
        # 重启无效或无节点：链路已死，换号是唯一出路（尊重自动换号开关）
        if self.config.get("auto_switch", True):
            self.log("当前节点恢复失败，尝试自动换号")
            r = self.switcher.auto_switch("代理链路异常")
            if r and not r.ok:
                self.log(f"自动换号未成功: {r.error}")
        else:
            self.log("链路仍异常且自动换号已关闭；请手动换号或开启自动换号")

    def _probe(self) -> tuple[bool, str]:
        """链路探活：进程活着 + 端口可连 + 出口 IP 可取。"""
        if not self.engine.is_running():
            return False, ""
        if not self.engine._port_open(self.engine.port):
            return False, ""
        ip = self.engine.check_exit_ip(timeout=8)
        return (bool(ip), ip)

    # ---- 检查是否需要换号 ----
    def _check_switch(self):
        acc = self.pool.get_active()
        if acc is None:
            # 无在用账号：如果有备用，直接换
            if self.pool.pick_next():
                r = self.switcher.auto_switch("当前无在用账号")
                if r and not r.ok:
                    self.log(f"自动换号未成功: {r.error}")
            return
        try:
            cloud = CloudAccount(acc["email"], acc.get("password", ""))
            # 登录拿最新流量
            if not cloud.login():
                code = cloud.login_error_code()
                if code == "bad_credentials":
                    self.log(f"当前账号失效（{acc['email']}），标记并换号")
                    self.pool.mark_banned(acc["email"])
                    r = self.switcher.auto_switch("当前账号登录失败")
                    if r and not r.ok:
                        self.log(f"自动换号未成功: {r.error}")
                elif code == "network":
                    self.log("网络错误，跳过本次检查")
                return
            # 同步服务端套餐到期时间
            if cloud.class_expire:
                self.pool.set_class_expire(acc["email"], cloud.class_expire)
            # 流量：优先 /app/user 的 u/d，缺失时兜底订阅响应头（服务端实时计费）
            traffic = cloud.traffic or cloud.fetch_traffic() or {}
            # 计算剩余
            total = traffic.get("total", 0)
            used = traffic.get("upload", 0) + traffic.get("download", 0)
            # 日报统计：按服务端累计已用的增量累加（幂等，重复采样增量为 0）
            try:
                import stats
                stats.add_traffic(acc["email"], used)
            except Exception:
                pass
            if total > 0:
                remain_mb = (total - used) / 1024 / 1024
                min_mb = float(self.config.get("min_traffic_mb", 30.0))
                if remain_mb < min_mb:
                    self.log(f"流量不足: 剩 {remain_mb:.1f}MB < {min_mb:.0f}MB，触发换号")
                    r = self.switcher.auto_switch(f"流量不足 ({remain_mb:.1f}MB)")
                    if r and not r.ok:
                        self.log(f"自动换号未成功: {r.error}")
                    return
            # 有效期：客户端生命周期 与 服务端套餐到期 取较早者
            remain_s = None
            created = float(acc.get("created_at") or 0)
            if created > 0:
                lifetime = float(self.config.get("account_lifetime_seconds", 86400))
                remain_s = lifetime - (time.time() - created)
            class_expire = float(acc.get("class_expire") or 0)
            if class_expire > 0:
                remain_ce = class_expire - time.time()
                remain_s = remain_ce if remain_s is None else min(remain_s, remain_ce)
            thr = float(self.config.get("expiry_threshold_seconds", 1800.0))
            if remain_s is not None and remain_s < thr:
                self.log(f"有效期不足: 剩 {max(0, remain_s)/60:.0f} 分钟，触发换号")
                r = self.switcher.auto_switch(f"有效期不足 ({max(0, remain_s)/60:.0f}分钟)")
                if r and not r.ok:
                    self.log(f"自动换号未成功: {r.error}")
        except Exception as e:
            self.log(f"流量检查异常: {e}")

    # ---- 补号 ----
    def _topup_if_needed(self):
        reserve = int(self.config.get("reserve_accounts", 2))
        valid = self.pool.count_valid()
        active = self.pool.get_active()
        need = reserve - valid + (1 if active else 0)
        if need > 0:
            self.log(f"备用账号不足（有效 {valid}，需 {reserve}+在用），开始补号 {need} 个...")
            added = self.pool.topup(log=self.log)
            if added > 0:
                self.log(f"补号完成: +{added} 个")
            else:
                self.log("补号失败（临时邮箱或注册接口异常），5 分钟后重试")

    # ---- 系统代理收敛 ----
    def _sysproxy_reconcile(self):
        """每轮对齐系统代理与配置开关/实际端口（兜手改注册表、换端口、外部覆盖）。"""
        if self.engine is None:
            return
        try:
            import sysproxy
            # apply_if_enabled 的日志回调带 tag 参数，适配成 self.log 的单参签名
            sysproxy.apply_if_enabled(self.config, self.engine,
                                      log=lambda m, *_a: self.log(m))
        except Exception:
            pass

    # ---- 每日运行日报 ----
    def _maybe_daily_report(self):
        """到点推送上一日运行日报（晚启动可补发；当日已推不重复；失败保留待推下轮重试）。"""
        try:
            import stats
            s = stats.get()
            s.touch()   # 日期翻转：把上一日计数固化为待推送
            raw = str(self.config.get("daily_report_time", "") or "").strip()
            if not re.match(r"^\d{1,2}:\d{2}$", raw):
                return   # 空/非法 = 禁用日报
            hh, mm = (int(x) for x in raw.split(":"))
            if hh > 23 or mm > 59:
                return
            now = time.localtime()
            if (now.tm_hour, now.tm_min) < (hh, mm):
                return   # 未到推送时间
            if s.is_reported_today():
                return   # 今天已推送
            pending = s.get_pending()
            if not pending:
                return   # 无上一日计数（如首日启动）
            if self._no_channel_hint_if_unset():
                return
            from notify import push
            if push(self._compose_report(pending)):
                s.mark_reported()
                self.log(f"日报已推送（{pending.get('date')}）")
            else:
                # 不记日期、保留 pending 下轮重试；失败日志 30 分钟节流
                if time.time() - self._report_fail_ts >= 1800:
                    self._report_fail_ts = time.time()
                    self.log("日报推送失败（飞书未配置或网络异常），稍后重试")
        except Exception as e:
            self.log(f"日报异常: {e}")

    def _no_channel_hint_if_unset(self) -> bool:
        """飞书通道未配置 → 返回 True 跳过推送，且提示每天只出一次。"""
        try:
            from notify import feishu_configured
            if feishu_configured():
                return False
        except Exception:
            return True
        today = time.strftime("%Y-%m-%d")
        if self._report_hint_date != today:
            self._report_hint_date = today
            self.log("日报跳过：未配置飞书推送（设置里填 feishu_webhook 或应用三件套）")
        return True

    def _compose_report(self, pending: dict) -> str:
        """组装日报文案：上一日换号次数 / 流量消耗 / 账号池 / 链路状态。"""
        import stats
        lines = [
            f"📊 账号大师日报（{pending.get('date', '?')}）",
            f"换号: 成功 {int(pending.get('switch_ok') or 0)} 次 / "
            f"失败 {int(pending.get('switch_fail') or 0)} 次",
            f"流量消耗: {stats.fmt_bytes(pending.get('traffic_used') or 0)}",
        ]
        active = self.pool.get_active()
        lines.append(f"账号池: 在用 {active['email'] if active else '无'} · "
                     f"有效账号 {self.pool.count_valid()}")
        link = self.last_link or {}
        if link.get("ok") and link.get("ip"):
            lines.append(f"链路: 正常（出口 {link['ip']} @ {link.get('ts', '?')}）")
        else:
            lines.append("链路: 异常（监控自愈中）")
        return "\n".join(lines)
