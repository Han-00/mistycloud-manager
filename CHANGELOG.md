# 更新日志 — 账号大师 Pro 2.0

## 2026-08-31（下午）

### 新增
- **过期账号自动删除**：`cleanup_expired()` 由「标记」改为「真删除」——`expired`/`banned` 直接从账号库删除，ready 但生命周期/服务端套餐到点的先标后删；**在用号（active）永不删除**，由换号流程接管，避免正在服务的代理被抽走。监控每轮执行，删除时记日志。已上线生效（账号库 8 → 3）。
- **换号结果飞书推送**：成功推「✅ 自动换号成功 / 账号 / 出口: 直连 → 代理」，失败推「❌ 原因」。开放平台应用三件套（app_id/app_secret/open_id）优先，群机器人 webhook 兜底；通知异常绝不阻塞换号主流程。已实测打通（open_id 可用 tenant_access_token 查通讯录自动获取）。
- `tests/test_cleanup.py` 过期删除隔离测试（4 项断言）。

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
