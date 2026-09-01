# -*- coding: utf-8 -*-
"""液态玻璃风格 GUI（customtkinter 版）。

极光渐变底（Canvas + PIL 预混色模拟毛玻璃，无真模糊）+ 大圆角玻璃卡片。
三主题可切换（浅色云雾 / 深海蓝 / 暮光紫），设置记忆于 settings.json。
系统托盘（pystray，可选）：图标随链路状态变色，右键快速操作，双击恢复窗口。
业务逻辑/线程模型与 ui.AppUI 完全一致：后台线程一律走 _post() 队列，
主线程 100ms 轮询消费，严禁后台线程直接碰 Tk。
未安装 customtkinter 时由 main.py 回退到 ui.AppUI。
"""
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from account_pool import AccountPool
from config import Config
from switcher import Switcher
from v2ray_engine import V2RayEngine

# ---- 主题库（液态玻璃三配色）----
THEMES = {
    "浅色云雾": {
        "mode": "light",
        "base": "#e9f1fa", "card": "#f7fafe", "card_hi": "#ffffff",
        "card_border": "#c8d8ee", "accent": "#2f6fed", "accent_hi": "#4a84ff",
        "accent_cyan": "#18a4e8", "ok": "#1fa65c", "warn": "#dd8b00",
        "err": "#e14b41", "text": "#243247", "muted": "#64778f",
        "log_bg": "#f0f6fd", "log_text": "#37475c", "row_sel": "#dbe8fb",
        "secondary": "#ffffff", "secondary_hi": "#eef4fc",
        "field_bg": "#ffffff", "track_bg": "#dde8f5",
        "bg_base": (233, 241, 250),
        "blobs": [
            (-0.08, -0.18, 0.55, (140, 190, 255), 150),  # 左上 天蓝
            (0.95, -0.12, 0.48, (150, 228, 195), 110),   # 右上 薄荷
            (0.42, 1.12, 0.62, (185, 205, 255), 100),    # 底部 淡紫蓝
            (1.08, 0.95, 0.42, (255, 205, 228), 70),     # 右下 淡粉
        ],
    },
    "深海蓝": {
        "mode": "dark",
        "base": "#081737", "card": "#12295f", "card_hi": "#183272",
        "card_border": "#2f4f9e", "accent": "#3b82f6", "accent_hi": "#5b9bff",
        "accent_cyan": "#4fc3ff", "ok": "#34d399", "warn": "#f5a623",
        "err": "#f87171", "text": "#eef4ff", "muted": "#93a9d6",
        "log_bg": "#0a1a40", "log_text": "#c9d6f2", "row_sel": "#1d3c85",
        "secondary": "#12295f", "secondary_hi": "#1a3474",
        "field_bg": "#0e2150", "track_bg": "#0a1a40",
        "bg_base": (8, 20, 54),
        "blobs": [
            (-0.08, -0.18, 0.55, (30, 90, 220), 190),    # 左上 深蓝
            (0.95, -0.12, 0.48, (40, 170, 235), 150),    # 右上 亮青
            (0.42, 1.12, 0.62, (90, 60, 220), 120),      # 底部 紫
            (1.08, 0.95, 0.42, (20, 120, 200), 90),      # 右下 湖蓝
        ],
    },
    "暮光紫": {
        "mode": "dark",
        "base": "#170f2e", "card": "#251a4a", "card_hi": "#2f2259",
        "card_border": "#54408a", "accent": "#8b5cf6", "accent_hi": "#9d74f8",
        "accent_cyan": "#c084fc", "ok": "#34d399", "warn": "#f5a623",
        "err": "#f87171", "text": "#f3efff", "muted": "#a99ad1",
        "log_bg": "#120c26", "log_text": "#cfc4ec", "row_sel": "#3a2a66",
        "secondary": "#251a4a", "secondary_hi": "#302259",
        "field_bg": "#1e1540", "track_bg": "#120c26",
        "bg_base": (22, 13, 44),
        "blobs": [
            (-0.08, -0.18, 0.55, (124, 58, 237), 170),   # 左上 紫
            (0.95, -0.12, 0.48, (217, 70, 239), 130),    # 右上 品红
            (0.42, 1.12, 0.62, (88, 80, 210), 120),      # 底部 蓝紫
            (1.08, 0.95, 0.42, (244, 114, 182), 80),     # 右下 粉
        ],
    },
}
DEFAULT_THEME = "浅色云雾"

# 当前生效配色（模块级，widget 构建时读取；切主题后重建 UI 生效）
BASE = CARD = CARD_HI = CARD_BORDER = ACCENT = ACCENT_HI = ACCENT_CYAN = ""
OK = WARN = ERR = TEXT = MUTED = LOG_BG = LOG_TEXT = ROW_SEL = ""
SECONDARY = SECONDARY_HI = FIELD_BG = TRACK_BG = ""
AP_MODE = "light"
BG_BASE = (233, 241, 250)
BLOBS = []

STATUS_TEXT = {"active": "● 在用", "ready": "● 备用",
               "expired": "● 过期", "banned": "● 失效"}
STATUS_COLOR = {}

FONT = "Microsoft YaHei UI"
AUTOSTART_NAME = "AccountMasterPro2"


def _load_palette(name: str):
    """把主题写入模块级配色常量。"""
    global BASE, CARD, CARD_HI, CARD_BORDER, ACCENT, ACCENT_HI, ACCENT_CYAN
    global OK, WARN, ERR, TEXT, MUTED, LOG_BG, LOG_TEXT, ROW_SEL
    global SECONDARY, SECONDARY_HI, FIELD_BG, TRACK_BG
    global AP_MODE, BG_BASE, BLOBS, STATUS_COLOR
    t = THEMES.get(name) or THEMES[DEFAULT_THEME]
    BASE, CARD, CARD_HI, CARD_BORDER = t["base"], t["card"], t["card_hi"], t["card_border"]
    ACCENT, ACCENT_HI, ACCENT_CYAN = t["accent"], t["accent_hi"], t["accent_cyan"]
    OK, WARN, ERR = t["ok"], t["warn"], t["err"]
    TEXT, MUTED = t["text"], t["muted"]
    LOG_BG, LOG_TEXT, ROW_SEL = t["log_bg"], t["log_text"], t["row_sel"]
    SECONDARY, SECONDARY_HI = t["secondary"], t["secondary_hi"]
    FIELD_BG, TRACK_BG = t["field_bg"], t["track_bg"]
    AP_MODE, BG_BASE, BLOBS = t["mode"], t["bg_base"], t["blobs"]
    STATUS_COLOR = {"active": OK, "ready": ACCENT_CYAN,
                    "expired": ERR, "banned": ERR}


class _RowsShim:
    """给测试用的最小 Treeview 兼容层（列表行数查询）。"""

    def __init__(self, ui):
        self._ui = ui

    def get_children(self):
        return list(self._ui._row_widgets)


class GlassAppUI:
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
        self._selected_email = ""
        self._row_widgets = []
        self._row_frames = []
        self._bg_photo = None
        self._bg_size = (0, 0)
        self._bg_job = None
        self._tray = None
        self._tray_hint = False
        self._toast_win = None
        self._auto_on = bool(self.config.get("auto_switch", True))
        self._sysproxy_on = bool(self.config.get("system_proxy", False))

        self.theme = self.config.get("ui_theme") or DEFAULT_THEME
        if self.theme not in THEMES:
            self.theme = DEFAULT_THEME
        _load_palette(self.theme)

        ctk.set_appearance_mode(AP_MODE)
        self.root = ctk.CTk()
        self.root.title("账号大师 Pro 2.0 — 自动换号")
        self.root.configure(fg_color=BASE)
        self.root.geometry(self._load_geometry())
        self._build_ui()
        self._setup_tray()
        self._pump_job = self.root.after(100, self._pump)
        self._schedule_refresh()

    # ---- 线程安全投递 ----
    def _post(self, fn):
        self._event_q.put(fn)

    def _pump(self):
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
        w = self.config.get("win_w") or 860
        h = self.config.get("win_h") or 620
        x = self.config.get("win_x")
        y = self.config.get("win_y")
        if x is not None and y is not None:
            return f"{w}x{h}+{x}+{y}"
        return f"{w}x{h}"

    # ---- 主题切换 ----
    def _apply_theme(self, name: str, save: bool = True):
        if name not in THEMES:
            name = DEFAULT_THEME
        if name == self.theme:
            return
        # 保留日志内容
        old_log = ""
        try:
            old_log = self.log_text.get("1.0", "end").rstrip("\n")
        except Exception:
            pass
        self.theme = name
        _load_palette(name)
        ctk.set_appearance_mode(AP_MODE)
        for w in self.root.winfo_children():
            w.destroy()
        self._bg_photo = None
        self._bg_size = (0, 0)
        self.root.configure(fg_color=BASE)
        self._build_ui()
        if old_log:
            try:
                self.log_text.configure(state="normal")
                self.log_text.insert("end", old_log + "\n")
                self.log_text.configure(state="disabled")
            except tk.TclError:
                pass
        try:
            self._render_bg(self.root.winfo_width(), self.root.winfo_height())
        except Exception:
            pass
        self._refresh_now()
        if save:
            self.config.set("ui_theme", name)
            self.config.save()
        self.log(f"主题已切换: {name}", "ok")

    # ---- 极光渐变背景（PIL 预混色，1/3 分辨率渲染后放大，便宜）----
    def _render_bg(self, w: int, h: int):
        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageTk
            qw, qh = max(4, w // 3), max(4, h // 3)
            img = Image.new("RGB", (qw, qh), BG_BASE)
            for cx, cy, r, (rr, gg, bb), alpha in BLOBS:
                mask = Image.new("L", (qw, qh), 0)
                d = ImageDraw.Draw(mask)
                x0, y0 = int((cx - r) * qw), int((cy - r) * qh)
                x1, y1 = int((cx + r) * qw), int((cy + r) * qh)
                d.ellipse([x0, y0, x1, y1], fill=255)
                mask = mask.filter(ImageFilter.GaussianBlur(min(qw, qh) * 0.22))
                layer = Image.new("RGB", (qw, qh), (rr, gg, bb))
                img = Image.composite(layer, img, mask.point(lambda v: v * alpha // 255))
            img = img.resize((w, h), Image.BICUBIC)
            self._bg_photo = ImageTk.PhotoImage(img)
            self._bg_canvas.itemconfig("bg", image=self._bg_photo)
        except Exception:
            pass  # PIL 不可用就纯底色

    def _on_resize(self, _event=None):
        w, h = self.root.winfo_width(), self.root.winfo_height()
        if w < 50 or h < 50 or (w, h) == self._bg_size:
            return
        self._bg_size = (w, h)
        if self._bg_job:
            self.root.after_cancel(self._bg_job)
        self._bg_job = self.root.after(120, lambda: self._render_bg(*self._bg_size))

    # ---- UI 构建 ----
    def _glass(self, parent, **kw):
        kw.setdefault("corner_radius", 16)
        kw.setdefault("fg_color", CARD)
        kw.setdefault("border_width", 1)
        kw.setdefault("border_color", CARD_BORDER)
        return ctk.CTkFrame(parent, **kw)

    def _make_card(self, parent, col: int, title: str):
        card = self._glass(parent)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 10, 0))
        ctk.CTkLabel(card, text=title, text_color=MUTED,
                     font=(FONT, 11)).pack(anchor="w", padx=16, pady=(12, 0))
        val = ctk.CTkLabel(card, text="-", text_color=TEXT, font=(FONT, 19, "bold"))
        val.pack(anchor="w", padx=16, pady=(2, 0))
        return card, val

    def _build_ui(self):
        # 底层：极光渐变画布
        self._bg_canvas = tk.Canvas(self.root, highlightthickness=0, bd=0, bg=BASE)
        self._bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._bg_canvas.create_image(0, 0, tags="bg", anchor="nw")
        self.root.bind("<Configure>", self._on_resize)

        # 内容层（透明，叠在画布上）
        content = ctk.CTkFrame(self.root, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=14, pady=10)

        # 顶栏
        top = ctk.CTkFrame(content, fg_color="transparent")
        top.pack(fill="x")
        title_box = ctk.CTkFrame(top, fg_color="transparent")
        title_box.pack(side="left")
        ctk.CTkLabel(title_box, text="账号大师 Pro", text_color=TEXT,
                     font=(FONT, 21, "bold")).pack(side="left")
        ctk.CTkLabel(title_box, text="自动换号 · 链路监控", text_color=MUTED,
                     font=(FONT, 11)).pack(side="left", padx=(12, 0), pady=(10, 0))
        self.auto_var = tk.BooleanVar(value=self._auto_on)
        ctk.CTkButton(top, text="⚙ 设置", width=84, height=32, corner_radius=10,
                      fg_color=SECONDARY, hover_color=SECONDARY_HI, text_color=TEXT,
                      border_width=1, border_color=CARD_BORDER,
                      command=self._open_settings).pack(side="right")
        ctk.CTkSwitch(top, text="自动换号", variable=self.auto_var,
                      progress_color=ACCENT, fg_color=TRACK_BG,
                      text_color=TEXT, font=(FONT, 12),
                      command=self._toggle_auto).pack(side="right", padx=(0, 14))
        self.sysproxy_var = tk.BooleanVar(value=self._sysproxy_on)
        ctk.CTkSwitch(top, text="系统代理", variable=self.sysproxy_var,
                      progress_color=ACCENT, fg_color=TRACK_BG,
                      text_color=TEXT, font=(FONT, 12),
                      command=self._toggle_sysproxy).pack(side="right", padx=(0, 14))

        # 统计卡片
        cards = ctk.CTkFrame(content, fg_color="transparent")
        cards.pack(fill="x", pady=(12, 0))
        for i in range(4):
            cards.grid_columnconfigure(i, weight=1, uniform="cards")

        card, self.card_account_val = self._make_card(cards, 0, "当前账号")
        self.card_account_val.configure(font=(FONT, 13, "bold"))
        self.card_account_sub = ctk.CTkLabel(card, text=" ", text_color=MUTED,
                                             font=(FONT, 10))
        self.card_account_sub.pack(anchor="w", padx=16, pady=(0, 12))

        card, self.card_traffic_val = self._make_card(cards, 1, "流量剩余")
        self.traffic_bar = ctk.CTkProgressBar(card, height=8, corner_radius=99,
                                              progress_color=ACCENT_CYAN,
                                              fg_color=TRACK_BG)
        self.traffic_bar.set(0)
        self.traffic_bar.pack(fill="x", padx=16, pady=(8, 0))
        self.card_traffic_sub = ctk.CTkLabel(card, text=" ", text_color=MUTED,
                                             font=(FONT, 10))
        self.card_traffic_sub.pack(anchor="w", padx=16, pady=(4, 12))

        card, self.card_expire_val = self._make_card(cards, 2, "有效期")
        self.card_expire_sub = ctk.CTkLabel(card, text=" ", text_color=MUTED,
                                            font=(FONT, 10))
        self.card_expire_sub.pack(anchor="w", padx=16, pady=(0, 12))

        card, self.card_link_val = self._make_card(cards, 3, "代理链路")
        self.card_link_sub = ctk.CTkLabel(card, text=" ", text_color=MUTED,
                                          font=(FONT, 10))
        self.card_link_sub.pack(anchor="w", padx=16, pady=(0, 12))

        # 账号库（玻璃面板 + 可点选行）
        acc_panel = self._glass(content)
        acc_panel.pack(fill="both", expand=True, pady=(12, 0))
        ctk.CTkLabel(acc_panel, text="账号库（右键更多操作）", text_color=MUTED,
                     font=(FONT, 11, "bold")).pack(anchor="w", padx=16, pady=(10, 4))
        self._rows_box = ctk.CTkFrame(acc_panel, fg_color="transparent")
        self._rows_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tree = _RowsShim(self)

        # 操作按钮
        btns = ctk.CTkFrame(content, fg_color="transparent")
        btns.pack(fill="x", pady=(12, 0))
        ctk.CTkButton(btns, text="立即换号", width=110, height=36, corner_radius=12,
                      fg_color=ACCENT, hover_color=ACCENT_HI,
                      font=(FONT, 12, "bold"),
                      command=self._manual_switch).pack(side="left")
        for text, cmd in (("补号", self._manual_topup),
                          ("刷新状态", self._manual_refresh),
                          ("删除选中", self._delete_selected)):
            ctk.CTkButton(btns, text=text, width=84, height=36, corner_radius=12,
                          fg_color=SECONDARY, hover_color=SECONDARY_HI, text_color=TEXT,
                          border_width=1, border_color=CARD_BORDER,
                          command=cmd).pack(side="left", padx=(10, 0))
        self.btn_state = ctk.CTkLabel(btns, text="", text_color=MUTED, font=(FONT, 11))
        self.btn_state.pack(side="right")

        # 指定账号
        sel = ctk.CTkFrame(content, fg_color="transparent")
        sel.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(sel, text="指定账号:", text_color=MUTED,
                     font=(FONT, 11)).pack(side="left")
        self.account_var = tk.StringVar(value="-")
        self.account_combo = ctk.CTkOptionMenu(
            sel, variable=self.account_var, values=["-"], width=280, height=32,
            corner_radius=10, fg_color=FIELD_BG, button_color=ACCENT,
            button_hover_color=ACCENT_HI, text_color=TEXT,
            dropdown_fg_color=FIELD_BG, dropdown_hover_color=ROW_SEL,
            dropdown_text_color=TEXT)
        self.account_combo.pack(side="left", padx=(10, 10))
        ctk.CTkButton(sel, text="切换到所选", width=100, height=32, corner_radius=10,
                      fg_color=SECONDARY, hover_color=SECONDARY_HI, text_color=TEXT,
                      border_width=1, border_color=CARD_BORDER,
                      command=self._switch_selected).pack(side="left")

        # 日志（终端风格玻璃面板）
        log_panel = self._glass(content)
        log_panel.pack(fill="both", expand=True, pady=(12, 0))
        log_hdr = ctk.CTkFrame(log_panel, fg_color="transparent")
        log_hdr.pack(fill="x", padx=16, pady=(10, 2))
        ctk.CTkLabel(log_hdr, text="日志", text_color=MUTED,
                     font=(FONT, 11, "bold")).pack(side="left")
        ctk.CTkButton(log_hdr, text="清空", width=52, height=22, corner_radius=8,
                      fg_color="transparent", hover_color=SECONDARY_HI,
                      text_color=MUTED, border_width=1, border_color=CARD_BORDER,
                      font=(FONT, 10), command=self._clear_log).pack(side="right")
        self.log_text = ctk.CTkTextbox(log_panel, fg_color=LOG_BG, corner_radius=12,
                                       border_width=0, text_color=LOG_TEXT,
                                       font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_text.configure(state="disabled")
        self.log_text.tag_config("err", foreground=ERR)
        self.log_text.tag_config("ok", foreground=OK)
        self.log_text.tag_config("warn", foreground=WARN)

        # 快捷键
        self.root.bind("<F5>", lambda _e: self._manual_refresh())
        self.root.bind("<Control-t>", lambda _e: self._manual_switch())
        self.root.bind("<Control-T>", lambda _e: self._manual_switch())

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- 日志（任意线程可调）----
    def log(self, msg: str, tag: str = ""):
        def _do():
            try:
                self.log_text.configure(state="normal")
                self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n", tag)
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
            except tk.TclError:
                pass
        self._post(_do)

    def _clear_log(self):
        try:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass

    # ---- 桌面浮窗提示（非阻塞）----
    def _toast(self, msg: str, ok: bool = True):
        def _do():
            try:
                if self._toast_win is not None:
                    try:
                        self._toast_win.destroy()
                    except Exception:
                        pass
                win = ctk.CTkToplevel(self.root)
                win.overrideredirect(True)
                try:
                    win.attributes("-alpha", 0.95)
                except tk.TclError:
                    pass
                win.configure(fg_color="#101828")
                ctk.CTkLabel(win, text=("✓ " if ok else "✗ ") + msg,
                             text_color="#ffffff", font=(FONT, 12),
                             padx=18, pady=12).pack()
                win.update_idletasks()
                sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
                w, h = win.winfo_reqwidth(), win.winfo_reqheight()
                win.geometry(f"+{max(0, sw - w - 24)}+{max(0, sh - h - 60)}")
                self._toast_win = win

                def _close():
                    try:
                        win.destroy()
                    except Exception:
                        pass
                    if self._toast_win is win:
                        self._toast_win = None
                self.root.after(4000, _close)
            except Exception:
                pass
        self._post(_do)

    # ---- 账号列表行 ----
    def _select_row(self, email: str):
        self._selected_email = email
        for row_email, frame in self._row_frames:
            frame.configure(fg_color=ROW_SEL if row_email == email else "transparent")

    def _row_menu(self, event, email: str):
        self._select_row(email)
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="复制邮箱",
                      command=lambda: (self.root.clipboard_clear(),
                                       self.root.clipboard_append(email),
                                       self.log(f"已复制: {email}")))
        m.add_command(label="查看详情",
                      command=lambda: self._show_account_info(email))
        m.add_separator()
        m.add_command(label="删除该账号",
                      command=lambda: self._delete_email(email))
        m.tk_popup(event.x_root, event.y_root)

    def _show_account_info(self, email: str):
        acc = next((a for a in self.pool.all() if a.get("email") == email), None)
        if not acc:
            self.log(f"账号 {email} 已不在库中", "err")
            return
        created = float(acc.get("created_at") or 0)
        lines = [
            f"账号: {email}",
            f"状态: {STATUS_TEXT.get(acc.get('status'), acc.get('status'))}",
            f"添加时间: {time.strftime('%m-%d %H:%M', time.localtime(created)) if created else '?'}",
        ]
        if created > 0:
            lifetime = float(self.config.get("account_lifetime_seconds", 86400))
            remain_s = lifetime - (time.time() - created)
            lines.append(f"生命周期剩余: {remain_s/3600:.1f} 小时" if remain_s > 0 else "生命周期: 已过期")
        messagebox.showinfo("账号详情", "\n".join(lines))

    def _build_rows(self):
        for w in self._row_widgets:
            w.destroy()
        self._row_widgets = []
        self._row_frames = []
        for a in self.pool.all():
            email = a.get("email", "")
            status = a.get("status", "?")
            created = float(a.get("created_at") or 0)
            remain = ""
            if created > 0:
                lifetime = float(self.config.get("account_lifetime_seconds", 86400))
                remain_s = lifetime - (time.time() - created)
                remain = f"{remain_s/3600:.1f}h" if remain_s > 0 else "已过期"
            row = ctk.CTkFrame(self._rows_box, fg_color="transparent",
                               corner_radius=10, height=32)
            row.pack(fill="x", pady=1)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=email, text_color=TEXT, anchor="w",
                         font=(FONT, 11)).grid(row=0, column=0, sticky="ew", padx=12)
            ctk.CTkLabel(row, text=STATUS_TEXT.get(status, f"● {status}"),
                         text_color=STATUS_COLOR.get(status, TEXT),
                         font=(FONT, 11, "bold"), width=86,
                         anchor="w").grid(row=0, column=1, padx=6)
            ctk.CTkLabel(row, text=remain, text_color=MUTED, width=70,
                         anchor="e", font=(FONT, 11)).grid(row=0, column=2, padx=12)
            for w in (row, *row.winfo_children()):
                w.bind("<Button-1>", lambda _e, em=email: self._select_row(em))
                w.bind("<Button-3>", lambda e, em=email: self._row_menu(e, em))
            self._row_widgets.append(row)
            self._row_frames.append((email, row))
        if self._selected_email not in [e for e, _ in self._row_frames]:
            self._selected_email = ""

    # ---- 自动换号 / 系统代理开关 ----
    def _set_auto(self, on: bool):
        """显式状态设置（顶栏开关与托盘菜单共用；修托盘点击 no-op）。"""
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
                self._toast(f"换号成功 {r.email}")
            else:
                self.log(f"换号失败: {r.error}", "err")
                self._toast(f"换号失败: {r.error}", ok=False)
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
        self.btn_state.configure(text="刷新中...")
        self._refresh_now()
        self.root.after(1000, lambda: self.btn_state.configure(text=""))

    def _delete_selected(self):
        """删除列表中选中的账号（在用号除外，其淘汰由换号流程接管）。"""
        if not self._selected_email:
            self.log("请先在列表中点选要删除的账号", "err")
            return
        self._delete_email(self._selected_email)

    def _delete_email(self, email: str):
        if self.switcher.is_switching():
            self.log("切换进行中，请完成后再删除账号", "err")
            return
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
            if self._selected_email == email:
                self._selected_email = ""
        else:
            self.log(f"删除取消：{email} 已是当前在用号或已不在库中", "err")
        self._refresh_now()

    def _switch_selected(self):
        email = self.account_var.get()
        if not email or email == "-":
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
                self._toast(f"切换成功 {email}")
            else:
                self.log(f"切换失败: {r.error}", "err")
                self._toast(f"切换失败: {r.error}", ok=False)
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
            self.card_account_sub.configure(text="状态: 在用" if active else "无在用账号")

            # 代理链路卡
            ll = getattr(self.monitor, "last_link", None) if self.monitor else None
            if not self.engine.is_running():
                self.card_link_val.configure(text="未运行", text_color=ERR)
                self.card_link_sub.configure(
                    text=f"检查于 {ll.get('ts', '?')}" if ll else " ")
                self._update_tray_status("idle")
            elif ll:
                ok = bool(ll.get("ok"))
                self.card_link_val.configure(text="正常" if ok else "异常",
                                             text_color=OK if ok else ERR)
                self.card_link_sub.configure(
                    text=f"出口 {ll.get('ip') or '?'} · 检查于 {ll.get('ts', '?')}")
                self._update_tray_status("ok" if ok else "err")
            else:
                self.card_link_val.configure(text="运行中", text_color=TEXT)
                self.card_link_sub.configure(text=f"端口 {self.engine.port}")
                self._update_tray_status("ok")

            # 账号列表 + 下拉
            self._build_rows()
            emails = [a["email"] for a in self.pool.all()
                      if a.get("status") in ("ready", "active") and self.pool._is_valid(a)]
            current = self.account_var.get()
            self.account_combo.configure(values=emails or ["-"])
            if current not in emails and emails:
                self.account_var.set(emails[0])
            elif not emails:
                self.account_var.set("-")

            # 流量 + 有效期（后台查，不卡 UI）
            self._refresh_traffic_async()
        except tk.TclError:
            pass
        except Exception:
            pass

    def _refresh_traffic_async(self):
        """后台线程查询当前账号流量/有效期，经 _post 更新统计卡片。"""
        def work():
            try:
                active = self.pool.get_active()
                if not active:
                    self._post(self._reset_stat_cards)
                    return
                from cloud_api import CloudAccount
                cloud = CloudAccount(active["email"], active.get("password", ""))
                if not cloud.login():
                    self._post(lambda: self._apply_traffic(None, "查询失败（登录失败）"))
                    return
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
        self.card_traffic_val.configure(text="-", text_color=TEXT)
        self.card_traffic_sub.configure(text=" ")
        self.traffic_bar.set(0)
        self.traffic_bar.configure(progress_color=ACCENT_CYAN)
        self.card_expire_val.configure(text="-", text_color=TEXT)
        self.card_expire_sub.configure(text=" ")

    def _apply_traffic(self, data, err):
        if err:
            self.card_traffic_val.configure(text="查询失败", text_color=ERR)
            self.card_traffic_sub.configure(text=err)
            return
        total, used = data
        if total <= 0:
            self.card_traffic_val.configure(text="-", text_color=TEXT)
            self.card_traffic_sub.configure(text="无流量数据")
            self.traffic_bar.set(0)
            return
        remain = max(total - used, 0)
        low = remain / 1024 / 1024 < float(self.config.get("min_traffic_mb", 30.0))
        self.card_traffic_val.configure(text=self._fmt_bytes(remain),
                                        text_color=WARN if low else TEXT)
        self.traffic_bar.set(max(0.0, min(1.0, remain / total)))
        self.traffic_bar.configure(progress_color=WARN if low else ACCENT_CYAN)
        self.card_traffic_sub.configure(
            text=f"已用 {self._fmt_bytes(used)} · 总量 {self._fmt_bytes(total)}")

    def _apply_expire(self, class_expire):
        try:
            ce = float(class_expire or 0)
        except (TypeError, ValueError):
            ce = 0
        if ce <= 0:
            self.card_expire_val.configure(text="-", text_color=TEXT)
            self.card_expire_sub.configure(text="无数据")
            return
        left = ce - time.time()
        if left <= 0:
            self.card_expire_val.configure(text="已过期", text_color=ERR)
            self.card_expire_sub.configure(text=" ")
            return
        warn = left < float(self.config.get("expiry_threshold_seconds", 1800.0))
        self.card_expire_val.configure(text=f"{left/3600:.1f} 小时",
                                       text_color=WARN if warn else TEXT)
        self.card_expire_sub.configure(
            text=f"到期 {time.strftime('%m-%d %H:%M', time.localtime(ce))}")

    @staticmethod
    def _fmt_bytes(n) -> str:
        try:
            n = float(n)
        except (TypeError, ValueError):
            return "-"
        if n >= 1024 * 1024:
            return f"{n/1024/1024:.1f}MB"
        if n >= 1024:
            return f"{n/1024:.1f}KB"
        return f"{int(n)}B"

    # ---- 系统托盘（pystray，可选）----
    def _setup_tray(self):
        try:
            import pystray
        except Exception:
            return
        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", self._tray_show, default=True),
            pystray.MenuItem("立即换号", lambda *_: self._post(self._manual_switch)),
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

    def _make_tray_icon(self, status: str = "idle"):
        """PIL 现画托盘图标：蓝色圆角底 + 白云 + 状态角标。"""
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([2, 2, 62, 62], radius=16, fill=(47, 111, 237, 255))
        d.ellipse([13, 24, 33, 44], fill=(255, 255, 255, 255))
        d.ellipse([26, 16, 50, 40], fill=(255, 255, 255, 255))
        d.rectangle([15, 34, 48, 44], fill=(255, 255, 255, 255))
        dot = {"ok": (31, 166, 92), "err": (225, 75, 65)}.get(status, (148, 163, 184))
        d.ellipse([40, 40, 62, 62], fill=dot + (255,), outline=(255, 255, 255, 255), width=2)
        return img

    def _update_tray_status(self, status: str):
        if self._tray is None:
            return
        try:
            self._tray.icon = self._make_tray_icon(status)
        except Exception:
            pass

    def _tray_show(self, *_):
        def _do():
            self.root.deiconify()
            try:
                self.root.state("normal")
            except tk.TclError:
                pass
            self.root.lift()
            self.root.focus_force()
        self._post(_do)

    def _tray_toggle_auto(self, *_):
        # 托盘线程读不到控件变量，用显式状态翻转（修复点击无效）
        self._post(lambda: self._set_auto(not self._auto_on))

    def _tray_toggle_sysproxy(self, *_):
        self._post(lambda: self._set_sysproxy(not self._sysproxy_on))

    def _tray_quit(self, *_):
        self._post(self._quit)

    # ---- 开机自启（HKCU Run 注册表）----
    def _get_autostart(self) -> bool:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                cmd, _ = winreg.QueryValueEx(k, AUTOSTART_NAME)
                return bool(cmd)
        except Exception:
            return False

    def _set_autostart(self, enable: bool):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run",
                                0, winreg.KEY_SET_VALUE) as k:
                if enable:
                    if getattr(sys, "frozen", False):
                        cmd = f'"{sys.executable}"'
                    else:
                        pyw = os.path.join(sys.base_prefix, "pythonw.exe")
                        main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               "main.py")
                        cmd = f'"{pyw}" "{main_py}"'
                    winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(k, AUTOSTART_NAME)
                    except FileNotFoundError:
                        pass
        except Exception:
            self.log("开机自启设置失败（注册表访问受限）", "err")

    # ---- 设置对话框 ----
    def _open_settings(self):
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("设置")
        dlg.geometry("520x600")
        dlg.configure(fg_color=BASE)
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
            ctk.CTkLabel(dlg, text=label, text_color=TEXT, font=(FONT, 11)).grid(
                row=row, column=0, sticky="w", padx=16, pady=7)
            v = tk.StringVar()
            if key == "expiry_threshold_minutes":
                v.set(str(int(float(self.config.get("expiry_threshold_seconds", 1800.0)) / 60)))
            elif key == "account_lifetime_hours":
                v.set(str(int(float(self.config.get("account_lifetime_seconds", 86400.0)) / 3600)))
            else:
                v.set(str(self.config.get(key, "")))
            ctk.CTkEntry(dlg, textvariable=v, width=180, height=30, corner_radius=8,
                         fg_color=FIELD_BG, border_color=CARD_BORDER, text_color=TEXT).grid(
                row=row, column=1, padx=16)
            vars[key] = v
            row += 1

        ctk.CTkLabel(dlg, text="飞书 Webhook（可选）", text_color=TEXT,
                     font=(FONT, 11)).grid(row=row, column=0, sticky="w", padx=16, pady=7)
        v = tk.StringVar(value=str(self.config.get("feishu_webhook", "")))
        ctk.CTkEntry(dlg, textvariable=v, width=260, height=30, corner_radius=8,
                     fg_color=FIELD_BG, border_color=CARD_BORDER, text_color=TEXT).grid(row=row, column=1, padx=16)
        vars["feishu_webhook"] = v
        row += 1
        ctk.CTkLabel(dlg, text="飞书 AppID/Secret/OpenID（可选）", text_color=TEXT,
                     font=(FONT, 11)).grid(row=row, column=0, sticky="w", padx=16, pady=7)
        v = tk.StringVar(value="/".join(str(self.config.get(k, "") or "")
                                        for k in ("feishu_app_id", "feishu_app_secret", "feishu_open_id")))
        ctk.CTkEntry(dlg, textvariable=v, width=260, height=30, corner_radius=8,
                     fg_color=FIELD_BG, border_color=CARD_BORDER, text_color=TEXT).grid(row=row, column=1, padx=16)
        vars["feishu_app"] = v
        row += 1
        ctk.CTkLabel(dlg, text="日报推送时间（HH:MM，空=禁用）", text_color=TEXT,
                     font=(FONT, 11)).grid(row=row, column=0, sticky="w", padx=16, pady=7)
        v = tk.StringVar(value=str(self.config.get("daily_report_time", "") or ""))
        ctk.CTkEntry(dlg, textvariable=v, width=180, height=30, corner_radius=8,
                     fg_color=FIELD_BG, border_color=CARD_BORDER, text_color=TEXT).grid(row=row, column=1, padx=16)
        vars["daily_report_time"] = v
        row += 1

        # 界面主题（即时生效）
        ctk.CTkLabel(dlg, text="界面主题", text_color=TEXT, font=(FONT, 11)).grid(
            row=row, column=0, sticky="w", padx=16, pady=7)
        self.theme_var = tk.StringVar(value=self.theme)
        ctk.CTkOptionMenu(dlg, variable=self.theme_var, values=list(THEMES),
                          width=180, height=30, corner_radius=8,
                          fg_color=FIELD_BG, button_color=ACCENT,
                          button_hover_color=ACCENT_HI, text_color=TEXT,
                          dropdown_fg_color=FIELD_BG, dropdown_hover_color=ROW_SEL,
                          dropdown_text_color=TEXT,
                          command=self._on_theme_choice).grid(row=row, column=1, padx=16)
        row += 1

        # 托盘相关开关
        self.mtt_var = tk.BooleanVar(value=bool(self.config.get("minimize_to_tray", True)))
        ctk.CTkCheckBox(dlg, text="关闭时最小化到托盘", variable=self.mtt_var,
                        text_color=TEXT, fg_color=ACCENT, hover_color=ACCENT_HI,
                        checkbox_width=20, checkbox_height=20,
                        font=(FONT, 11)).grid(row=row, column=0, columnspan=2,
                                              sticky="w", padx=16, pady=(8, 2))
        row += 1
        self.autostart_var = tk.BooleanVar(value=self._get_autostart())
        ctk.CTkCheckBox(dlg, text="开机自动启动", variable=self.autostart_var,
                        text_color=TEXT, fg_color=ACCENT, hover_color=ACCENT_HI,
                        checkbox_width=20, checkbox_height=20,
                        font=(FONT, 11)).grid(row=row, column=0, columnspan=2,
                                              sticky="w", padx=16, pady=2)
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
                # 日报时间：宽松校验（空=禁用；只卡格式不强制功能）
                import re
                drt = vars["daily_report_time"].get().strip()
                if drt:
                    mch = re.match(r"^(\d{1,2}):(\d{2})$", drt)
                    if not mch or int(mch.group(1)) > 23 or int(mch.group(2)) > 59:
                        raise ValueError("日报推送时间格式应为 HH:MM（如 09:00）")
                    drt = f"{int(mch.group(1)):02d}:{mch.group(2)}"
                self.config.set("daily_report_time", drt)
                self.config.set("minimize_to_tray", bool(self.mtt_var.get()))
                self.config.save()
                self._set_autostart(bool(self.autostart_var.get()))
                self.log("设置已保存", "ok")
                if new_port != old_port and self.engine.is_running():
                    self._restart_engine_async(f"端口 {old_port} → {new_port}")
                dlg.destroy()
            except ValueError as e:
                self.log(f"设置格式错误: {e}", "err")

        ctk.CTkButton(dlg, text="保存", width=90, height=32, corner_radius=10,
                      fg_color=ACCENT, hover_color=ACCENT_HI,
                      command=save).grid(row=row, column=0, padx=16, pady=14)
        ctk.CTkButton(dlg, text="取消", width=90, height=32, corner_radius=10,
                      fg_color=SECONDARY, hover_color=SECONDARY_HI, text_color=TEXT,
                      border_width=1, border_color=CARD_BORDER,
                      command=dlg.destroy).grid(row=row, column=1, pady=14)

    def _on_theme_choice(self, choice: str):
        self._apply_theme(choice)

    def _restart_engine_async(self, reason: str):
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
        """窗口关闭按钮：托盘可用时收进托盘，否则真退出。"""
        if self._tray is not None and bool(self.config.get("minimize_to_tray", True)):
            self.root.withdraw()
            if not self._tray_hint:
                self._tray_hint = True
                self.log("已收进系统托盘：双击图标恢复窗口，右键 → 退出 才是真退出")
            return
        self._quit()

    def _quit(self):
        try:
            self.config.set("win_x", self.root.winfo_x())
            self.config.set("win_y", self.root.winfo_y())
            self.config.set("win_w", self.root.winfo_width())
            self.config.set("win_h", self.root.winfo_height())
            self.config.save()
        except Exception:
            pass
        for job in (self._refresh_job, self._pump_job, self._bg_job):
            if job:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
        self._restore_sysproxy()   # 先还原系统代理，再停引擎（退出即端口死）
        self.engine.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
