# -*- coding: utf-8 -*-
"""UI 自测：构建主窗口 → 刷新 → 设置对话框读写 → 手动换号入口 → 关闭。"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\Desktop_Files\mistycloud_manager")

import tkinter as tk

from config import Config
from account_pool import AccountPool
from v2ray_engine import V2RayEngine
from switcher import Switcher
from monitor import Monitor
try:
    from ui_glass import GlassAppUI as AppUI   # 液态玻璃版（需 customtkinter）
except Exception:
    from ui import AppUI                        # 降级：ttk 深色版


def main():
    cfg = Config()
    pool = AccountPool(cfg)
    eng = V2RayEngine(cfg)
    sw = Switcher(cfg, pool, eng)
    mon = Monitor(cfg, pool, sw, eng)

    ui = AppUI(cfg, pool, sw, eng, mon)
    ui.log = lambda m, tag="": print(f"  [UI] {m}")  # 重定向日志，避免依赖 mainloop
    sw.log = ui.log
    mon.log = ui.log

    # 1. 主窗口构建
    ui.root.update_idletasks()
    ui.root.update()
    print(f"[1] 主窗口标题: {ui.root.title()!r} geometry={ui.root.geometry()}")

    # 2. 刷新（含流量后台线程）
    ui._refresh_now()
    t0 = time.time()
    while time.time() - t0 < 6:
        ui.root.update()
        time.sleep(0.05)
    print(f"[2] 刷新后: 账号={ui.card_account_val.cget('text')!r} "
          f"| 流量={ui.card_traffic_val.cget('text')!r} "
          f"| 有效期={ui.card_expire_val.cget('text')!r} "
          f"| 链路={ui.card_link_val.cget('text')!r}")
    try:
        combo_values = ui.account_combo.cget("values")
    except Exception:
        combo_values = ui.account_combo["values"]
    print(f"    账号列表行数: {len(ui.tree.get_children())} 下拉项: {len(combo_values)}")

    # 2.5 主题切换（重建 UI 后控件仍可用）
    ui._apply_theme("深海蓝", save=False)
    ui.root.update()
    ui._apply_theme("浅色云雾", save=False)
    ui.root.update()
    print(f"[2.5] 主题切换往返: 账号={ui.card_account_val.cget('text')!r} "
          f"行数={len(ui.tree.get_children())}")

    # 3. 设置对话框：直接测内部保存逻辑
    dlg_rows = {}
    orig_toplevel = tk.Toplevel

    class FakeDlg(orig_toplevel):
        def grab_set(self): pass
        def destroy(self): pass

    # 打开真实对话框并立即自动保存（递归找任意类型的「保存」按钮，兼容 ttk/CTk）
    def _find_save_btn(widget):
        try:
            if widget.cget("text") == "保存" and hasattr(widget, "invoke"):
                return widget
        except Exception:
            pass
        for c in widget.winfo_children():
            found = _find_save_btn(c)
            if found:
                return found
        return None

    def auto_save():
        for w in ui.root.winfo_children():
            if isinstance(w, orig_toplevel):
                btn = _find_save_btn(w)
                if btn:
                    btn.invoke()
                    return True
        return False

    ui.root.after(200, auto_save)
    ui._open_settings()
    t0 = time.time()
    while time.time() - t0 < 2:
        ui.root.update()
        time.sleep(0.05)
    print(f"[3] 设置对话框保存: min_traffic_mb={cfg.get('min_traffic_mb')} "
          f"proxy_port={cfg.get('proxy_port')} webhook={cfg.get('feishu_webhook')!r}")

    # 4. 关闭
    for _attr in ("_refresh_job", "_pump_job", "_bg_job"):
        _job = getattr(ui, _attr, None)
        if _job:
            ui.root.after_cancel(_job)
    ui.root.destroy()
    print("[4] UI 自测完成")

if __name__ == "__main__":
    main()
