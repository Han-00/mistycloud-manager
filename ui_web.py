# -*- coding: utf-8 -*-
"""Web 版 GUI（pywebview + webui/index.html 单页应用）。

对外接口与 ui.AppUI 完全对齐：
    WebAppUI(config, pool, switcher, engine, monitor)
    ui.log(msg, tag="")   # 任意线程可调
    ui.run()              # 阻塞，主线程调用

线程模型（与 ui.py 同一套纪律）：
- JS → Python：pywebview 桥方法（_JsApi）一律只投递/起后台线程，
  长任务（换号/补号/查流量）绝不在桥线程里跑，避免卡死页面事件。
- Python → JS：任何线程调 log()/状态推送，先进 self._q；
  泵线程等页面 loaded 后逐条 window.evaluate_js 消费。
  （pywebview 的 evaluate_js 内部已封送到 GUI 线程，跨线程调用安全。）
- 页面未加载前的日志/状态会积压在队列里，loaded 后自动补放，不丢。

未安装 pywebview 时由 main.py 回退到 ui_glass / ui。
"""
import json
import os
import queue
import threading
import time
from contextlib import nullcontext

from account_pool import AccountPool
import autostart
from config import Config
from settings_schema import apply_settings
from switcher import Switcher
from v2ray_engine import V2RayEngine
from routing_rules import get_rule_state, normalize_domain, normalize_rules, resolve_domain

WEBUI_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "webui", "index.html")


def _webui_app(environ, start_response):
    """显式禁用桌面页面缓存，只提供前端资源，避免更新后仍加载旧界面。"""
    assets = {"/": ("index.html", "text/html"),
              "/index.html": ("index.html", "text/html"),
              "/app.css": ("app.css", "text/css"),
              "/traffic.css": ("traffic.css", "text/css"),
              "/app.js": ("app.js", "text/javascript"),
              "/traffic.js": ("traffic.js", "text/javascript")}
    asset = assets.get(environ.get("PATH_INFO", "/"))
    headers = [("Cache-Control", "no-cache, no-store, must-revalidate"),
               ("Pragma", "no-cache"), ("Expires", "0")]
    if environ.get("REQUEST_METHOD", "GET") not in ("GET", "HEAD"):
        start_response("405 Method Not Allowed", headers + [("Allow", "GET, HEAD")])
        return [b""]
    if not asset:
        start_response("404 Not Found", headers)
        return [b""]
    filename, content_type = asset
    with open(os.path.join(os.path.dirname(WEBUI_HTML), filename), "rb") as source:
        content = source.read()
    start_response("200 OK", headers + [("Content-Type", content_type + "; charset=utf-8"),
                                        ("Content-Length", str(len(content)))])
    return [b"" if environ.get("REQUEST_METHOD") == "HEAD" else content]


class _JsApi:
    """暴露给前端的桥方法（window.pywebview.api.*）。

    原则：快速返回；耗时操作全部丢后台线程，经 busy/log/state 事件反馈。
    """

    def __init__(self, ui: "WebAppUI"):
        self._ui = ui

    # ---- 状态 ----
    def get_state(self):
        return self._ui.build_state()

    def get_traffic_report(self, start=None, end=None):
        return self._ui.get_traffic_report(start, end)

    def export_traffic_report(self, start, end):
        return self._ui.export_traffic_report(start, end)

    def refresh(self):
        self._ui.push_state()
        self._ui.refresh_traffic_async()
        return True

    # ---- 操作 ----
    def switch_now(self):
        self._ui.manual_switch()
        return True

    def switch_to(self, email):
        self._ui.switch_to_email(str(email))
        return True

    def topup(self):
        self._ui.manual_topup()
        return True

    def delete_account(self, email):
        self._ui.delete_account(str(email))
        return True

    def delete_inactive(self):
        self._ui.delete_inactive()
        return True

    # ---- 开关 ----
    def set_auto(self, on):
        self._ui.set_auto(bool(on))
        return True

    def set_sysproxy(self, on):
        self._ui.set_sysproxy(bool(on))
        return True

    # ---- 设置 ----
    def get_settings(self):
        return self._ui.get_settings()

    def save_settings(self, payload):
        return self._ui.save_settings(dict(payload or {}))

    # ---- 开机自启（走注册表，不属于 config，故不经过 settings_schema）----
    def set_autostart(self, on):
        return self._ui.set_autostart(bool(on))

    # ---- 事件流（换号 / 注册 / 恢复的留痕）----
    def get_events(self, limit=100):
        return self._ui.get_events(limit)

    def clear_events(self):
        self._ui.clear_events()
        return True

    def get_routing_rules(self):
        return self._ui.get_routing_rules()

    def save_routing_rules(self, rules):
        return self._ui.save_routing_rules(rules)

    def preview_domain(self, domain):
        try:
            domain = normalize_domain(domain)
            return {"ok": True, "domain": domain,
                    **resolve_domain(self._ui.config, domain)}
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": str(e)}

    def diagnose_proxy(self, app_query="", domain=""):
        return self._ui.diagnose_proxy(app_query, domain)


class WebAppUI:
    def __init__(self, config: Config, pool: AccountPool, switcher: Switcher,
                 engine: V2RayEngine, monitor=None):
        self.config = config
        self.pool = pool
        self.switcher = switcher
        self.engine = engine
        self.monitor = monitor

        self._q: queue.Queue = queue.Queue()
        self._loaded = threading.Event()
        self._shown = threading.Event()
        self._closed = False
        self.window = None

        # 托盘（pystray 可选，与 ui_glass 一致：可用才收进托盘）
        self._tray = None
        self._tray_hint = False
        self._quit_requested = False

        # 统计卡缓存（后台查询结果，build_state 时读取）
        self._traffic = None        # (total, used) 字节
        self._traffic_err = None    # 查询失败原因
        self._expire = 0.0          # class_expire 时间戳

        self._auto_on = bool(self.config.get("auto_switch", True))
        self._sysproxy_on = bool(self.config.get("system_proxy", False))
        self._rules_pending = False
        self._rules_lock = threading.Lock()
        self._diagnosis_lock = threading.Lock()
        self._anomalies_cache = None
        self._anomalies_ts = 0.0

    # ================= Python → JS 通道 =================
    def _emit(self, js_code: str):
        """任意线程可调：把 JS 代码排入队列，由泵线程在页面上执行。"""
        if not self._closed:
            self._q.put(js_code)

    def _pump(self):
        """泵线程：等页面加载完成后消费 JS 队列。"""
        self._loaded.wait(timeout=60)
        while not self._closed:
            try:
                code = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if self.window is not None:
                    self.window.evaluate_js(code)
            except Exception:
                pass

    def log(self, msg: str, tag: str = ""):
        """任意线程可调的日志入口（main.py 挂钩用）。"""
        self._emit("window.App && App.log(%s, %s)"
                   % (json.dumps(str(msg), ensure_ascii=False),
                      json.dumps(str(tag or ""))))

    def toast(self, msg: str, ok: bool = True):
        """桌面浮窗提示（对应玻璃版的 _toast），任意线程可调。

        与 log 的分工：log 是给「回头看」的流水账，toast 是给「此刻就看见」的
        操作回执——换号/删除这类用户主动触发且会离开当前视线的动作，
        只写日志等于没反馈。
        """
        self._emit("window.App && App.toast(%s, %s)"
                   % (json.dumps(str(msg), ensure_ascii=False),
                      "true" if ok else "false"))

    def _set_busy(self, on: bool, label: str = ""):
        self._emit("window.App && App.setBusy(%s, %s)"
                   % ("true" if on else "false",
                      json.dumps(label, ensure_ascii=False)))

    def push_state(self):
        st = self.build_state()
        self._emit("window.App && App.setState(%s)"
                   % json.dumps(st, ensure_ascii=False))
        self._sync_tray(st)

    # ================= 状态快照 =================
    def build_state(self) -> dict:
        active = self.pool.get_active()
        accounts = []
        valid_count = 0
        lifetime = float(self.config.get("account_lifetime_seconds", 86400))
        now = time.time()
        for a in self.pool.all():
            created = float(a.get("created_at") or 0)
            remain_s = max(0.0, lifetime - (now - created)) if created > 0 else 0.0
            # is_usable 内部已对脏数据健壮，这里不再需要 try/except——
            # 原先兜住后会退化成"按 status 猜"，反而静默显示错误的可用性
            valid = self.pool.is_usable(a)
            if valid:
                valid_count += 1
            accounts.append({
                "email": a.get("email", "?"),
                "status": a.get("status", "?"),
                "valid": valid,
                "remain_s": remain_s,
                "remain_pct": round(remain_s / lifetime * 100, 1) if lifetime > 0 else 0,
                "created_at": created,
            })

        tr_state = None
        if self._traffic_err:
            tr_state = {"err": self._traffic_err}
        elif self._traffic:
            total, used = self._traffic
            remain = max(total - used, 0)
            if total > 0:
                tr_state = {
                    "total": total, "used": used, "remain": remain,
                    "low": remain / 1024 / 1024 < float(
                        self.config.get("min_traffic_mb", 30.0)),
                }

        ll = getattr(self.monitor, "last_link", None) if self.monitor else None
        try:
            running = bool(self.engine.is_running())
        except Exception:
            running = False

        # 热力图 + 续航预测 + 链路可用率（stats 异常不影响状态快照）
        try:
            import stats as _stats
            heat = _stats.hourly_series(7)
            avg_daily = _stats.avg_daily_bytes(3)
            uptime = _stats.availability(24)
        except Exception:
            heat, avg_daily = [], None
            uptime = {"pct": None, "ok": 0, "total": 0}
        # 只有真正拿到剩余流量时才能预测续航：查询失败时 tr_state 是
        # {"err": ...}，根本没有 remain 字段——直接索引会抛 KeyError，
        # 而它发生在 build_state 里，会让整轮状态推送中断、界面停止更新。
        fuel_days = None
        remain = (tr_state or {}).get("remain")
        if remain is not None and avg_daily:
            fuel_days = round(remain / avg_daily, 1)

        return {
            "active": active["email"] if active else None,
            "engine_running": running,
            "port": getattr(self.engine, "port", None),
            "link": ll or None,
            "traffic": tr_state,
            "traffic_heatmap": heat,
            "fuel_days": fuel_days,
            "link_uptime": uptime,
            "traffic_anomalies": self._traffic_anomalies(bool(heat)),
            "expire_ts": self._expire or 0,
            "expiry_threshold_s": float(
                self.config.get("expiry_threshold_seconds", 1800.0)),
            "auto_on": self._auto_on,
            "sysproxy_on": self._sysproxy_on,
            "valid_count": valid_count,
            "switching": self.switcher.is_switching(),
            "accounts": accounts,
        }

    def _traffic_anomalies(self, has_history):
        """30 秒缓存，异常分析失败不阻断其余状态推送。"""
        if self._anomalies_cache is not None and time.monotonic() - self._anomalies_ts < 30:
            return self._anomalies_cache
        try:
            import anomaly
            items = anomaly.detect(days=7)
            result = {"status": "ready" if has_history else "no_data",
                      "items": items[:20], "count": len(items),
                      "threshold_mb": anomaly.THRESHOLD_MB, "ratio": anomaly.RATIO}
        except Exception:
            result = {"status": "error", "items": [], "count": 0,
                      "error": "异常消耗分析暂不可用"}
        self._anomalies_cache = result
        self._anomalies_ts = time.monotonic()
        return result

    # ================= 流量历史与报告 =================
    def get_traffic_report(self, start=None, end=None):
        try:
            import stats
            from traffic_analysis import build_report
            return build_report(stats.get().snapshot(), start, end)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception:
            return {"ok": False, "error": "流量历史暂不可用，请稍后重试"}

    def export_traffic_report(self, start, end):
        """用户点击导出后由原生保存对话框选路径，内容只含聚合用量。"""
        report = self.get_traffic_report(start, end)
        if not report.get("ok"):
            return report
        if self.window is None:
            return {"ok": False, "error": "窗口尚未就绪，无法打开保存对话框"}
        try:
            import webview
            from traffic_analysis import export_report
            name = f"流量明细_{report['start']}_{report['end']}.csv"
            selected = self.window.create_file_dialog(webview.FileDialog.SAVE,
                                                      save_filename=name, file_types=("CSV 明细 (*.csv)",))
            if not selected:
                return {"ok": True, "cancelled": True}
            path = selected[0]
            # Windows 的保存对话框负责覆盖确认，按用户选定的路径写入。
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(export_report(report))
            return {"ok": True, "filename": os.path.basename(path)}
        except Exception:
            return {"ok": False, "error": "报告保存失败，请检查目标目录是否可写"}

    # ================= 分流规则与代理诊断 =================
    def get_routing_rules(self):
        return {**get_rule_state(self.config), "applying": self._rules_pending}

    def save_routing_rules(self, raw):
        try:
            rules = normalize_rules(raw)
        except (TypeError, ValueError) as e:
            return {"ok": False, "error": str(e)}
        with self._rules_lock:
            if self._rules_pending:
                return {"ok": False, "error": "分流规则正在应用，请稍后再试"}
            if self.switcher.is_switching():
                return {"ok": False, "error": "换号或恢复进行中，请完成后再保存"}
            self._rules_pending = True

        def work():
            result = {"ok": False, "error": "分流规则未应用"}
            try:
                guard = getattr(self.switcher, "runtime_update_guard", None)
                with guard() if guard else nullcontext(not self.switcher.is_switching()) as acquired:
                    if not acquired:
                        result["error"] = "换号或恢复进行中，请完成后再保存"
                    elif self._closed:
                        result["error"] = "窗口已退出，规则未应用"
                    else:
                        result = self._apply_routing_rules(rules)
            except Exception as e:
                result = {"ok": False, "error": f"规则应用异常: {e}"}
            finally:
                with self._rules_lock:
                    self._rules_pending = False
                self._emit("window.App && App.rulesSaved(%s)" % json.dumps(
                    {**result, "state": self.get_routing_rules()}, ensure_ascii=False))
                self.log(result.get("message") or result.get("error", "规则处理完成"),
                         "ok" if result["ok"] else "err")
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "pending": True}

    def _apply_routing_rules(self, rules):
        """由换号共用守卫保护；保存或应用失败恢复旧规则。"""
        # 避免窗口位置/其他开关的 save 将尚未验证的新规则提前写盘。
        with getattr(self.config, "_lock", nullcontext()):
            return self._apply_routing_rules_locked(rules)

    def _apply_routing_rules_locked(self, rules):
        old_rules = self.config.get("proxy_domain_rules", [])
        running = self.engine.is_running()
        node = self.engine.current_node
        self.config.set("proxy_domain_rules", rules)
        touched_engine = False
        try:
            if self._closed:
                raise RuntimeError("窗口已退出，取消应用规则")
            if running:
                if not node:
                    raise RuntimeError("当前节点信息缺失，请恢复代理后重试")
                touched_engine = True
                self.engine.write_config(node)
                if self._closed:
                    raise RuntimeError("窗口已退出，取消重启代理")
                if not self.engine.start() or not self.engine.wait_port(timeout=15):
                    raise RuntimeError("代理未能按新规则启动")
                if self._closed:
                    raise RuntimeError("窗口已退出，取消保存规则")
                import sysproxy
                if (self.config.get("system_proxy", False) and sysproxy.available()
                        and not sysproxy.apply_if_enabled(self.config, self.engine)):
                    raise RuntimeError("系统代理例外未能同步")
            if self.config.save() is False:
                raise OSError("配置文件无法保存")
            return {"ok": True, "applied": running,
                    "message": "分流规则已保存并应用；已有长连接需重新连接"
                    if running else "分流规则已保存，代理启动后生效"}
        except Exception as e:
            self.config.set("proxy_domain_rules", old_rules)
            recovered = not touched_engine
            if touched_engine:
                try:
                    self.engine.write_config(node)
                    recovered = self._closed or bool(self.engine.start() and self.engine.wait_port(timeout=15))
                    if recovered and not self._closed:
                        import sysproxy
                        if self.config.get("system_proxy", False) and sysproxy.available():
                            recovered = bool(sysproxy.apply_if_enabled(self.config, self.engine))
                except Exception:
                    recovered = False
            # 整个事务持有配置锁，新规则验证失败时不会被其他保存路径提前写盘。
            suffix = "；已恢复旧规则" if recovered else "；旧代理恢复失败，请检查链路"
            return {"ok": False, "error": str(e) + suffix, "recovered": recovered}

    def diagnose_proxy(self, app_query="", domain=""):
        if not isinstance(app_query, str) or len(app_query) > 160:
            return {"ok": False, "error": "应用名称过长或格式不正确"}
        if domain:
            try:
                domain = normalize_domain(domain)
            except (TypeError, ValueError) as e:
                return {"ok": False, "error": str(e)}
        if not self._diagnosis_lock.acquire(blocking=False):
            return {"ok": False, "error": "正在采集连接，请稍后再试"}
        def work():
            try:
                from proxy_diagnostics import build_diagnostics
                result = build_diagnostics(self.config, self.engine, app_query, domain)
                result["sampled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._emit("window.App && App.renderDiagnostics(%s)" % json.dumps(
                    result, ensure_ascii=False))
            except Exception:
                self._emit('window.App && App.diagnosticsFailed("诊断暂不可用，请重试")')
            finally:
                self._diagnosis_lock.release()
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "pending": True}

    # ================= 流量 / 有效期（后台查询）=================
    def refresh_traffic_async(self):
        """后台线程查当前账号流量/有效期，完成后推送状态。
        数据源优先级与 ui.py 一致：订阅响应头 → /app/user 兜底。
        """
        def work():
            try:
                active = self.pool.get_active()
                if not active:
                    self._traffic, self._expire = None, 0.0
                    self._traffic_err = None
                    self.push_state()
                    return
                from cloud_api import CloudAccount
                cloud = CloudAccount(active["email"], active.get("password", ""))
                if not cloud.login():
                    self._traffic_err = "查询失败（登录失败）"
                    self.push_state()
                    return
                tr = cloud.fetch_traffic() or cloud.traffic or {}
                total = tr.get("total", 0)
                used = tr.get("upload", 0) + tr.get("download", 0)
                try:
                    import stats
                    stats.add_traffic(active["email"], used)
                except Exception:
                    pass
                self._traffic = (total, used)
                self._traffic_err = None
                self._expire = float(cloud.class_expire or 0)
                self.push_state()
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _periodic(self):
        """每 30s 刷一次状态 + 流量（对齐 ui.py 的 _schedule_refresh）。"""
        while not self._closed:
            time.sleep(30)
            if self._closed:
                break
            self.push_state()
            self.refresh_traffic_async()

    # ================= 手动操作（后台线程）=================
    def manual_switch(self):
        if self._closed:
            return
        if self.switcher.is_switching():
            self.log("已有切换进行中", "err")
            return

        def work():
            if self._closed:
                return
            self._set_busy(True, "换号中…")
            r = self.switcher.auto_switch("手动触发")
            if r.ok:
                self.log(f"换号成功: {r.email} → 代理 {r.proxy_ip}", "ok")
                self.toast(f"换号成功 {r.email}")
            else:
                self.log(f"换号失败: {r.error}", "err")
                self.toast(f"换号失败: {r.error}", ok=False)
            self._set_busy(False)
            self.push_state()
            self.refresh_traffic_async()
        threading.Thread(target=work, daemon=True).start()

    def switch_to_email(self, email: str):
        if not email or self._closed:
            return
        if self.switcher.is_switching():
            self.log("已有切换进行中", "err")
            return

        def work():
            if self._closed:
                return
            self._set_busy(True, "切换中…")
            r = self.switcher.switch_to_email(email, reason="手动选择账号")
            if r.ok:
                self.log(f"切换成功: {email} → 代理 {r.proxy_ip}", "ok")
                self.toast(f"切换成功 {email}")
            else:
                self.log(f"切换失败: {r.error}", "err")
                self.toast(f"切换失败: {r.error}", ok=False)
            self._set_busy(False)
            self.push_state()
            self.refresh_traffic_async()
        threading.Thread(target=work, daemon=True).start()

    def manual_topup(self):
        def work():
            self._set_busy(True, "补号中…")
            n = self.pool.topup(log=self.log)
            self.log(f"补号完成: +{n}", "ok")
            self._set_busy(False)
            self.push_state()
        threading.Thread(target=work, daemon=True).start()

    def delete_account(self, email: str):
        """删除账号（在用号除外，其淘汰由换号流程接管）。"""
        if self.switcher.is_switching():
            self.log("切换进行中，请完成后再删除账号", "err")
            self.toast("切换进行中，请稍后再试", ok=False)
            return
        acc = next((a for a in self.pool.all() if a.get("email") == email), None)
        if acc is None:
            self.log(f"账号 {email} 已不在库中", "err")
            self.toast(f"账号已不在库中: {email}", ok=False)
            self.push_state()
            return
        if acc.get("status") == "active":
            self.log(f"在用号 {email} 不能直接删除，请先换号后再删", "err")
            self.toast("在用号不能删除，请先换号", ok=False)
            return
        if self.pool.remove(email):
            self.log(f"已删除账号 {email}", "ok")
            self.toast(f"已删除 {email}")
        else:
            self.log(f"删除取消：{email} 已是当前在用号或已不在库中", "err")
            self.toast("删除失败：账号已不在库中", ok=False)
        self.push_state()

    def delete_inactive(self):
        """清理所有过期 / 失效账号。"""
        if self.switcher.is_switching():
            self.log("切换进行中，请完成后再清理", "err")
            self.toast("切换进行中，请稍后再试", ok=False)
            return
        n = 0
        for a in list(self.pool.all()):
            if a.get("status") in ("expired", "banned"):
                if self.pool.remove(a.get("email", "")):
                    n += 1
        self.log(f"清理完成：删除 {n} 个失效账号", "ok" if n else "")
        # 「没得可清」是正常结果而非失败，别用红色 ✗ 吓人
        self.toast(f"已清理 {n} 个失效账号" if n else "没有可清理的失效账号")
        self.push_state()

    # ================= 开关 =================
    def set_auto(self, on: bool):
        self._auto_on = bool(on)
        self.config.set("auto_switch", self._auto_on)
        self.config.save()
        self.log(f"自动换号: {'开' if self._auto_on else '关'}",
                 "ok" if self._auto_on else "")
        self.push_state()

    def set_sysproxy(self, on: bool):
        """系统代理开关：期望状态先落 config（唯一事实源），再动注册表。
        端口未就绪时不写注册表（指过去等于全网断网），状态保留，
        由监控每轮/引擎重启后收敛生效。
        """
        if self._closed:
            return
        guard = getattr(self.switcher, "runtime_update_guard", None)
        with guard() if guard else nullcontext(True) as acquired:
            if not acquired or self._closed:
                self.log("运行时操作进行中，请完成后再切换系统代理", "err")
                self.push_state()
                return
            on = bool(on)
            self._sysproxy_on = on
            self.config.set("system_proxy", on)
            self.config.save()
            try:
                import sysproxy
                if not sysproxy.available():
                    self.log("系统代理：仅支持 Windows", "err")
                    return
                if on:
                    if not sysproxy.apply_if_enabled(self.config, self.engine, log=self.log):
                        self.log("系统代理已开启，代理端口就绪后自动生效")
                else:
                    sysproxy.disable()
                    self.log("系统代理已关闭，已恢复原代理设置", "ok")
            except Exception as e:
                self.log(f"系统代理操作异常: {e}", "err")
        self.push_state()

    def _restore_sysproxy(self):
        """退出前还原系统代理（退出即代理端口死，不还原会让用户断网）。"""
        try:
            import sysproxy
            if (sysproxy.available() and
                    (bool(self.config.get("system_proxy", False)) or sysproxy.has_backup())):
                sysproxy.disable()
                self.log("已还原系统代理设置")
        except Exception:
            pass

    # ================= 设置 =================
    def get_settings(self) -> dict:
        return {
            "min_traffic_mb": self.config.get("min_traffic_mb", 30.0),
            "expiry_threshold_minutes": int(float(
                self.config.get("expiry_threshold_seconds", 1800.0)) / 60),
            "account_lifetime_hours": int(float(
                self.config.get("account_lifetime_seconds", 86400.0)) / 3600),
            "reserve_accounts": self.config.get("reserve_accounts", 1),
            "proxy_port": self.config.get("proxy_port", ""),
            "feishu_webhook": self.config.get("feishu_webhook", "") or "",
            "feishu_app": "/".join(str(self.config.get(k, "") or "")
                                   for k in ("feishu_app_id",
                                             "feishu_app_secret",
                                             "feishu_open_id")),
            "daily_report_time": self.config.get("daily_report_time", "") or "",
            # 开机自启存在注册表里，不是 config 项。
            # 放进 get_settings 只是为了让设置页一次加载就填好所有控件；
            # 它的写路径是独立的 set_autostart()，不走 save_settings。
            "autostart": autostart.is_enabled(),
        }

    def set_autostart(self, on: bool) -> dict:
        """开机自启开关：立即生效（与自动换号/系统代理一致），不等「保存设置」。

        返回 {"ok": bool, "error": str|None}。**不在这里弹提示**——前端拿到
        失败结果后要先把开关回滚，再统一提示；后端也弹一次就会提示两遍。
        注册表可能因组策略/权限受限而写不进去，静默失败会让用户以为设成功了。
        """
        try:
            autostart.set_enabled(bool(on))
        except Exception as e:
            self.log("开机自启设置失败（注册表访问受限）", "err")
            return {"ok": False, "error": str(e)}
        self.log(f"开机自启: {'开' if on else '关'}", "ok" if on else "")
        return {"ok": True, "error": None}

    # ================= 事件流 =================
    def get_events(self, limit=100) -> list:
        """最近的事件（新的在前）。events 模块内部已吞异常，这里不会再抛。"""
        try:
            import events
            return events.recent(limit)
        except Exception:
            return []

    def clear_events(self):
        try:
            import events
            events.clear()
        except Exception:
            pass

    def save_settings(self, p: dict) -> dict:
        """校验并落盘；端口变更且引擎在跑则后台重启代理。

        校验规则集中在 settings_schema，三个 UI 共用同一套。
        """
        try:
            old_port = int(self.config.get("proxy_port", 0) or 0)
            values = apply_settings(self.config, p)
            self.config.save()
            self.log("设置已保存", "ok")
            new_port = int(values.get("proxy_port", old_port))
            if new_port != old_port and self.engine.is_running():
                self._restart_engine_async(f"端口 {old_port} → {new_port}")
            self.push_state()
            return {"ok": True}
        except (ValueError, TypeError, KeyError) as e:
            return {"ok": False, "error": str(e)}

    def _restart_engine_async(self, reason: str):
        """端口等运行参数变更后，用当前节点重启代理（后台线程）。"""
        if self._closed:
            return
        def work():
            guard = getattr(self.switcher, "runtime_update_guard", None)
            with guard() if guard else nullcontext(not self.switcher.is_switching()) as acquired:
                if not acquired:
                    self.log("运行时操作进行中，新参数将在下一次代理启动时应用", "err")
                    return
                if not self._closed:
                    restart()

        def restart():
            try:
                node = self.engine.current_node
                if not node:
                    return
                self.log(f"应用新设置（{reason}），重启代理...")
                self.engine.write_config(node)
                if self._closed:
                    return
                if self.engine.start() and self.engine.wait_port(timeout=15):
                    if self._closed:
                        return
                    ip = self.engine.check_exit_ip(timeout=10)
                    self.log(f"代理已重启（端口 {self.engine.port}）"
                             + (f"，出口 {ip}" if ip else ""), "ok")
                    try:
                        import sysproxy
                        if bool(self.config.get("system_proxy", False)):
                            sysproxy.apply_if_enabled(self.config, self.engine,
                                                      log=self.log)
                    except Exception:
                        pass
                else:
                    self.log("代理重启失败，将在下次换号时自动恢复", "err")
                self.push_state()
            except Exception as e:
                self.log(f"代理重启异常: {e}", "err")
        threading.Thread(target=work, daemon=True).start()

    # ================= 生命周期 =================
    def _on_loaded(self):
        self._loaded.set()
        # 隐藏启动消除黑框：页面渲染完成后再显示窗口
        if not self._shown.is_set():
            self._shown.set()
            try:
                if self.window is not None:
                    self.window.show()
            except Exception:
                pass
        self.push_state()
        self.refresh_traffic_async()

    def _on_closing(self):
        """窗口关闭按钮：托盘可用时收进托盘（隐藏），否则真退出。
        返回 False 阻止 pywebview 真正关闭窗口。"""
        if (not self._quit_requested
                and self._tray is not None
                and bool(self.config.get("minimize_to_tray", True))):
            try:
                self.window.hide()
            except Exception:
                pass
            if not self._tray_hint:
                self._tray_hint = True
                self.log("已收进系统托盘：双击图标恢复窗口，右键 → 退出 才是真退出")
            return False
        return True

    def _save_geometry(self):
        try:
            if self.window is None:
                return
            self.config.set("win_x", int(self.window.x))
            self.config.set("win_y", int(self.window.y))
            self.config.set("win_w", int(self.window.width))
            self.config.set("win_h", int(self.window.height))
            self.config.save()
        except Exception:
            pass

    # ================= 系统托盘（pystray，可选）=================
    def _setup_tray(self):
        try:
            import pystray
        except Exception:
            self._tray = None
            return
        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", self._tray_show, default=True),
            pystray.MenuItem("立即换号", self._tray_switch),
            pystray.MenuItem("自动换号", self._tray_toggle_auto,
                             checked=lambda *_: self._auto_on),
            pystray.MenuItem("系统代理", self._tray_toggle_sysproxy,
                             checked=lambda *_: self._sysproxy_on),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._tray_quit),
        )
        try:
            self._tray = pystray.Icon("AccountMasterPro",
                                      self._make_tray_icon("idle"),
                                      "账号大师 Pro", menu)
            threading.Thread(target=self._tray.run, daemon=True).start()
        except Exception:
            self._tray = None

    def _make_tray_icon(self, status: str = "idle", fuel=None):
        """PIL 现画托盘图标：蓝色圆角底 + 白云 + 状态角标 + 顶部油量条。
        fuel: 0.0~1.0 的流量水位（None=无数据不画条），
        >50% 蓝 / 25~50% 黄 / <25% 红。"""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([2, 2, 62, 62], radius=16, fill=(47, 111, 237, 255))
        d.ellipse([13, 24, 33, 44], fill=(255, 255, 255, 255))
        d.ellipse([26, 16, 50, 40], fill=(255, 255, 255, 255))
        d.rectangle([15, 34, 48, 44], fill=(255, 255, 255, 255))
        if fuel is not None:
            fuel = max(0.0, min(1.0, float(fuel)))
            d.rounded_rectangle([8, 5, 56, 10], radius=3,
                                fill=(255, 255, 255, 60))
            if fuel > 0.003:
                color = ((255, 255, 255) if fuel > 0.5
                         else (245, 166, 35) if fuel > 0.25
                         else (225, 75, 65))
                alpha = 235 if fuel > 0.5 else 255
                d.rounded_rectangle([8, 5, 8 + 48 * fuel, 10],
                                    radius=3, fill=color + (alpha,))
        dot = {"ok": (31, 166, 92), "err": (225, 75, 65)}.get(
            status, (148, 163, 184))
        d.ellipse([40, 40, 62, 62], fill=dot + (255,),
                  outline=(255, 255, 255, 255), width=2)
        return img

    def _update_tray_status(self, status: str, fuel=None):
        if self._tray is None:
            return
        try:
            self._tray.icon = self._make_tray_icon(status, fuel)
        except Exception:
            pass

    def _sync_tray(self, st: dict):
        """按状态快照刷新托盘：角标颜色（链路）+ 油量条（水位）+ tooltip。"""
        if self._tray is None:
            return
        try:
            if not st.get("engine_running"):
                status, link_txt = "idle", "未运行"
            else:
                ok = bool((st.get("link") or {}).get("ok"))
                status = "ok" if ok else "err"
                link_txt = "链路正常" if ok else "链路异常"
            fuel = None
            lines = ["账号大师 Pro", link_txt]
            tr = st.get("traffic")
            if tr and tr.get("total"):
                fuel = max(0.0, min(1.0, tr["remain"] / tr["total"]))
                from stats import fmt_bytes
                txt = f"剩 {fmt_bytes(tr['remain'])}"
                if st.get("fuel_days") is not None:
                    txt += f" · 还能撑 {st['fuel_days']} 天"
                lines.append(txt)
            elif tr and tr.get("err"):
                lines.append("流量查询失败")
            self._update_tray_status(status, fuel)
            self._tray.title = "\n".join(lines)
        except Exception:
            pass

    def _tray_show(self, *_):
        try:
            if self.window is not None:
                self.window.show()
                self.window.restore()
                self.window.on_top = True
                self.window.on_top = False
        except Exception:
            pass

    def _tray_switch(self, *_):
        self.manual_switch()

    def _tray_toggle_auto(self, *_):
        self.set_auto(not self._auto_on)

    def _tray_toggle_sysproxy(self, *_):
        self.set_sysproxy(not self._sysproxy_on)

    def _tray_quit(self, *_):
        self._quit_requested = True
        self._save_geometry()
        try:
            if self.window is not None:
                self.window.destroy()
        except Exception:
            pass

    def run(self):
        import webview
        api = _JsApi(self)
        self.window = webview.create_window(
            "账号大师 Pro 3.0 — 自动换号",
            _webui_app,
            width=int(self.config.get("win_w") or 1060),
            height=int(self.config.get("win_h") or 720),
            min_size=(920, 640),
            js_api=api,
            background_color="#0a0c10",
            hidden=True,   # 加载完成后再显示，避免 WebView2 首帧黑框
        )
        self.window.events.loaded += self._on_loaded
        self.window.events.closing += self._on_closing
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._periodic, daemon=True).start()
        self._setup_tray()
        # 兜底：页面万一没触发 loaded，最多 3s 强制显示窗口，避免"隐形窗口"
        def _force_show():
            time.sleep(3)
            if not self._shown.is_set() and not self._closed:
                self._shown.set()
                try:
                    self.window.show()
                except Exception:
                    pass
        threading.Thread(target=_force_show, daemon=True).start()
        # 注意：必须用 private_mode=False + 固定 storage_path。
        # 默认私密模式在退出时清理临时数据目录，新版 WebView2 SDK 下
        # 清理逻辑会抛 BrowserProcessId NoneType 并连带初始化 E_ABORT。
        import tempfile
        storage = os.path.join(tempfile.gettempdir(), "AccountMasterPro2_WebView")
        try:
            webview.start(debug=False, private_mode=False, storage_path=storage)
        finally:
            self._shutdown()

    def _shutdown(self):
        """拒绝新操作，排空已开始的引擎操作后恢复系统代理并停止引擎。"""
        self._closed = True
        if self.monitor is not None:
            self.monitor.stop()
        shutdown = getattr(self.switcher, "begin_shutdown", None)
        if shutdown:
            shutdown()
        if self.monitor is not None:
            # bootstrap 可能已通过启动检查；排空后再次停止，避免其清掉 stop 标记。
            self.monitor.stop()
        self._save_geometry()
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
        with getattr(self.config, "_lock", nullcontext()):
            self._restore_sysproxy()
            self.engine.stop()
