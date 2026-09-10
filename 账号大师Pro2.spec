# -*- mode: python ; coding: utf-8 -*-
"""打包配置 — 账号大师 Pro 2.0（onedir / 目录分发）

为什么用 onedir 而不是 onefile：
- onefile 每次启动要把 PyInstaller 运行时解压到临时目录，启动慢 3~5 秒，
  且 WebView2 从临时目录加载更容易被杀软拦。
- 运行数据（settings.json / accounts.json / stats.json / v2ray_work）一律落在
  exe 同级目录，onedir 下路径稳定、可随时查看排障。
- 目录分发可以内嵌 v2ray 引擎，目标机无需安装 Misty、无需装 Python。

分发方式：把整个 dist/账号大师Pro2/ 打成 zip 发走，解压后双击「账号大师Pro2.exe」。

构建：C:\\Python313\\python.exe -m PyInstaller --noconfirm 账号大师Pro2.spec
"""
import os

from PyInstaller.utils.hooks import collect_data_files

# ---- 1. 间接依赖的资源收集 ----
# ui_glass.py（customtkinter）与 ui.py（sv_ttk）都是惰性导入，静态分析扫不到：
# 显式收集其资源并声明隐藏导入（PIL 也在 _render_bg 内惰性导入）。
# pystray 按平台动态加载后端（win32），同样要显式声明。
sv_datas = collect_data_files('sv_ttk')
ctk_datas = collect_data_files('customtkinter')

# ui_web.py（pywebview）同为惰性导入：收集 webview 包内 js/css 资源 +
# 项目的 webui/ 前端页面；打包机未装 pywebview 时不阻断构建（运行时回退旧 UI）。
try:
    webview_datas = collect_data_files('webview')
except Exception:
    webview_datas = []
webview_datas += [('webui', 'webui')]

# ---- 2. 内嵌 v2ray 引擎（目标机零依赖的关键）----
# 独立引擎只依赖这 4 个文件，原样复制进 _internal/v2ray_bin/；
# 运行时 v2ray_engine._discover() 会把它作为首选候选目录，
# 再由此复制一份到 exe 同级的 v2ray_work/ 作为可变工作目录。
# 注意：用 datas 而不是 binaries——避免 PyInstaller 分析其依赖，
# 也避免 UPX 压缩破坏 Go 编译的 v2ray.exe。
V2RAY_FILES = ("v2ray.exe", "v2ctl.exe", "geoip.dat", "geosite.dat")


def _v2ray_source():
    """v2ray 源目录：环境变量 V2RAY_SRC 优先，其次 Misty 安装位置。"""
    candidates = [
        os.environ.get("V2RAY_SRC", ""),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Misty"),
        r"C:\Users\kesai\AppData\Local\Programs\Misty",
    ]
    for d in candidates:
        if d and os.path.exists(os.path.join(d, "v2ray.exe")):
            return d
    return ""


v2ray_datas = []
_src = _v2ray_source()
if _src:
    for _f in V2RAY_FILES:
        _p = os.path.join(_src, _f)
        if os.path.exists(_p):
            v2ray_datas.append((_p, "v2ray_bin"))
    print(f"[spec] 内嵌 v2ray 引擎: {_src} -> v2ray_bin/ ({len(v2ray_datas)} 个文件)")
else:
    print("[spec] 警告: 未找到 v2ray 引擎源目录，"
          "打出的包需要目标机自行安装 Misty 或在 settings.json 配置 v2ray_dir")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=sv_datas + ctk_datas + webview_datas + v2ray_datas,
    hiddenimports=['sv_ttk', 'customtkinter', 'PIL', 'pystray', 'pystray._win32',
                   'webview', 'webview.platforms.winforms',
                   'webview.platforms.edgechromium', 'clr'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 砍掉误收集的科学计算栈：项目与全部运行时依赖（pywebview / pythonnet /
    # customtkinter / sv_ttk / pystray / Pillow）都不引用 numpy，
    # 但打包机上装着 pandas/opencv 等库时 PyInstaller 会顺着 hook 把它捞进来，
    # 实测占 27M（占整包 24%）。排除后请在干净目录做一次启动冒烟再分发。
    excludes=['numpy', 'scipy', 'pandas', 'matplotlib',
              'torch', 'cv2', 'IPython', 'notebook',
              'PyQt5', 'PyQt6', 'PySide2', 'PySide6'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # onedir：二进制交给 COLLECT 收进 _internal
    name='账号大师Pro2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # 不开 UPX：避免杀软误报与 Go 二进制被压坏
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='账号大师Pro2',
)
