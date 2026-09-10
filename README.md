# 账号大师 Pro 2.0

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078d4.svg)](https://www.microsoft.com/windows)
[![GitHub stars](https://img.shields.io/github/stars/Han-00/mistycloud-manager?style=social)](https://github.com/Han-00/mistycloud-manager)
[![GitHub last commit](https://img.shields.io/github/last-commit/Han-00/mistycloud-manager)](https://github.com/Han-00/mistycloud-manager/commits/main)

> **MistyCloud 临时账号自动注册 / 过期换号 / v2ray 代理管理** —— 设计初衷：**简单高效，省去繁琐，直击要点**。

## ✨ 核心特性

- 🔄 **自动换号** — 流量低 / 即将过期 / 链路探测失败自动切换；冷却 15 分钟；备用不足自动注册补齐
- 📡 **v2ray 代理一体化** — 内置引擎，端口开箱即用（10808 SOCKS / 10809 HTTP），系统代理自动接管
- 🌐 **智能分流** — 默认全走代理 + 国内域名白名单直连（v2ray routing 层双写）
- 📊 **数据面板** — 流量热力图（4 档色阶）/ 续航预测 / 链路可用率 / 换号事件流
- 🖥️ **三套 UI 自动回退** — Web（pywebview 推荐）/ 玻璃质感（customtkinter）/ 经典 tkinter
- 🪟 **托盘 + 自启 + 飞书通知** — 关闭 = 退出代理（无误触），可选开机自启和换号飞书推送
- 📦 **可分发** — PyInstaller onedir 打包，v2ray 引擎内嵌，其他机器零依赖即用

## 📑 目录

- [启动](#启动)
- [端口](#端口)
- [v2ray 运行时](#v2ray-运行时)
- [自动换号](#自动换号)
- [飞书通知（可选）](#飞书通知可选)
- [目录说明](#目录说明)
- [打包分发](#打包分发)
- [注意](#注意)
- [协议](#协议)
- [致谢](#致谢)

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

## 打包分发

```bash
python build_dist.py
# 产出：dist/账号大师Pro2_v2.0_YYYYMMDD.zip（含 v2ray 引擎，零依赖即用）
```

onedir 模式 + 排除 numpy/scipy 等大依赖，最终 ~35 MB。详见 `docs/分发使用说明.txt`。

## 注意

- **不要**同时运行 bat 版与打包 exe 版：两者都用 10808 端口，会互抢。
- 换号 / 注册依赖网络，全程 6~10 秒属正常（瓶颈在临时邮箱收码与远端接口）。
- 本项目为个人工具，**协议 GPL-3.0（强传染）**，fork / 衍生作品也必须开源。

## 协议

本项目采用 **GPL-3.0** 协议发布。完整条款见 [LICENSE](LICENSE) 文件（GNU 官方逐字文本，未作任何改动）。

```text
Copyright (C) 2026 Han-00

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
```

## 致谢

- v2ray 引擎来自本地 Misty 安装（`v2ray.exe / v2ctl.exe / geoip.dat / geosite.dat`）
- [pywebview](https://pywebview.flowrl.com/) · [customtkinter](https://github.com/TomSchimansky/CustomTkinter) · [Flask](https://flask.palletsprojects.com/) 等开源项目