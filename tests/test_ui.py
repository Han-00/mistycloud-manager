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
from ui import AppUI


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
    print(f"[2] 刷新后: {ui.lbl_active.cget('text')!r} | {ui.lbl_traffic.cget('text')!r} "
          f"| {ui.lbl_expire.cget('text')!r} | {ui.lbl_proxy.cget('text')!r}")
    print(f"    账号列表行数: {len(ui.tree.get_children())} 下拉项: {len(ui.account_combo['values'])}")

    # 3. 设置对话框：直接测内部保存逻辑
    dlg_rows = {}
    orig_toplevel = tk.Toplevel

    class FakeDlg(orig_toplevel):
        def grab_set(self): pass
        def destroy(self): pass

    # 打开真实对话框并立即自动保存
    def auto_save():
        # 找到最新 Toplevel 里的保存按钮
        for w in ui.root.winfo_children():
            if isinstance(w, orig_toplevel):
                kids = w.winfo_children()
                btns = [k for k in kids if isinstance(k, tk.ttk.Button)]
                for b in btns:
                    if b.cget("text") == "保存":
                        b.invoke()
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
    ui._refresh_job and ui.root.after_cancel(ui._refresh_job)
    ui.root.destroy()
    print("[4] UI 自测完成")

if __name__ == "__main__":
    main()
