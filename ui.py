"""tkinter GUI — 状态面板 + 手动切换 + 设置 + 日志。

线程模型（重要）：tkinter 只允许主线程触碰。
本机 Python 3.13 实测：任何后台线程调用 root.after 都会抛
RuntimeError("main thread is not in main loop")，且被 try/except 吞掉后
表现为"流量永远不刷新 / 换号日志丢失 / 切换流程中途异常"。
因此所有后台线程 → UI 的通信一律走 self._post()（queue.Queue），
由主线程每 100ms 轮询消费。后台线程内严禁直接调用任何 Tk 方法。
"""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from account_pool import AccountPool
from config import Config
from switcher import Switcher
from v2ray_engine import V2RayEngine


class AppUI:
    def __init__(self, config: Config, pool: AccountPool, switcher: Switcher,
                 engine: V2RayEngine, monitor=None):
        self.config = config
        self.pool = pool
        self.switcher = switcher
        self.engine = engine
        self.monitor = monitor

        self._event_q: queue.Queue = queue.Queue()
        self._pump_job = None
        self._refresh_job = None

        self.root = tk.Tk()
        self.root.title("账号大师 Pro 2.0 — 自动换号")
        self.root.geometry(self._load_geometry())
        self._build_ui()
        self._pump_job = self.root.after(100, self._pump)
        self._schedule_refresh()

    # ---- 线程安全投递 ----
    def _post(self, fn):
        """任意线程可调：把 UI 操作排入队列，由主线程执行。"""
        self._event_q.put(fn)

    def _pump(self):
        """主线程轮询：消费事件队列。"""
        try:
            while True:
                fn = self._event_q.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        try:
            self._pump_job = self.root.after(100, self._pump)
        except tk.TclError:
            pass

    def _load_geometry(self) -> str:
        w = self.config.get("win_w") or 760
        h = self.config.get("win_h") or 560
        x = self.config.get("win_x")
        y = self.config.get("win_y")
        if x is not None and y is not None:
            return f"{w}x{h}+{x}+{y}"
        return f"{w}x{h}"

    # ---- UI 构建 ----
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)
        ttk.Label(top, text="账号大师 Pro 2.0", font=("Microsoft YaHei UI", 14, "bold")).pack(side=tk.LEFT)
        self.auto_var = tk.BooleanVar(value=bool(self.config.get("auto_switch", True)))
        ttk.Checkbutton(top, text="自动换号", variable=self.auto_var,
                        command=self._toggle_auto).pack(side=tk.RIGHT)
        ttk.Button(top, text="设置", command=self._open_settings).pack(side=tk.RIGHT, padx=6)

        # 状态卡
        status = ttk.LabelFrame(self.root, text="状态", padding=8)
        status.pack(fill=tk.X, padx=8, pady=4)
        self.lbl_active = ttk.Label(status, text="当前账号: -")
        self.lbl_active.pack(anchor=tk.W)
        self.lbl_traffic = ttk.Label(status, text="流量: -")
        self.lbl_traffic.pack(anchor=tk.W)
        self.lbl_expire = ttk.Label(status, text="有效期: -")
        self.lbl_expire.pack(anchor=tk.W)
        self.lbl_proxy = ttk.Label(status, text="代理: -")
        self.lbl_proxy.pack(anchor=tk.W)

        # 账号列表
        acc_frame = ttk.LabelFrame(self.root, text="账号库", padding=8)
        acc_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        cols = ("email", "status", "remain")
        self.tree = ttk.Treeview(acc_frame, columns=cols, show="headings", height=6)
        for c, w, t in (("email", 260, "账号"), ("status", 80, "状态"),
                        ("remain", 120, "剩余")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=tk.W)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(acc_frame, command=self.tree.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scroll.set)

        btns = ttk.Frame(self.root, padding=8)
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="立即换号", command=self._manual_switch).pack(side=tk.LEFT)
        ttk.Button(btns, text="补号", command=self._manual_topup).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="刷新状态", command=self._manual_refresh).pack(side=tk.LEFT)
        self.btn_state = ttk.Label(btns, text="")
        self.btn_state.pack(side=tk.RIGHT)

        # 选号行：下拉 + 切换
        sel = ttk.Frame(self.root, padding=(8, 0, 8, 4))
        sel.pack(fill=tk.X)
        ttk.Label(sel, text="指定账号:").pack(side=tk.LEFT)
        self.account_var = tk.StringVar()
        self.account_combo = ttk.Combobox(sel, textvariable=self.account_var,
                                          state="readonly", width=30)
        self.account_combo.pack(side=tk.LEFT, padx=6)
        ttk.Button(sel, text="切换到所选", command=self._switch_selected).pack(side=tk.LEFT)

        # 日志
        log_frame = ttk.LabelFrame(self.root, text="日志", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.log_text = tk.Text(log_frame, height=10, bg="#111", fg="#0f0",
                                font=("Consolas", 9), state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("err", foreground="#f66")
        self.log_text.tag_configure("ok", foreground="#6f6")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- 日志（任意线程可调）----
    def log(self, msg: str, tag: str = ""):
        def _do():
            try:
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {msg}\n", tag)
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
            except tk.TclError:
                pass  # 窗口已关闭
        self._post(_do)

    # ---- 自动换号开关 ----
    def _toggle_auto(self):
        self.config.set("auto_switch", self.auto_var.get())
        self.config.save()
        state = "开" if self.auto_var.get() else "关"
        self.log(f"自动换号: {state}", "ok" if self.auto_var.get() else "")

    # ---- 手动操作 ----
    def _manual_switch(self):
        if self.switcher.is_switching():
            self.log("已有切换进行中", "err")
            return

        def work():
            self._post(lambda: self.btn_state.configure(text="换号中..."))
            r = self.switcher.auto_switch("手动触发")
            if r.ok:
                self.log(f"换号成功: {r.email} → 代理 {r.proxy_ip}", "ok")
            else:
                self.log(f"换号失败: {r.error}", "err")
            self._post(lambda: self.btn_state.configure(text=""))
            self._post(self._refresh_now)
        threading.Thread(target=work, daemon=True).start()

    def _manual_topup(self):
        def work():
            self._post(lambda: self.btn_state.configure(text="补号中..."))
            n = self.pool.topup(log=self.log)
            self.log(f"补号完成: +{n}", "ok")
            self._post(lambda: self.btn_state.configure(text=""))
            self._post(self._refresh_now)
        threading.Thread(target=work, daemon=True).start()

    def _manual_refresh(self):
        """手动刷新状态 + 账号列表。"""
        self.btn_state.configure(text="刷新中...")
        self._refresh_now()
        self.root.after(1000, lambda: self.btn_state.configure(text=""))

    def _switch_selected(self):
        """切换到下拉框选中的账号。"""
        email = self.account_var.get()
        if not email:
            self.log("请先在列表中选择账号", "err")
            return
        if self.switcher.is_switching():
            self.log("已有切换进行中", "err")
            return

        def work():
            self._post(lambda: self.btn_state.configure(text="切换中..."))
            r = self.switcher.switch_to_email(email)
            if r.ok:
                self.log(f"切换成功: {email} → 代理 {r.proxy_ip}", "ok")
            else:
                self.log(f"切换失败: {r.error}", "err")
            self._post(lambda: self.btn_state.configure(text=""))
            self._post(self._refresh_now)
        threading.Thread(target=work, daemon=True).start()

    # ---- 刷新（主线程调用）----
    def _schedule_refresh(self):
        self._refresh_now()
        self._refresh_job = self.root.after(30000, self._schedule_refresh)

    def _refresh_now(self):
        try:
            active = self.pool.get_active()
            if active:
                self.lbl_active.configure(text=f"当前账号: {active['email']}")
            else:
                self.lbl_active.configure(text="当前账号: -")

            # 代理状态（有监控探测结果时显示链路详情，对齐 v1.0.4 检查时间戳）
            if not self.engine.is_running():
                proxy_txt = "代理: 未运行"
            else:
                proxy_txt = f"代理: 运行中 ({self.engine.port})"
            ll = getattr(self.monitor, "last_link", None) if self.monitor else None
            if ll:
                if self.engine.is_running():
                    state = "正常" if ll.get("ok") else "异常"
                    proxy_txt = (f"链路: {state} ({self.engine.port})"
                                 f" · 出口 {ll.get('ip') or '?'}"
                                 f" · 检查于 {ll.get('ts', '?')}")
                else:
                    proxy_txt = f"代理: 未运行 · 检查于 {ll.get('ts', '?')}"
            self.lbl_proxy.configure(text=proxy_txt)

            # 账号列表
            self.tree.delete(*self.tree.get_children())
            emails = []
            for a in self.pool.all():
                created = float(a.get("created_at") or 0)
                remain = ""
                if created > 0:
                    lifetime = float(self.config.get("account_lifetime_seconds", 86400))
                    remain_s = lifetime - (time.time() - created)
                    remain = f"{remain_s/3600:.1f}h" if remain_s > 0 else "已过期"
                self.tree.insert("", tk.END, values=(
                    a["email"], a.get("status", "?"), remain))
                # 下拉框只列可用账号（ready/active 且未过期）
                if a.get("status") in ("ready", "active") and self.pool._is_valid(a):
                    emails.append(a["email"])
            # 下拉框同步
            current = self.account_var.get()
            self.account_combo["values"] = emails
            if current not in emails and emails:
                self.account_var.set(emails[0])
            elif not emails:
                self.account_var.set("")
            # 流量 + 有效期（后台查，不卡 UI）
            self._refresh_traffic_async()
        except tk.TclError:
            pass
        except Exception:
            pass

    def _refresh_traffic_async(self):
        """后台线程查询当前账号流量/有效期，经 _post 更新状态面板。

        数据源优先级：订阅响应头（Subscription-Userinfo，服务端实时计费）
        → 兜底 /app/user 的 u/d。
        """
        def work():
            try:
                active = self.pool.get_active()
                if not active:
                    return
                from cloud_api import CloudAccount
                cloud = CloudAccount(active["email"], active.get("password", ""))
                if not cloud.login():
                    self._post(lambda: self.lbl_traffic.configure(
                        text="流量: 查询失败（登录失败）"))
                    return
                # 先试订阅头（实时），失败用 /app/user 兜底
                tr = cloud.fetch_traffic() or cloud.traffic or {}
                total = tr.get("total", 0)
                used = tr.get("upload", 0) + tr.get("download", 0)
                remain = total - used
                if total > 0:
                    txt = (f"流量: 剩余 {self._fmt_bytes(remain)}"
                           f" / 已用 {self._fmt_bytes(used)}"
                           f" / 总量 {self._fmt_bytes(total)}")
                else:
                    txt = "流量: -"
                now = time.strftime("%H:%M:%S")
                txt += f"   (刷新于 {now})"
                # 有效期（class_expire 或账号生命周期）
                if cloud.class_expire > 0:
                    left = cloud.class_expire - time.time()
                    if left > 0:
                        txt2 = (f"有效期: {left/3600:.1f} 小时 "
                                f"({time.strftime('%m-%d %H:%M', time.localtime(cloud.class_expire))})")
                    else:
                        txt2 = "有效期: 已过期"
                else:
                    txt2 = "有效期: -"
                self._post(lambda: self.lbl_traffic.configure(text=txt))
                self._post(lambda: self.lbl_expire.configure(text=txt2))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _fmt_bytes(n) -> str:
        """字节数自适应显示：B / KB / MB。"""
        try:
            n = float(n)
        except (TypeError, ValueError):
            return "-"
        if n >= 1024 * 1024:
            return f"{n/1024/1024:.1f}MB"
        if n >= 1024:
            return f"{n/1024:.1f}KB"
        return f"{int(n)}B"

    # ---- 设置对话框 ----
    def _open_settings(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("设置")
        dlg.geometry("460x460")
        dlg.transient(self.root)
        dlg.grab_set()

        fields = [
            ("自动换号阈值 (MB)", "min_traffic_mb"),
            ("有效期预警 (分钟)", "expiry_threshold_minutes"),
            ("账号生命周期 (小时)", "account_lifetime_hours"),
            ("备用账号数", "reserve_accounts"),
            ("SOCKS 端口", "proxy_port"),
        ]
        vars = {}
        row = 0
        for label, key in fields:
            ttk.Label(dlg, text=label).grid(row=row, column=0, sticky=tk.W, padx=10, pady=6)
            v = tk.StringVar()
            if key == "expiry_threshold_minutes":
                v.set(str(int(float(self.config.get("expiry_threshold_seconds", 1800.0)) / 60)))
            elif key == "account_lifetime_hours":
                v.set(str(int(float(self.config.get("account_lifetime_seconds", 86400.0)) / 3600)))
            else:
                v.set(str(self.config.get(key, "")))
            ttk.Entry(dlg, textvariable=v, width=24).grid(row=row, column=1, padx=10)
            vars[key] = v
            row += 1

        ttk.Label(dlg, text="飞书 Webhook（可选）").grid(row=row, column=0, sticky=tk.W, padx=10, pady=6)
        v = tk.StringVar(value=str(self.config.get("feishu_webhook", "")))
        ttk.Entry(dlg, textvariable=v, width=34).grid(row=row, column=1, padx=10)
        vars["feishu_webhook"] = v
        row += 1
        ttk.Label(dlg, text="飞书 AppID/Secret/OpenID（可选）").grid(
            row=row, column=0, sticky=tk.W, padx=10, pady=6)
        v = tk.StringVar(value="/".join(str(self.config.get(k, "") or "")
                                        for k in ("feishu_app_id", "feishu_app_secret", "feishu_open_id")))
        ttk.Entry(dlg, textvariable=v, width=34).grid(row=row, column=1, padx=10)
        vars["feishu_app"] = v
        row += 1

        def save():
            try:
                self.config.set("min_traffic_mb", float(vars["min_traffic_mb"].get()))
                mins = float(vars["expiry_threshold_minutes"].get())
                self.config.set("expiry_threshold_seconds", mins * 60)
                hours = float(vars["account_lifetime_hours"].get())
                if hours <= 0:
                    raise ValueError("账号生命周期必须为正数")
                self.config.set("account_lifetime_seconds", hours * 3600)
                self.config.set("reserve_accounts", max(0, int(vars["reserve_accounts"].get())))
                new_port = int(vars["proxy_port"].get())
                if not (1 <= new_port <= 65535):
                    raise ValueError("端口必须在 1-65535 之间")
                old_port = int(self.config.get("proxy_port", 0) or 0)
                self.config.set("proxy_port", new_port)
                self.config.set("feishu_webhook", vars["feishu_webhook"].get().strip())
                parts = [p.strip() for p in vars["feishu_app"].get().split("/")]
                for i, k in enumerate(("feishu_app_id", "feishu_app_secret", "feishu_open_id")):
                    self.config.set(k, parts[i] if i < len(parts) else "")
                self.config.save()
                self.log("设置已保存", "ok")
                if new_port != old_port and self.engine.is_running():
                    self._restart_engine_async(f"端口 {old_port} → {new_port}")
                dlg.destroy()
            except ValueError as e:
                self.log(f"设置格式错误: {e}", "err")

        ttk.Button(dlg, text="保存", command=save).grid(row=row, column=0, padx=10, pady=12)
        ttk.Button(dlg, text="取消", command=dlg.destroy).grid(row=row, column=1, pady=12)

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
                else:
                    self.log("代理重启失败，将在下次换号时自动恢复", "err")
                self._post(self._refresh_now)
            except Exception as e:
                self.log(f"代理重启异常: {e}", "err")
        threading.Thread(target=work, daemon=True).start()

    # ---- 关闭 ----
    def _on_close(self):
        try:
            self.config.set("win_x", self.root.winfo_x())
            self.config.set("win_y", self.root.winfo_y())
            self.config.set("win_w", self.root.winfo_width())
            self.config.set("win_h", self.root.winfo_height())
            self.config.save()
        except Exception:
            pass
        for job in (self._refresh_job, self._pump_job):
            if job:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        self.engine.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
