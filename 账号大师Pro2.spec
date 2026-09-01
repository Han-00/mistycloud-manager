# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

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

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=sv_datas + ctk_datas + webview_datas,
    hiddenimports=['sv_ttk', 'customtkinter', 'PIL', 'pystray', 'pystray._win32',
                   'webview', 'webview.platforms.winforms',
                   'webview.platforms.edgechromium', 'clr'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='账号大师Pro2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
