# Misty 快速注册（Android 自用版）

这是 Misty Android 的配套工具。它可以注册一个新账号并显示账号、密码和本机记录的注册时间，供你复制到 Misty 登录。0.4.0 还提供可选的无障碍换号悬浮按钮：启用后，点按钮会注册一个新账号，再尝试在 Misty 中登出、填写新账号并重新登录。

## 手工注册与登录

1. 打开“Misty 快速注册”，点“注册新账号”。
2. 等待注册和登录校验完成；收取验证码可能需要一两分钟。
3. 点“复制账号”，切换到 Misty 登录页粘贴；再回到本应用复制密码并粘贴。

失败后可点“继续上次注册”；若这次临时邮箱无法继续使用，可选“换一个邮箱重新注册”。本应用只显示最近一次成功注册的账号。下次注册成功会替换界面和本机记录中的旧账号，请先保存需要保留的凭据。旧版本没有记录注册时间的账号会显示“旧版未记录”。

临时邮箱由 [mail.tm](https://mail.tm) 提供，未来可能无法用于找回密码，请妥善保存账号和密码。账号凭据保存在本机加密数据中，系统备份已关闭。复制到剪贴板后，其他有权限读取剪贴板的应用仍可能接触到复制内容。

## 可选：Misty 换号悬浮按钮

在本应用中点“开启 Misty 换号悬浮按钮”，进入 Android 无障碍设置，然后由你手动启用“Misty 换号按钮”服务。Android 不允许本应用静默开启该服务。服务启用后，在 Misty 前台时会显示悬浮按钮；点“换号”后，本服务会自动创建并验证一个新账号，然后尝试操作 Misty 的界面：登出、填写新账号和密码、提交登录，最后尝试返回桌面、结束 Misty 后台进程并重新启动 Misty。

此流程依赖 Misty 当前界面、控件文字和系统无障碍行为，可能因应用版本、语言、系统限制或界面变化而失败。失败时请检查 Misty 当前状态；你也可以回到上面的手工流程，将已注册账号复制到 Misty 登录。该服务仅在你启用后工作，它可读取当前窗口的无障碍节点并执行界面操作；只在信任本应用且理解该访问范围时启用。应用声明了网络权限、结束后台进程权限和无障碍服务。

## 构建

在 Windows PowerShell 中，从 `mistycloud_manager` 仓库根目录运行：

```powershell
pwsh -NoProfile -File .\android-companion\build.ps1
```

构建脚本默认使用本机 SDK `D:\AndroidTools\sdk`、Android SDK build-tools 37.0.0 / Android API 37 平台，以及 JDK 21（`D:\AndroidTools\jdk\jdk-21.0.12.1+1`）。可通过 `-SdkRoot` 和 `-JdkRoot` 指定其他安装位置：

```powershell
pwsh -NoProfile -File .\android-companion\build.ps1 `
  -SdkRoot 'D:\path\to\Android\Sdk' `
  -JdkRoot 'D:\path\to\jdk-21'
```

脚本输出 `android-companion\build\misty-quick-register-debug.apk`。若本机默认 keystore 不存在，脚本会在 `D:\AndroidTools\signing\misty-account-helper-debug.jks` 创建本地调试签名密钥（口令为脚本内的开发默认值）；这是用于本机构建的调试签名，不应当作安全的发布签名。构建成功和 APK 签名校验不等于已在真机安装、运行或完成账号注册。
