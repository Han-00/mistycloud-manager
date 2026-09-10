# -*- coding: utf-8 -*-
"""冻结（打包）环境路径验证 —— 离线、零副作用。

为什么需要它：
打包后最容易翻车的地方全在路径上，而且都不报错、只表现为「功能莫名不可用」：
- sys._MEIPASS（只读资源目录）与 exe 同级目录（可写数据目录）必须分得清；
  写错会导致配置改了不生效、日志找不到。
- 内嵌的 _internal/v2ray_bin 必须被 _discover() 优先命中，
  否则目标机没装 Misty 就直接「找不到引擎」。
- 引擎必须能从只读资源目录复制到可写工作目录（v2ray 要写 config.json）。

做法：在源码环境里伪造 frozen 三件套（sys.frozen / sys.executable /
sys._MEIPASS）指向**真实打包产物**，从而不启动程序、不连网、不动系统代理，
就复现出打包版的路径解析行为。

用法（先打包，再跑）：
    python -m PyInstaller --noconfirm 账号大师Pro2.spec
    python tests/test_frozen_paths.py
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist", "账号大师Pro2")
INTERNAL = os.path.join(DIST, "_internal")
EXE = os.path.join(DIST, "账号大师Pro2.exe")


def main():
    if not os.path.exists(EXE):
        print(f"[SKIP] 未找到打包产物：{EXE}\n       先运行 PyInstaller 打包。")
        return 0

    # ---- 伪造 frozen 环境（必须在 import 项目模块之前）----
    sys.frozen = True
    sys._MEIPASS = INTERNAL
    sys.executable = EXE
    sys.path.insert(0, ROOT)

    import paths

    print(f"[1] 可写数据目录 : {paths.base_dir()}")
    assert paths.base_dir() == DIST, f"数据目录应落在 exe 同级，实际 {paths.base_dir()}"
    assert paths.is_portable(), "应为绿色包（数据跟程序走）"
    print("    ✓ 数据目录 = exe 同级（绿色包），未误用临时目录")

    print(f"[2] 只读资源目录 : {paths.resource_dir()}")
    assert paths.resource_dir() == INTERNAL, paths.resource_dir()
    assert os.path.isdir(paths.resource_dir()), "资源目录不存在"
    print("    ✓ 资源目录 = _internal")

    # ---- 内嵌引擎 ----
    bundled = os.path.join(INTERNAL, "v2ray_bin")
    print(f"[3] 内嵌引擎目录 : {bundled}")
    assert os.path.isdir(bundled), f"未找到内嵌引擎目录 {bundled}（spec 未收集？）"
    from v2ray_engine import V2RayEngine
    from config import Config

    eng = V2RayEngine(Config())
    ok = eng._discover()
    print(f"    _discover() = {ok}, source = {eng.v2ray_source_dir}")
    assert ok, "打包后未能发现 v2ray 引擎（目标机没装 Misty 时会直接失效）"
    assert os.path.normcase(eng.v2ray_source_dir) == os.path.normcase(bundled), \
        f"应优先命中内嵌引擎，实际命中 {eng.v2ray_source_dir}"
    print("    ✓ 内嵌引擎被优先命中（目标机无需安装 Misty）")

    # ---- 前端页面 ----
    webui = os.path.join(INTERNAL, "webui", "index.html")
    print(f"[4] 前端页面     : {webui}")
    assert os.path.exists(webui), "webui/index.html 未打进包"
    print("    ✓ webui 已打包")

    # ---- 复制到可写工作目录（v2ray 要往里写 config.json）----
    print("[5] prepare() 复制引擎到工作目录")
    work = os.path.join(DIST, "v2ray_work")
    existed = os.path.isdir(work)
    ok = eng.prepare()
    assert ok, "prepare() 失败"
    print(f"    work_dir = {eng.work_dir}")
    for f in V2RayEngine.REQUIRED_FILES:
        p = os.path.join(eng.work_dir, f)
        assert os.path.exists(p), f"缺少 {f}"
    print("    ✓ 4 个引擎文件已就位于可写目录")

    # 清理：v2ray_work 是运行时生成的，不该进分发包（与 _internal/v2ray_bin 重复占 32M）
    if not existed:
        shutil.rmtree(work, ignore_errors=True)
        print("    · 已清理测试生成的 v2ray_work（避免混进分发包）")

    print("\n== 冻结环境路径验证通过 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
