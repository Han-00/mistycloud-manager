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

from account_pool import AccountPool
from config import Config
from settings_schema import apply_settings
from switcher import Switcher
from v2ray_engine import V2RayEngine

WEBUI_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "webui", "index.html")


class _JsApi:
    """暴露给前端的桥方法（window.pywebview.api.*）。

    原则：快速返回；耗时操作全部丢后台线程，经 busy/log/state 事件反馈。
    """

    def __init__(self, ui: "WebAppUI"):
        self._ui = ui

    # ---- 状态 ----
    def get_state(self):
        return self._ui.build_state()

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
            try:
                valid = bool(self.pool._is_valid(a))
            except Exception:
                valid = a.get("status") in ("active", "ready")
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

        # 热力图 + 续航预测（stats 异常不影响状态快照）
        try:
            import stats as _stats
            heat = _stats.hourly_series(7)
            avg_daily = _stats.avg_daily_bytes(3)
        except Exception:
            heat, avg_daily = [], None
        fuel_days = None
        if tr_state and avg_daily:
            fuel_days = round(tr_state["remain"] / avg_daily, 1)

        return {
            "active": active["email"] if active else None,
            "engine_running": running,
            "port": getattr(self.engine, "port", None),
            "link": ll or None,
            "traffic": tr_state,
            "traffic_heatmap": heat,
            "fuel_days": fuel_days,
            "expire_ts": self._expire or 0,
            "expiry_threshold_s": float(
                self.config.get("expiry_threshold_seconds", 1800.0)),
            "auto_on": self._auto_on,
            "sysproxy_on": self._sysproxy_on,
            "valid_count": valid_count,
            "switching": self.switcher.is_switching(),
            "accounts": accounts,
        }

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
        if self.switcher.is_switching():
            self.log("已有切换进行中", "err")
            return

        def work():
            self._set_busy(True, "换号中…")
            r = self.switcher.auto_switch("手动触发")
            if r.ok:
                self.log(f"换号成功: {r.email} → 代理 {r.proxy_ip}", "ok")
            else:
                self.log(f"换号失败: {r.error}", "err")
            self._set_busy(False)
            self.push_state()
            self.refresh_traffic_async()
        threading.Thread(target=work, daemon=True).start()

    def switch_to_email(self, email: str):
        if not email:
            return
        if self.switcher.is_switching():
            self.log("已有切换进行中", "err")
            return

        def work():
            self._set_busy(True, "切换中…")
            r = self.switcher.switch_to_email(email)
            if r.ok:
                self.log(f"切换成功: {email} → 代理 {r.proxy_ip}", "ok")
            else:
                self.log(f"切换失败: {r.error}", "err")
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
            return
        acc = next((a for a in self.pool.all() if a.get("email") == email), None)
        if acc is None:
            self.log(f"账号 {email} 已不在库中", "err")
            self.push_state()
            return
        if acc.get("status") == "active":
            self.log(f"在用号 {email} 不能直接删除，请先换号后再删", "err")
            return
        if self.pool.remove(email):
            self.log(f"已删除账号 {email}", "ok")
        else:
            self.log(f"删除取消：{email} 已是当前在用号或已不在库中", "err")
        self.push_state()

    def delete_inactive(self):
        """清理所有过期 / 失效账号。"""
        if self.switcher.is_switching():
            self.log("切换进行中，请完成后再清理", "err")
            return
        n = 0
        for a in list(self.pool.all()):
            if a.get("status") in ("expired", "banned"):
                if self.pool.remove(a.get("email", "")):
                    n += 1
        self.log(f"清理完成：删除 {n} 个失效账号", "ok" if n else "")
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
                if sysproxy.apply_if_enabled(self.config, self.engine, log=self.log):
                    pass   # apply 内已出「已指向 127.0.0.1:端口」日志
                else:
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
            if bool(self.config.get("system_proxy", False)):
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
        }

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
        def work():
            try:
                node = self.engine.current_node
                if not node:
                    return
                self.log(f"应用新设置（{reason}），重启代理...")
                self.engine.write_config(node)
                if self.engine.start() and self.engine.wait_port(timeout=15):
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
            "账号大师 Pro 2.0 — 自动换号",
            WEBUI_HTML,
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
            self._closed = True
            self._save_geometry()
            if self._tray is not None:
                try:
                    self._tray.stop()
                except Exception:
                    pass
            self._restore_sysproxy()   # 先还原系统代理，再停引擎（退出即端口死）
            self.engine.stop()
