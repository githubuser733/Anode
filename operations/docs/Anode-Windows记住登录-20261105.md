# Anode 的 Windows 记住登录候选版

用户已选择保留当前 Windows 账户，由 Windows 原生凭据窗口提供保存和复用。本页记录首次候选的设计与检查。**最新状态：v2 已经获准切换，用户已在 Windows 窗口勾选保存并登录，但普通重连仍曾要求密码，免输入未通过。** 修正后的 v4 本地检查通过，尚未获本次切换确认；关闭后重新启动及电脑重启复用仍为 UNTESTED。没有创建新账户、由 Anode 读取/保存密码或更改 Windows 登录策略。后续实际证据以 [登录与重启实测](./Anode-登录与Codex重启实测-20261105.md) 为准。

## 为什么原版每次都问

当前源码 `work/anode-fork/src/Anode/Daemon/RdpViewer.cs:220` 明确执行 `SetAllowCredentialSaving(false)`。原版在已使用密码登录的子会话重新连接时还会再次要求交互输入。这是程序当前的选择，不是创建一个相同密码的账户就会消失。

本机 Anode 的子会话以当前用户运行；宿主启动使用当前 Windows 身份，通信管道是 CurrentUserOnly。独立账户需要另做进程、数据目录和通信权限适配，不能用它直接替换登录框里的当前身份。

Microsoft 的 [Child Sessions 文档](https://learn.microsoft.com/en-us/windows/win32/termserv/child-sessions) 说明此机制依附现有用户会话。[Power Automate 的子会话限制](https://learn.microsoft.com/en-us/power-automate/desktop-flows/run-desktop-flows-pip#limitations-of-child-session-mode) 说明 PIN 和本机凭据策略可能影响免提示登录。

## 候选版的范围

- 增加 CLI 显式参数 `--remember-login`，仅在可见启动时允许 Windows 显示保存凭据选项；设计目标是普通重连不强制新输密码，实际免输仍须验收。
- 密码只由用户输入到 Windows 原生窗口；Anode 不调用读取凭据接口，也不把密码写进源码、启动参数、配置、MCP、日志或聊天。
- 继续保留 Sign in 手动修复入口；默认模式仍禁止保存，不会影响未选择此功能的调用者。
- 不修改开机自动登录、远程桌面安全策略、NLA、防火墙或账户密码。不是空密码登录，也不是免除 Windows 身份认证。
- 显示分辨率重连仍关闭交互凭据选项，避免 Agent 调整显示时突然弹出登录窗口。
- 本次 41 个 MCP 工具定义与当前活动版本逐字段完全一致。

Microsoft 的 [AllowCredentialSaving](https://learn.microsoft.com/en-us/windows/win32/termserv/imsrdpclientnonscriptable4-allowcredentialsaving) 只承诺让原生凭据窗口提供保存复选框；不能据此保证本机策略一定允许保存或子会话一定复用。必须由用户输入后测试普通 Reconnect 才能验收。

## 文件与检查

候选配置：`config/anode-remember-login-candidate-20261105.json`。

候选程序：`work/anode-remember-login-20261105-118f620b/publish-v2/anode.exe`。

隔离源码分支：`work/anode-remember-source-118f620b`，基于当前活动提交 `f3c18607eae740dc38fdf4d98a1965cd0f33df7a`；没有覆盖原源码工作区或当前运行程序。

运行下列检查不会启动子桌面或接触凭据：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\AI infrastructure\agent-Windows-operation\scripts\Start-AnodeRememberLogin.ps1" -CheckOnly
```

预期 `ANODE_REMEMBER_ENTRY_CHECK_OK`、`credentialAccess=false`、`processStarted=false`。旧版本仍运行时 `blockedByExistingHost=true` 是预期保护。

```powershell
& "F:\AI infrastructure\agent-Windows-operation\work\anode-remember-login-20261105-118f620b\publish-v2\anode.exe" __remember-login-check | Out-Host
```

预期 `REMEMBER_LOGIN_LOCAL_CHECKS_OK credentialReads=0 credentialWrites=0 connections=0`。它验证原生 COM 的保存/提示组合、16 种重连策略、宿主启动门控、Stop、旧模式及 CLI 拒绝隐藏交互启动，不会真实登录。

证据目录：`artifacts/anode-remember-login-20261105-118f620b`。本轮 .NET 10.0.403 构建结果为 0 警告、0 错误，发布版检查通过。

首次 PATH 命中 .NET 7 导致 `NETSDK1045`，已改用项目已有 .NET 10 SDK，未安装新 SDK。旧 `Test-AnodeTimelineProtocol.py` 对 10/31 原版基线的检查失败；差异来自当前活动版已有的文本增强工具参数。`compare-protocol.py` 严格核对当前活动二进制哈希后，再比较活动版和候选版，实际输出 `REMEMBER_LOGIN_CURRENT_MCP_SCHEMA_OK tools=41 candidateEqualsActive=true`；旧测试和旧基线均未改动。差异路径保留于 `current-protocol.json`。

尚未运行整个 selftest --quick；它包含临时文件自动删除，不能据此扩大本次专用检查的覆盖声明。

## 切换与用户验收

首次切换到 v2 已完成；本节保留切换操作方法。以后每次切换前仍须核对现有子会话及程序，保存工作，并在该次切换的明确范围内获准正常退出；入口不会自动停止旧版。

切换获准后，首次使用 `Start-AnodeRememberLogin.ps1 -SignIn` 打开原生窗口。由用户输入当前 Windows 账户密码并勾选保存；不要把密码发到聊天。普通启动使用同一脚本、不加 SignIn，优先尝试 Windows 复用。

用户复核清单：

- [ ] 上述 CheckOnly 输出正确，新版程序哈希与候选配置一致。
- [ ] 切换后，在 Windows 原生窗口中能看到保存凭据选项；用户亲自输入并选择保存。
- [ ] `status --json` 显示 ready、agentReady=true、rememberWindowsLogin=true。
- [ ] 普通 Reconnect 后不再输入密码，原应用及内容仍在，宿主就绪。
- [ ] 关闭后重新启动、电脑重新登录或重启后的复用分别测试并留证据；未测试前继续标记 UNTESTED。

如果本机策略仍要求密码，应记录真实错误或窗口状态后再决定后续方案，不把勾选保存当作已经实现免输入。
