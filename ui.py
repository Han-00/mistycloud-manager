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
from tkinter import ttk, messagebox

from account_pool import AccountPool
from config import Config
from settings_schema import apply_settings
from switcher import Switcher
from v2ray_engine import V2RayEngine

# ---- 深色主题配色（对齐设计稿，零第三方依赖：ttk clam + 自定义样式）----
C_BG     = "#12141a"    # 窗口底色
C_PANEL  = "#1c1f27"    # 面板/卡片
C_BTN    = "#252a36"    # 次级按钮
C_TROUGH = "#232733"    # 槽/表头
C_BORDER = "#2a2e3a"    # 边框
C_TEXT   = "#e8eaed"    # 主文字
C_MUTED  = "#8a91a0"    # 次级文字
C_ACCENT = "#4da3ff"    # 主色（主按钮/进度条）
C_OK     = "#34c38f"    # 正常/在用
C_WARN   = "#f5a623"    # 预警
C_ERR    = "#f0564a"    # 异常/失效
C_LOG_BG = "#0d0f13"    # 日志区底色

STATUS_TEXT = {"active": "● 在用", "ready": "● 备用",
               "expired": "● 过期", "banned": "● 失效"}


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
        self._auto_on = bool(self.config.get("auto_switch", True))
        self._sysproxy_on = bool(self.config.get("system_proxy", False))

        self.root = tk.Tk()
        self.root.title("账号大师 Pro 2.0 — 自动换号")
        try:
            import sv_ttk
            sv_ttk.set_theme("dark")
            self._sv = True
            self._win_bg = "#1c1c1c"   # sun-valley 暗色底色
        except Exception:
            self._sv = False
            self._win_bg = C_BG
        self.root.configure(bg=self._win_bg)
        self.root.geometry(self._load_geometry())
        self._setup_style()
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

    # ---- 主题样式 ----
    def _setup_style(self):
        style = ttk.Style(self.root)
        if self._sv:
            # sun-valley 已提供全套深色样式（圆角卡片/开关/主按钮），
            # 这里只统一中文字体 + 卡片文字层次（不碰背景，避免盖掉图片元素）
            font = ("Microsoft YaHei UI", 9)
            for s in ("TLabel", "TButton", "Accent.TButton", "TCheckbutton",
                      "TEntry", "TCombobox", "TLabelframe.Label",
                      "Treeview", "Treeview.Heading"):
                style.configure(s, font=font)
            style.configure("Title.TLabel", font=("Microsoft YaHei UI", 15, "bold"))
            style.configure("Sub.TLabel", foreground=C_MUTED)
            style.configure("Card.TLabel", foreground=C_MUTED)
            style.configure("CardValue.TLabel", font=("Microsoft YaHei UI", 13, "bold"))
            style.configure("CardValueSm.TLabel", font=("Microsoft YaHei UI", 10, "bold"))
            style.configure("CardSub.TLabel", foreground=C_MUTED,
                            font=("Microsoft YaHei UI", 8))
            return
        # 降级：未安装 sv_ttk 时用 clam + 自定义深色样式
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        base_font = ("Microsoft YaHei UI", 9)
        style.configure(".", background=C_BG, foreground=C_TEXT, bordercolor=C_BORDER,
                        darkcolor=C_BORDER, lightcolor=C_PANEL, troughcolor=C_TROUGH,
                        selectbackground=C_ACCENT, selectforeground="#ffffff", font=base_font)
        style.configure("TFrame", background=C_BG)
        style.configure("Panel.TFrame", background=C_PANEL)
        style.configure("TLabel", background=C_BG, foreground=C_TEXT)
        style.configure("Title.TLabel", background=C_BG, foreground=C_TEXT,
                        font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Sub.TLabel", background=C_BG, foreground=C_MUTED)
        style.configure("Card.TLabel", background=C_PANEL, foreground=C_MUTED)
        style.configure("CardValue.TLabel", background=C_PANEL, foreground=C_TEXT,
                        font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("CardValueSm.TLabel", background=C_PANEL, foreground=C_TEXT,
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("CardSub.TLabel", background=C_PANEL, foreground=C_MUTED,
                        font=("Microsoft YaHei UI", 8))
        style.configure("TLabelframe", background=C_BG, bordercolor=C_BORDER,
                        foreground=C_MUTED)
        style.configure("TLabelframe.Label", background=C_BG, foreground=C_MUTED)
        style.configure("TButton", background=C_BTN, foreground=C_TEXT,
                        bordercolor=C_BORDER, focuscolor=C_BORDER, padding=(12, 6))
        style.map("TButton",
                  background=[("active", "#2e3442"), ("pressed", "#20242e")],
                  foreground=[("active", C_TEXT)])
        style.configure("Accent.TButton", background=C_ACCENT, foreground="#0b1220",
                        bordercolor=C_ACCENT, font=("Microsoft YaHei UI", 9, "bold"),
                        padding=(16, 7))
        style.map("Accent.TButton",
                  background=[("active", "#6cb4ff"), ("pressed", "#3b8de0")])
        style.configure("TCheckbutton", background=C_BG, foreground=C_TEXT)
        style.map("TCheckbutton", background=[("active", C_BG)])
        style.configure("TEntry", fieldbackground=C_PANEL, foreground=C_TEXT,
                        insertcolor=C_TEXT, bordercolor=C_BORDER)
        style.configure("TCombobox", fieldbackground=C_PANEL, background=C_BTN,
                        foreground=C_TEXT, arrowcolor=C_MUTED, bordercolor=C_BORDER,
                        insertcolor=C_TEXT)
        style.map("TCombobox",
                  fieldbackground=[("readonly", C_PANEL)],
                  foreground=[("readonly", C_TEXT)])
        style.configure("Treeview", background=C_PANEL, fieldbackground=C_PANEL,
                        foreground=C_TEXT, rowheight=26, bordercolor=C_BORDER)
        style.configure("Treeview.Heading", background=C_TROUGH, foreground=C_MUTED,
                        font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Treeview",
                  background=[("selected", "#27405f")],
                  foreground=[("selected", "#ffffff")])
        for name, color in (("Accent", C_ACCENT), ("Warn", C_WARN)):
            style.configure(f"{name}.Horizontal.TProgressbar", background=color,
                            troughcolor=C_TROUGH, bordercolor=C_BORDER,
                            lightcolor=color, darkcolor=color, thickness=6)

    # ---- UI 构建 ----
    def _make_card(self, parent, col: int, title: str):
        """统计卡片：小标题 + 大字数值，返回 (卡片容器, 数值 Label)。"""
        card = ttk.Frame(parent, style="Card.TFrame" if self._sv else "Panel.TFrame",
                         padding=(14, 10))
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
        ttk.Label(card, text=title, style="Card.TLabel").pack(anchor=tk.W)
        val = ttk.Label(card, text="-", style="CardValue.TLabel")
        val.pack(anchor=tk.W, pady=(2, 0))
        return card, val

    def _build_ui(self):
        # 顶部：标题 + 副标题 / 自动换号开关 + 设置
        top = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        top.pack(fill=tk.X)
        title_box = ttk.Frame(top)
        title_box.pack(side=tk.LEFT)
        ttk.Label(title_box, text="账号大师 Pro", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(title_box, text="自动换号 · 链路监控", style="Sub.TLabel").pack(
            side=tk.LEFT, padx=(10, 0), pady=(7, 0))
        self.auto_var = tk.BooleanVar(value=bool(self.config.get("auto_switch", True)))
        ttk.Button(top, text="⚙ 设置", command=self._open_settings).pack(side=tk.RIGHT)
        ttk.Checkbutton(top, text="自动换号", variable=self.auto_var,
                        style="Switch.TCheckbutton" if self._sv else "TCheckbutton",
                        command=self._toggle_auto).pack(side=tk.RIGHT, padx=(0, 8))
        self.sysproxy_var = tk.BooleanVar(value=bool(self.config.get("system_proxy", False)))
        ttk.Checkbutton(top, text="系统代理", variable=self.sysproxy_var,
                        style="Switch.TCheckbutton" if self._sv else "TCheckbutton",
                        command=self._toggle_sysproxy).pack(side=tk.RIGHT, padx=(0, 8))

        # 统计卡片行：当前账号 / 流量剩余（进度条）/ 有效期 / 代理链路
        cards = ttk.Frame(self.root)
        cards.pack(fill=tk.X, padx=10, pady=(6, 0))
        for i in range(4):
            cards.grid_columnconfigure(i, weight=1, uniform="cards")

        card, self.card_account_val = self._make_card(cards, 0, "当前账号")
        self.card_account_val.configure(style="CardValueSm.TLabel")
        self.card_account_sub = ttk.Label(card, text=" ", style="CardSub.TLabel")
        self.card_account_sub.pack(anchor=tk.W)

        card, self.card_traffic_val = self._make_card(cards, 1, "流量剩余")
        self._bar_ok = "Horizontal.TProgressbar" if self._sv else "Accent.Horizontal.TProgressbar"
        self._bar_warn = "Horizontal.TProgressbar" if self._sv else "Warn.Horizontal.TProgressbar"
        self.traffic_bar = ttk.Progressbar(card, maximum=100, value=0,
                                           style=self._bar_ok)
        self.traffic_bar.pack(fill=tk.X, pady=(6, 0))
        self.card_traffic_sub = ttk.Label(card, text=" ", style="CardSub.TLabel")
        self.card_traffic_sub.pack(anchor=tk.W, pady=(4, 0))

        card, self.card_expire_val = self._make_card(cards, 2, "有效期")
        self.card_expire_sub = ttk.Label(card, text=" ", style="CardSub.TLabel")
        self.card_expire_sub.pack(anchor=tk.W)

        card, self.card_link_val = self._make_card(cards, 3, "代理链路")
        self.card_link_sub = ttk.Label(card, text=" ", style="CardSub.TLabel")
        self.card_link_sub.pack(anchor=tk.W)

        # 账号库列表（状态彩色行：在用绿/失效过期红，选中高亮）
        acc_frame = ttk.LabelFrame(self.root, text=" 账号库 ", padding=8)
        acc_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))
        cols = ("email", "status", "remain")
        self.tree = ttk.Treeview(acc_frame, columns=cols, show="headings", height=6)
        for c, w, t in (("email", 320, "账号"), ("status", 100, "状态"),
                        ("remain", 120, "剩余")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=tk.W,
                             stretch=(c == "email"))
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(acc_frame, command=self.tree.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.tag_configure("active", foreground=C_OK, background="#182338")
        self.tree.tag_configure("ready", foreground=C_TEXT)
        self.tree.tag_configure("expired", foreground=C_ERR)
        self.tree.tag_configure("banned", foreground=C_ERR)

        # 操作按钮：立即换号为主按钮，其余次级
        btns = ttk.Frame(self.root, padding=(10, 8))
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="立即换号", style="Accent.TButton",
                   command=self._manual_switch).pack(side=tk.LEFT)
        ttk.Button(btns, text="补号", command=self._manual_topup).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btns, text="刷新状态", command=self._manual_refresh).pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="删除选中", command=self._delete_selected).pack(side=tk.LEFT)
        self.btn_state = ttk.Label(btns, text="", style="Sub.TLabel")
        self.btn_state.pack(side=tk.RIGHT)

        # 选号行：下拉 + 切换
        sel = ttk.Frame(self.root, padding=(10, 0, 10, 4))
        sel.pack(fill=tk.X)
        ttk.Label(sel, text="指定账号:", style="Sub.TLabel").pack(side=tk.LEFT)
        self.account_var = tk.StringVar()
        self.account_combo = ttk.Combobox(sel, textvariable=self.account_var,
                                          state="readonly", width=30)
        self.account_combo.pack(side=tk.LEFT, padx=8)
        ttk.Button(sel, text="切换到所选", command=self._switch_selected).pack(side=tk.LEFT)

        # 日志（终端风格深色面板）
        log_frame = ttk.LabelFrame(self.root, text=" 日志 ", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
        self.log_text = tk.Text(log_frame, height=9, bg=C_LOG_BG, fg="#c8ccd4",
                                font=("Consolas", 9), state=tk.DISABLED,
                                relief=tk.FLAT, highlightthickness=0,
                                insertbackground=C_TEXT)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("err", foreground=C_ERR)
        self.log_text.tag_configure("ok", foreground=C_OK)
        self.log_text.tag_configure("warn", foreground=C_WARN)

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

    # ---- 自动换号 / 系统代理开关 ----
    def _set_auto(self, on: bool):
        """显式状态设置（与 ui_glass 对齐）。"""
        self._auto_on = bool(on)
        try:
            self.auto_var.set(self._auto_on)
        except Exception:
            pass
        self.config.set("auto_switch", self._auto_on)
        self.config.save()
        self.log(f"自动换号: {'开' if self._auto_on else '关'}",
                 "ok" if self._auto_on else "")

    def _toggle_auto(self):
        self._set_auto(bool(self.auto_var.get()))

    def _toggle_sysproxy(self):
        self._set_sysproxy(bool(self.sysproxy_var.get()))

    def _set_sysproxy(self, on: bool):
        """系统代理开关：期望状态先落 config（唯一事实源），再动注册表。

        端口未就绪时不写注册表（指过去等于全网断网），状态保留，
        由监控每轮/引擎重启后收敛生效。
        """
        on = bool(on)
        self._sysproxy_on = on
        try:
            self.sysproxy_var.set(on)
        except Exception:
            pass
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

    def _restore_sysproxy(self):
        """退出前还原系统代理（退出即代理端口死，不还原会让用户断网）。"""
        try:
            import sysproxy
            if bool(self.config.get("system_proxy", False)):
                sysproxy.disable()
                self.log("已还原系统代理设置")
        except Exception:
            pass

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

    def _delete_selected(self):
        """删除列表中选中的账号（在用号除外，其淘汰由换号流程接管）。"""
        if self.switcher.is_switching():
            self.log("切换进行中，请完成后再删除账号", "err")
            return
        sel = self.tree.focus()
        if not sel:
            self.log("请先在列表中选中要删除的账号", "err")
            return
        vals = self.tree.item(sel, "values")
        if not vals:
            return
        email = vals[0]
        acc = next((a for a in self.pool.all() if a.get("email") == email), None)
        if acc is None:
            self.log(f"账号 {email} 已不在库中", "err")
            self._refresh_now()
            return
        if acc.get("status") == "active":
            self.log(f"在用号 {email} 不能直接删除，请先点「立即换号」切到其他账号后再删", "err")
            return
        if not messagebox.askyesno("确认删除", f"确定从账号库删除 {email}？\n此操作不可恢复"):
            return
        # 弹窗期间状态可能变化（如被自动换号升为在用），remove 内部会再原子校验
        if self.pool.remove(email):
            self.log(f"已删除账号 {email}", "ok")
        else:
            self.log(f"删除取消：{email} 已是当前在用号或已不在库中", "err")
        self._refresh_now()

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
            r = self.switcher.switch_to_email(email, reason="手动选择账号")
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
            self.card_account_val.configure(text=active["email"] if active else "-")
            self.card_account_sub.configure(
                text="状态: 在用" if active else "无在用账号")

            # 代理链路卡（有监控探测结果时显示出口详情）
            ll = getattr(self.monitor, "last_link", None) if self.monitor else None
            if not self.engine.is_running():
                self.card_link_val.configure(text="未运行", foreground=C_ERR)
                self.card_link_sub.configure(
                    text=f"检查于 {ll.get('ts', '?')}" if ll else " ")
            elif ll:
                ok = bool(ll.get("ok"))
                self.card_link_val.configure(
                    text="正常" if ok else "异常",
                    foreground=C_OK if ok else C_ERR)
                self.card_link_sub.configure(
                    text=f"出口 {ll.get('ip') or '?'} · 检查于 {ll.get('ts', '?')}")
            else:
                self.card_link_val.configure(text="运行中", foreground=C_TEXT)
                self.card_link_sub.configure(text=f"端口 {self.engine.port}")

            # 账号列表（状态彩色行）
            self.tree.delete(*self.tree.get_children())
            emails = []
            for a in self.pool.all():
                created = float(a.get("created_at") or 0)
                remain = ""
                if created > 0:
                    lifetime = float(self.config.get("account_lifetime_seconds", 86400))
                    remain_s = lifetime - (time.time() - created)
                    remain = f"{remain_s/3600:.1f}h" if remain_s > 0 else "已过期"
                status = a.get("status", "?")
                status_txt = STATUS_TEXT.get(status, f"● {status}")
                tags = (status,) if status in ("active", "ready", "expired", "banned") else ()
                self.tree.insert("", tk.END,
                                 values=(a["email"], status_txt, remain), tags=tags)
                # 下拉框只列可用账号（ready/active 且未过期）
                if status in ("ready", "active") and self.pool.is_usable(a):
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
        """后台线程查询当前账号流量/有效期，经 _post 更新统计卡片。

        数据源优先级：订阅响应头（Subscription-Userinfo，服务端实时计费）
        → 兜底 /app/user 的 u/d。
        """
        def work():
            try:
                active = self.pool.get_active()
                if not active:
                    self._post(self._reset_stat_cards)
                    return
                from cloud_api import CloudAccount
                cloud = CloudAccount(active["email"], active.get("password", ""))
                if not cloud.login():
                    self._post(lambda: self._apply_traffic(
                        None, "查询失败（登录失败）"))
                    return
                # 先试订阅头（实时），失败用 /app/user 兜底
                tr = cloud.fetch_traffic() or cloud.traffic or {}
                total = tr.get("total", 0)
                used = tr.get("upload", 0) + tr.get("download", 0)
                # 日报统计采样（与监控互补，覆盖自动换号关闭的场景）
                try:
                    import stats
                    stats.add_traffic(active["email"], used)
                except Exception:
                    pass
                self._post(lambda: self._apply_traffic((total, used), None))
                self._post(lambda: self._apply_expire(cloud.class_expire))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _reset_stat_cards(self):
        """无在用账号时把流量/有效期卡片复位。"""
        self.card_traffic_val.configure(text="-", foreground=C_TEXT)
        self.card_traffic_sub.configure(text=" ")
        self.traffic_bar.configure(value=0, style=self._bar_ok)
        self.card_expire_val.configure(text="-", foreground=C_TEXT)
        self.card_expire_sub.configure(text=" ")

    def _apply_traffic(self, data, err):
        """主线程更新流量卡。data=(total, used) 字节；err=失败原因文案。"""
        if err:
            self.card_traffic_val.configure(text="查询失败", foreground=C_ERR)
            self.card_traffic_sub.configure(text=err)
            return
        total, used = data
        if total <= 0:
            self.card_traffic_val.configure(text="-", foreground=C_TEXT)
            self.card_traffic_sub.configure(text="无流量数据")
            self.traffic_bar.configure(value=0)
            return
        remain = max(total - used, 0)
        low = remain / 1024 / 1024 < float(self.config.get("min_traffic_mb", 30.0))
        self.card_traffic_val.configure(text=self._fmt_bytes(remain),
                                        foreground=C_WARN if low else C_TEXT)
        self.traffic_bar.configure(
            value=max(0.0, min(100.0, remain / total * 100)),
            style=self._bar_warn if low else self._bar_ok)
        self.card_traffic_sub.configure(
            text=f"已用 {self._fmt_bytes(used)} · 总量 {self._fmt_bytes(total)}")

    def _apply_expire(self, class_expire):
        """主线程更新有效期卡。"""
        try:
            ce = float(class_expire or 0)
        except (TypeError, ValueError):
            ce = 0
        if ce <= 0:
            self.card_expire_val.configure(text="-", foreground=C_TEXT)
            self.card_expire_sub.configure(text="无数据")
            return
        left = ce - time.time()
        if left <= 0:
            self.card_expire_val.configure(text="已过期", foreground=C_ERR)
            self.card_expire_sub.configure(text=" ")
            return
        warn = left < float(self.config.get("expiry_threshold_seconds", 1800.0))
        self.card_expire_val.configure(text=f"{left/3600:.1f} 小时",
                                       foreground=C_WARN if warn else C_TEXT)
        self.card_expire_sub.configure(
            text=f"到期 {time.strftime('%m-%d %H:%M', time.localtime(ce))}")

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
        dlg.geometry("460x500")
        dlg.configure(bg=self._win_bg)
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
        ttk.Label(dlg, text="日报推送时间 (HH:MM，空=禁用)").grid(
            row=row, column=0, sticky=tk.W, padx=10, pady=6)
        v = tk.StringVar(value=str(self.config.get("daily_report_time", "") or ""))
        ttk.Entry(dlg, textvariable=v, width=24).grid(row=row, column=1, padx=10)
        vars["daily_report_time"] = v
        row += 1

        def save():
            try:
                # 校验规则集中在 settings_schema，三个 UI 共用同一套
                old_port = int(self.config.get("proxy_port", 0) or 0)
                values = apply_settings(
                    self.config, {k: v.get() for k, v in vars.items()})
                self.config.save()
                self.log("设置已保存", "ok")
                new_port = int(values.get("proxy_port", old_port))
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
                    # 端口可能变了：系统代理跟随新 HTTP 端口
                    try:
                        import sysproxy
                        if bool(self.config.get("system_proxy", False)):
                            sysproxy.apply_if_enabled(self.config, self.engine, log=self.log)
                    except Exception:
                        pass
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
        self._restore_sysproxy()   # 先还原系统代理，再停引擎（退出即端口死）
        self.engine.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
