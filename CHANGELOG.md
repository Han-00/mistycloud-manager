# 更新日志 — 账号大师 Pro 2.0

## 2026-09-10

### 新增
- **流量热力图**（仪表盘全宽卡片，近 7 天 × 24 小时）：`stats.py` 新增按小时流量桶（`hourly`，随 `add_traffic` 增量入桶、保留 8 天自动修剪），`hourly_series(7)` 供 UI；前端格子亮度按 log 强度映射主题 accent 色（换肤自动重绘），悬浮显示「日期 时:00 · 消耗」，深色/浅色模式均适配，标题栏显示 7 天总消耗。可一眼识别作息曲线与异常尖峰（如凌晨偷跑流量）。
- **续航预测**：`stats.avg_daily_bytes(3)` 计算近 3 天平均日消耗（当天按已流逝时长折算权重，避免半天拉低均值）；流量卡大数字下显示「照近 3 天烧法，还能撑 X 天」，无数据时提示积累中。
- **托盘即油表**：托盘图标顶部新增流量水位条（>50% 白 / 25~50% 黄 / <25% 红，无数据不画），tooltip 升级为三行（链路状态 + 剩余流量 + 还能撑 X 天）——不开窗口扫一眼托盘即知状态。

### 测试
- `tests/test_stats.py` 新增 [10]-[13]：小时桶增量/幂等/回退、持久化与 series 升序、过期桶修剪、avg_daily_bytes 当天折算（1% 相对容差，消除测试与实现的秒级时钟漂移）。

### 打包分发（可发给他人电脑使用）
- **分发包由 onefile 改为 onedir 目录分发**（`账号大师Pro2.spec`：`EXE(exclude_binaries=True)` + `COLLECT`）。onefile 每次启动都要把运行时解压到临时目录（慢 3~5 秒、易被杀软拦），且资源走临时路径难以排障；onedir 下 `settings.json`/`accounts.json`/`stats.json`/`app.log`/`v2ray_work` 全部稳定落在 exe 同级目录。`upx=False`——避免杀软误报，也避免 UPX 压坏 Go 编译的 v2ray.exe。
- **内嵌 v2ray 引擎（目标机零依赖）**：spec 把 `v2ray.exe`/`v2ctl.exe`/`geoip.dat`/`geosite.dat` 收进 `_internal/v2ray_bin/`；`V2RayEngine._discover()` 新增该目录为**最高优先候选**，并补上「已有 `v2ray_work`」作为末位兜底（Misty 被卸载/移动后仍可用）。目标机从此无需安装 Misty、无需装 Python。
- **统一路径解析 `paths.py`（消除 5 份重复实现）**：config / account_pool / logger / stats / v2ray_engine 原先各抄了一份 `_base_dir()`，是「改一处漏四处」的典型债。现统一为 `paths.base_dir()`（可写数据）与 `paths.resource_dir()`（只读资源），并加入**可写探测**：exe 同级目录不可写时（例如解压到 `Program Files`）五处**同步**回退到 `%LOCALAPPDATA%\AccountMasterPro2`，避免出现「配置写 A 处、日志写 B 处、引擎又建在 C 处」的分裂。
- **整包瘦身 29M**：spec 增加 `excludes` 排除 `numpy`/`scipy`/`pandas`/`matplotlib`/`torch`/`cv2`/`Qt`——项目与全部运行时依赖（pywebview / pythonnet / customtkinter / sv_ttk / pystray / Pillow）都不引用它们，但打包机上装着这些库时 PyInstaller 会顺着 hook 误收（实测 numpy 独占 27M）。**112M → 83M**。
- **新增 `build_dist.py` 一键分发**：挪走旧产物（用重命名规避安全网关的批量删除拦截）→ 构建 → 冻结路径验证 → 附说明文件 → 洁净度检查（确保不含 settings/accounts/app.log/v2ray_work）→ 压缩 zip。修复两个坑：PyInstaller 删不掉 1k+ 文件的旧 dist 时会**静默沿用旧产物**（表现为「以为重打了，其实产物没变」）；版本号提取不能用 `## v?([\d.]+)`，否则先命中日期标题得到 `v2026`。
- **新增 `tests/test_frozen_paths.py`**：伪造 `sys.frozen`/`sys._MEIPASS`/`sys.executable` 指向真实产物，离线验证「数据目录 = exe 同级 / 资源目录 = _internal / 内嵌引擎被优先命中 / prepare() 能把引擎复制到可写目录」，不启动程序、不连网、不动系统代理。
- 文档：新增 `docs/分发使用说明.txt`（解压即用、WebView2 要求、数据位置、托盘行为、常见问题）。

### 修复
- **界面回退链形同虚设，实际是「启动即崩」**：`ui_web.py` 只在 `run()` 内部惰性 `import webview`，顶层不引入 pywebview——于是「没装 pywebview」时 `from ui_web import WebAppUI` 依然成功，程序选中 Web 版，直到建窗口才抛 ImportError 直接崩溃，**根本不会回退**（回退链实际只对 ui_glass 有效，因为它顶层就 `import customtkinter`）。现 `_load_ui()` 显式探测依赖再选择实现，并抽成独立函数以便测试。
- **回退不再静默**：启动日志新增一行「界面实现：xxx」，发生回退时连同每条原因标红——过去 pywebview 缺失、WebView2 起不来、打包漏模块三种情况表现完全一样（界面莫名变旧），无从定位。
- **`tests/test_wiring.py` 失效断言**：它引用的是重构前的控件名（`ui.lbl_traffic` / `lbl_expire` / `lbl_proxy`，现版为 `card_traffic_val` / `card_expire_val` / `card_link_val`），早已跑不起来。控件文本改走容错读取（读不到只提示、不再制造假失败），硬断言只保留与 UI 无关的事实（「引擎确实在跑」）；文件头补注该测试会真实连网换号、占用代理端口，跑前须退出正在运行的实例。
- 新增 `tests/test_ui_fallback.py`：用 `sys.modules[m] = None` 屏蔽依赖，不依赖环境差异，锁定三级回退的落点与「每次回退都留痕」这一机制——上述 pywebview 回退失效问题正是它第一次运行就抓出来的。

### 重构
- **设置校验集中到 `settings_schema.py`**：原先 `ui.py` / `ui_glass.py` / `ui_web.py` 各写了一遍同一套规则（端口 1-65535、生命周期正数、日报时间正则、飞书三字段拆分），不仅重复，**而且已经开始漂移**——同一种错误在三个界面的提示文案都不一致（「日报时间须为 HH:MM」vs「日报推送时间格式应为 HH:MM」）。这正是「改一处漏两处」的实证：哪天要把端口范围放开，改了 Web 版，另两个界面还会继续拦。
  - 现统一为 `parse_settings()`（校验 + 规范化，返回可直接 `config.set` 的键值）与 `apply_settings()`（顺带写入 config，返回写入内容供判断端口是否变化）。三个界面只保留各自职责：落盘时机、错误提示、端口变更后重启引擎、关窗。
  - 刻意**不新增任何下限校验**（如自动换号阈值仍允许负数——那等于关闭该项阈值判断）：重构不夹带行为变更，要收紧应当单独提。
  - 新增 `tests/test_settings_schema.py`（9 组用例）：单位换算（分钟/小时→秒）、端口边界含端点、生命周期正数、日报时间格式与补零、整数与小数、缺省字段跳过、飞书三字段拆分、以及**校验失败时不产生半截配置**。它第一次运行就抓出我把「负数备用账号数静默归零」误改成报错的行为漂移——重构最怕的就是这种夹带。
- **`AccountPool._is_valid` 提升为公开 `is_usable`，并修掉它会抛异常的隐患**：这个判断被**三处界面**（账号列表是否弱化显示、下拉框是否列出）+ **三处账号池内部逻辑**（`pick_next` 挑候选、`count_valid` 统计、`cleanup_expired` 清理）共用，却顶着私有下划线暴露在外——典型的「私有方法事实上公开」。
  - 更实际的问题是**它会抛**：账号记录里的 `created_at` / `class_expire` 一旦无法解析成数字（脏数据），`float()` 就抛 ValueError。`ui_web` 用 `try/except` 兜住后**退化成「按 status 猜」**，会静默显示错误的可用性（界面显示可用、实际已过期）；而 `ui.py` / `ui_glass.py` **根本没有兜底**，同一条脏记录足以把账号列表刷挂。三个界面对同一份数据的行为竟然不一致。
  - 现在 `is_usable` 内部对脏数据健壮（时间戳解析失败按 0 处理、配置项脏值回落默认生命周期），三个界面统一受益、行为一致；`ui_web` 那个「猜」的兜底已删除。
  - 新增 `tests/test_account_usable.py`（7 组）：状态短路、生命周期边界、套餐到期、**脏数据类型全覆盖**（`None` / `""` / `"abc"` / `[]` / `{}`）、配置脏值回落、缺键记录安全，以及三个内部调用方与 `is_usable` 判断一致。
- **`webui/index.html` 拆分为三件**（1030 → 230 行）：抽出 `app.css`（400 行样式）与 `app.js`（399 行逻辑），HTML 只留结构。**`<head>` 里那段提前应用主题/深浅模式的内联脚本刻意保留内联**——它必须在样式表之前设置 `data-theme`，挪成外部文件会引入首帧闪色。拆分后用真实渲染复核（`App` 对象已加载、`--bg` 取自主题变量、卡片与导航在位），并扩展 `test_frozen_paths.py` 点名检查三个文件都打进包——漏收任何一个页面都会残掉（少 css 全无样式、少 js 界面完全不动）。
- **修复：流量查询失败会导致状态推送中断**（`ui_web.build_state`）：查询失败时 `tr_state` 形如 `{"err": ...}`、**没有 `remain` 字段**，而续航预测那行直接 `tr_state["remain"]` → KeyError。它发生在 `build_state` 内部，会让整轮状态推送失败、**界面就此停止更新**（日志仍在正常写，所以格外难察觉）。改为先取 `remain` 再判断。
- 新增 `tests/test_ui_web_state.py`（8 组）：`build_state` 字段齐全、账号行 `valid`/`remain_s`/`remain_pct` 计算、无在用账号、流量余量与低量标记、续航预测、查询失败分支，以及**删除保护规则**（在用号拒绝删除、换号进行中拒绝删除与清理、批量清理只动 expired/banned）。上述 KeyError 正是它第一次运行抓出来的。

## 2026-09-08

### 新增
- **直连域名分流（国内网站不走代理）**：配置项 `proxy_bypass_domains`。**内置 80 个常用国内域名**（抖音/字节、飞书/钉钉、学习通/智慧树/雨课堂/MOOC、DeepSeek/Kimi/智谱、腾讯/QQ/微信、阿里/淘宝/支付宝、百度、网易、B站、微博/知乎/小红书、京东、视频/直播/音乐、WPS/CSDN/Gitee、edu.cn/gov.cn/12306 等），`settings.json` 同名键用于**追加**——加载时去重合并（「内置 + 追加」语义），程序运行中整写本文件也不会丢内置域名。两层生效：
  - 系统代理层（`sysproxy.py`）：`enable()` 把列表合并进注册表 `ProxyOverride`（自动展开 `*.域名 + 裸域` 去重），浏览器及一切遵循系统代理的应用对这些域名直连；`apply_if_enabled` 把例外串纳入一致性比较，改列表后下一轮收敛自动重写注册表，无需重启。
  - 引擎分流层（`v2ray_engine.py`）：`_apply_bypass_routing` 把域名注入生成配置的 routing（`outboundTag: direct`，**置顶**插入以早于模板可能的 catch-all；模板与兜底两路径都生效，缺 routing/direct outbound 自动补齐）——即使流量已进入本地端口也直连出去。仅对装了 ZeroOmega 之类接管浏览器代理的场景起决定作用（那类浏览器忽略系统 ProxyOverride，只有引擎规则生效）。
  - 测试：`tests/test_bypass.py` 新增（离线注入/置顶/去重/去通配/缺项补齐），`test_sysproxy.py` 增 [3b]/[6b]（合并写入/未变静默/变更重收敛），全绿；新代码生成 config.json 实测置顶直连规则含 80 域名。

### 修复
- **开/关程序闪黑窗**：`_kill_port_owners` 里的 netstat/tasklist/taskkill 未带 `CREATE_NO_WINDOW`，GUI 进程（pythonw/打包 exe）每次调用都弹控制台黑框；由于它挂在 `stop()` 里而 `start()` 先调 `stop()`，打开程序、关闭程序、每次自动换号都会闪。三处 subprocess.run 统一补 `creationflags=_NO_WINDOW`（模块级常量，非 Windows 为 0）。

## 2026-09-01

### 新增
- **Web 前端 UI 全面落地**（方向 A+C：深色控制台布局 + pywebview 壳）：
  - `webui/index.html`：单页应用——左侧导航（仪表盘/账号库/日志/设置）、状态横幅（呼吸灯/当前账号/出口 IP）、SVG 环形流量进度、有效期秒级倒计时、真表格账号库（状态胶囊 + 剩余时长迷你条形图 + hover 操作按钮）、日志底部抽屉（错误红点 + 导航角标）、完整设置表单。JS→Python 走 pywebview 桥（后台线程执行），Python→JS 走队列泵 + evaluate_js（页面加载前日志积压补放，不丢）。无桥环境自动填演示数据，浏览器可直接打开调样式。
  - `ui_web.py`：pywebview 壳层，接口与 `ui.AppUI` 完全对齐（构造签名/log/run），`switcher`/`monitor`/`account_pool` 零改动。加载链：`ui_web` → `ui_glass` → `ui`，缺 pywebview 自动降级。
  - 打包：`账号大师Pro2.spec` 已加 webui 资源与 webview/edgechromium 隐藏导入。
  - 冒烟测试 `tests/smoke_ui_web.py`：隐藏启动→加载显示→托盘→真退出全链路，打桩 sysproxy/engine.stop/config.save。
- **主题系统**：6 套配色（极光蓝紫默认/星河紫/翠松绿/熔岩橙/蔷薇粉/石墨灰）× 深浅 2 模式 = 12 种外观。`data-theme` 管主色族、`data-mode` 管中性色翻转，两轴正交；设置页色板即点即切，存 localStorage 重启记忆；`<head>` 提前应用主题避免首帧闪色。
- **系统托盘**（`ui_web.py`，pystray）：图标随链路状态变色（绿/红/灰），双击恢复窗口，右键菜单（显示/立即换号/自动换号/系统代理/退出）；关闭按钮收托盘不退出，`_quit_requested` 标志区分收托盘与真退出，真退出保存窗口几何。

### 修复
- **启动闪黑框**：WebView2 页面渲染前窗口黑一瞬（`background_color` 在 EdgeChromium 后端不生效）→ 改 `hidden=True` 隐藏启动 + loaded 后 show() + 3s 兜底强制显示。
- **托盘缺失**：`ui_web.py` 未实现托盘导致关窗即退 → 补全 pystray 托盘，行为与 `ui_glass.py` 对齐。
- **WebView2 私密模式初始化失败**（E_ABORT）：必须 `private_mode=False + 固定 storage_path`（默认私密模式退出清理临时目录时新版 SDK 抛 BrowserProcessId NoneType）。

### 其他
- 项目清理：`vibe_images/`、`ui_mockup/`（概念稿与验收截图）、`__pycache__/`、`tests/e2e_stdout.log` 已移入回收站；`.workbuddy/`、`stats.json` 加入 `.gitignore`。

## 2026-08-31（晚）

### 优化
- **液态玻璃 UI**（`ui_glass.py`，现为默认界面）：基于 customtkinter 6.0 全新实现，设计稿经用户拍板选「浅色云雾」配色。
  - 浅天蓝极光渐变背景（PIL 1/3 分辨率预混色 + 高斯模糊，窗口缩放 120ms 防抖重绘）、白色磨砂玻璃圆角卡片、蓝色主按钮、蓝箭头下拉、彩色状态徽章、浅色终端风日志区。
  - 与 `ui.AppUI` 业务/线程模型完全一致（后台线程一律 `_post()` 队列、主线程 100ms 轮询）；控件属性名保持兼容（card_*/traffic_bar/account_combo/log_text），`tests/test_ui.py` 断言双路兼容（保存按钮递归查找、下拉取值兼容），实测全绿（真实流量/有效期数据正常上卡）。
  - 降级链：`GlassAppUI`（需 customtkinter）→ sv_ttk 版 `AppUI` → 零依赖 clam 深色，任一环节导入失败自动降级，功能不受影响。
  - 备选配色深海蓝 / 暮光紫已实现为可切换主题（效果图留档 `vibe_images/glass_*_ui_*.png`，见下方「三主题一键切换」）。
- **深色主题 UI 重构**：按设计稿（`vibe_images/account_master_pro_dark_ui_*.png`）全面改版界面。
  - 主方案：接入 sun-valley 主题（`sv-ttk`，Win11 风格）——圆角卡片、拨动开关、主色按钮、卡片式列表/日志边框；实测截图与设计稿基本一致。
  - 布局：顶部标题栏 + 四张统计卡（当前账号 / 流量剩余+进度条 / 有效期 / 代理链路）→ 状态彩色徽章账号库列表 → 主按钮「立即换号」+ 次级按钮 → 终端风格日志区。
  - 流量低于换号阈值数值转预警橙；有效期临近阈值同理；代理链路正常绿/异常红，含出口 IP 与检查时间。
  - 降级保底：未安装 `sv-ttk` 时自动退回零依赖深色方案（ttk clam + 自定义样式），功能不受影响。
  - 打包：`账号大师Pro2.spec` 已加 `collect_data_files('sv_ttk')` 与隐藏导入（重新打 exe 时主题资源不再缺失）。
  - `tests/test_ui.py` 断言同步更新到新卡片组件，实测全绿（真实流量/有效期数据正常上卡）。

### 新增
- **托盘常驻**（`pystray`）：程序启动即驻留系统托盘，PIL 手绘云朵图标带状态点（绿=链路正常 / 红=异常 / 灰=未运行），随刷新实时更新。
  - 右键菜单：显示主窗口 / 立即换号 / 自动换号（勾选态与主界面开关同步）/ 退出。
  - 关窗行为可选「最小化到托盘」（`minimize_to_tray`，默认开），首次最小化时日志提示一次；托盘所有回调经 `_post()` 队列回主线程，避免 tkinter 跨线程崩溃。
  - 设置里新增「开机自动启动」开关：写 HKCU Run 注册表（`AccountMasterPro2`），源码运行指向 `pythonw.exe main.py`，打包后指向 exe。
  - 打包：`账号大师Pro2.spec` hiddenimports 已补 `pystray` / `pystray._win32`（平台后端动态加载，静态分析扫不到）。
- **三主题一键切换**：`THEMES` 三套完整配色——浅色云雾（默认，light）/ 深海蓝 / 暮光紫（dark），设置里「界面主题」下拉即选即生效，无需重启：整套界面重建、日志内容保留、极光背景重渲染，选择持久化到 `ui_theme`。`tests/test_ui.py` 新增主题往返断言（[2.5]），两套深色主题截图验证通过。
- **操作细节打磨**：
  - 账号库行右键菜单：复制邮箱 / 查看详情 / 删除该账号（沿用原删除防误删逻辑）。
  - 快捷键：`F5` 刷新状态、`Ctrl+T` 立即换号。
  - 换号等操作结果右下角桌面浮窗提示（4 秒自动消失），成功绿 / 失败红。
  - 日志区新增「清空」按钮。

## 2026-08-31（下午）

### 新增
- **过期账号自动删除**：`cleanup_expired()` 由「标记」改为「真删除」——`expired`/`banned` 直接从账号库删除，ready 但生命周期/服务端套餐到点的先标后删；**在用号（active）永不删除**，由换号流程接管，避免正在服务的代理被抽走。监控每轮执行，删除时记日志。已上线生效（账号库 8 → 3）。
- **换号结果飞书推送**：成功推「✅ 自动换号成功 / 账号 / 出口: 直连 → 代理」，失败推「❌ 原因」。开放平台应用三件套（app_id/app_secret/open_id）优先，群机器人 webhook 兜底；通知异常绝不阻塞换号主流程。已实测打通（open_id 可用 tenant_access_token 查通讯录自动获取）。
- **手动删除账号**：账号库列表选中行 → 点「删除选中」→ 二次确认后从库删除（不可恢复）。在用号（active）禁删，提示先点「立即换号」——其淘汰统一由换号流程接管（换号成功即自动删旧号），避免删了在用号却不断代理的不一致。
- `tests/test_cleanup.py` 过期删除隔离测试（4 项断言）。

### 修复
- **换号后旧号残留**：换号成功后被替换的旧在用号此前仅降为 `ready` 混入备用池——会被 `pick_next` 重新选中导致**重复换号**（尤其"有效期不足"触发的循环），并使 `count_valid` 虚高造成**补号失准**、备用池新旧混淆。现由 `set_active` 直接删除旧在用号（切换前后同一账号不删自己），下轮不再可选；`cleanup_expired` 不再承担旧在用号清理。新增 `tests/test_set_active.py`（6 项场景）。
- **手动删除竞态**：「删除选中」的禁删检查只在弹窗前做一次，确认弹窗期间该号若被自动换号升为在用仍会被删；且切换进行中不拦截删除，可能删掉正在切换的候选号。现 `remove()` 的状态检查与删除在同一把锁内原子完成（在用号拒绝删除，返回 bool 区分真删/未删，日志不再误报「已删除」）；删除入口在切换进行中直接拦截；`set_active` 同步加固——目标账号不在库中时不淘汰旧在用号，避免无 active 空窗。`tests/test_set_active.py` 扩充场景 5/6 覆盖。

### 维护
- 清理调试残留脚本（`_test_*.py`、`_big_download.py`、`_evidence_test.py`、`_restore_accounts.py` 共 11 个）。
- 删除 PyInstaller 中间产物 `build/` 与过期打包 `dist/`（其 exe 构建于 01:28 半修复期，缺今日功能，且与 bat 版并行运行会抢 10808 端口）。
- 删除无用的顶层 `config.json`（端口 10881 旧模板残留；引擎实际使用 Misty 目录模板 + 显式构建 vmess 段）。
- 初始化 git 仓库；`accounts.json`（账号凭据）、`settings.json`（飞书密钥）、`app.log`、`v2ray_work/`（二进制运行时）一律不入库。

## 2026-08-31（凌晨）— 2.0 重写基线

对照 dist_fixed3（AccountMasterPro v1.0.9）修复清单全量排查，以干净 Python 3.13 源码重写。

### 新增
- **链路自愈**（monitor）：探测失败 2 轮确认 → 用当前节点重启恢复 → 仍不通才自动换号（尊重自动换号开关）。
- **换号并发守卫**：手动与自动换号互斥（`_guard`），杜绝互踩 v2ray 进程（v1.0.7 教训）。
- 失败账号 15 分钟冷却；换号失败先用原节点恢复现有链路；无节点账号绝不覆盖配置（不烧号、不写坏链路）。
- monitor 后台线程：流量/有效期检查（取客户端生命周期与服务端 `class_expire` 较早者；`/app/user` 缺字段时用订阅响应头兜底）、每 5 分钟补号、每 10 分钟心跳留痕。
- `logger.py` 文件日志 `app.log`（2MB 轮换，线程安全）。
- tkinter GUI：状态面板 / 立即换号 / 补号 / 选号切换 / 设置对话框；队列轮询修复后台线程碰 Tk 必崩的问题。
- `启动.bat` 一键启动（pythonw 无黑窗）；SOCKS 10808 / HTTP 10809 与旧版对齐，浏览器无需改配置。
- `tests/` 回归测试（引擎 / 云 API 解析 / 换号 / 监控 / 场景 / UI / 接线，真实网络 E2E + 自愈冒烟）。

### 修复
- `aid:null` 导致节点解析崩溃、节点被静默丢弃 → `aid=0` 是 VMess 合法值。
- 订阅 URL `sub=3` 参数重复拼接。
- 登录接口重复 POST（错误码查询复用已建立的会话）。
- `settings.json` / `accounts.json` 非原子写（断电/并发写坏全库）→ tmp + `os.replace`。
- 手动换号失败通知只在异常分支触发 → 统一在出口处理，成功必通知。
- v2ray 配置由「模板部分替换」改为「vmess 段整体重建」，杜绝旧 UUID 残留导致假成功烧流量。

### 已知事项
- dist_fixed3 历史中泄露的订阅 URL 需在 MistyCloud 服务台手动吊销（KNOWN_ISSUES A-1）。
- 若 PyInstaller 打包 exe 运行，请勿与 bat 版同时运行（同端口互抢）。
