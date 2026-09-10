# -*- coding: utf-8 -*-
"""界面回退链测试：Web → 玻璃 → ttk，且**每次回退都留下原因**。

存在的意义：回退链过去是完全静默的。pywebview 没装、WebView2 初始化失败、
打包漏了模块——三种原因在用户眼里表现一模一样：「界面怎么变回旧版了」，
无从查起。本测试锁定的是「留痕」这个机制本身。

做法：用 `sys.modules[m] = None` 让 import 抛 ImportError，不依赖真实环境差异。
不建窗口、不连网、零副作用。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
# main.py 里 _load_ui() 的返回顺序： (类, 实现名, 回退原因列表)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app_main  # noqa: E402  （只 import 定义，不会跑 main()）

_UI_MODS = ("ui_web", "ui_glass", "ui")


def _reset():
    """清掉已缓存的 UI 模块，保证 import 真的重新走一遍。"""
    for m in _UI_MODS:
        sys.modules.pop(m, None)


def _block(mods):
    saved = {}
    for m in mods:
        saved[m] = sys.modules.get(m, "__ABSENT__")
        sys.modules[m] = None      # import 时抛 ImportError
    return saved


def _restore(saved):
    for m, v in saved.items():
        if v == "__ABSENT__":
            sys.modules.pop(m, None)
        else:
            sys.modules[m] = v


def _load(block_mods):
    """在指定模块不可导入的前提下调用回退链。"""
    _reset()
    saved = _block(block_mods)
    try:
        return app_main._load_ui()
    finally:
        _restore(saved)
        _reset()


def main():
    # [1] 依赖全不可用 → 必须落到 ttk，且两条原因都在
    _cls, name, notes = _load(["webview", "customtkinter"])
    assert name.startswith("ttk"), f"应降到 ttk 版，实际 {name}"
    assert len(notes) == 2, f"应记录 2 条回退原因，实际 {notes}"
    assert any("ui_web" in n for n in notes), notes
    assert any("ui_glass" in n for n in notes), notes
    print(f"[1] 全不可用 → {name}，原因 2 条 ✓")
    for n in notes:
        print(f"      {n}")

    # [2] 只屏蔽 pywebview → 绝不可能是 Web 版，且记下第一条原因
    _cls, name, notes = _load(["webview"])
    assert "Web 版" not in name, f"pywebview 不可用却选了 Web 版: {name}"
    assert notes and "ui_web" in notes[0], f"未记录 ui_web 的失败原因: {notes}"
    print(f"[2] 无 pywebview → {name}，首条原因 ✓")

    # [3] 不屏蔽 → 实现与原因条数必须自洽（这正是「留痕」的意义所在）
    _cls, name, notes = _load([])
    expect = {0: "Web 版", 1: "玻璃版", 2: "ttk 版"}
    assert len(notes) in expect, f"回退条数异常: {notes}"
    assert name.startswith(expect[len(notes)]), f"实现与原因数不自洽: {name} / {notes}"
    print(f"[3] 当前环境 → {name}，回退 {len(notes)} 次 ✓")

    print("\n== 界面回退链测试通过 ==")


if __name__ == "__main__":
    main()
