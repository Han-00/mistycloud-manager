# 账号大师 Pro 2.0

MistyCloud 临时账号自动注册 / 过期换号 / v2ray 代理管理，可视化 GUI。

设计初衷：**简单高效，省去繁琐，直击要点**。

## 启动

双击 **`启动.bat`**（依赖本机 `C:\Python313`）。

- 最小化窗口不影响代理，程序继续后台运行
- **关闭窗口（✕）= 退出程序 = v2ray 停止 = 代理断开**
- 状态行 `链路: 正常 (10808) · 出口 x.x.x.x · 检查于 HH:MM:SS` 是存活信号

## 端口

| 协议 | 端口 |
|------|------|
| SOCKS5 | 10808 |
| HTTP | 10809 |

系统代理指向 `127.0.0.1:10809`，浏览器无需任何配置。

## v2ray 运行时

引擎首次运行会把 `v2ray.exe / v2ctl.exe / geoip.dat / geosite.dat` 从 Misty 安装目录
（`%LOCALAPPDATA%\Programs\Misty`）复制到 `v2ray_work/` 独立运行。
目录缺失时安装 Misty，或在 `settings.json` 的 `v2ray_dir` 指定。

## 自动换号

- 流量低于阈值 / 有效期不足 / 链路连续 2 轮探测失败 → 自动换号（可用「自动换号」开关关闭）
- 失败账号冷却 15 分钟；备用不足自动注册补齐
- 过期/失效账号自动从账号库删除（在用号除外）
- 换号结果自动推送飞书

## 飞书通知（可选）

`settings.json` 三选一：

```json
{
  "feishu_app_id": "cli_xxx",
  "feishu_app_secret": "xxx",
  "feishu_open_id": "ou_xxx"
}
```

或只填 `feishu_webhook`（群机器人）。open_id 可用 app 凭据换取 token 后查
`/open-apis/contact/v3/users` 获取。

## 目录说明

| 路径 | 说明 | 入 git？ |
|------|------|----------|
| `*.py` | 全部源码 | ✅ |
| `tests/` | 回归测试（`python tests/test_xxx.py`） | ✅ |
| `启动.bat` | 启动器 | ✅ |
| `账号大师Pro2.spec` | PyInstaller 打包配置（可选） | ✅ |
| `accounts.json` | 账号库（**含明文密码**） | ❌ |
| `settings.json` | 配置（**含飞书密钥**） | ❌ |
| `app.log` | 运行日志 | ❌ |
| `v2ray_work/` | v2ray 运行时副本 | ❌ |

## 注意

- **不要**同时运行 bat 版与打包 exe 版：两者都用 10808 端口，会互抢。
- 换号 / 注册依赖网络，全程 6~10 秒属正常（瓶颈在临时邮箱收码与远端接口）。
