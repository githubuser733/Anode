# Anode 登录、Codex 重启与 Alpha 窗口归属

最新状态：**v4 已按本次授权运行，自动启动仍弹 Windows 凭据窗口，免输入未通过；用户更正为人工登录可以正常完成。用户最新选择“先保留当前窗口，暂不登录”**，因此不再进行输入、重连或新建会话；同会话自动重连及冷启动复用均留待后续验收。不再把之前的“无法登录”笼统当作人工认证失败。保留原 Windows 用户，没有新建账户或写死密码。独立观察器已捕获 22:21 的 Codex 真实重启，因此关闭登录自启动尚不足以解决问题。Alpha 已解除 Codex 包身份继承并在主桌面独立运行。下方早期结果保留原验收范围，最新时间线见文末。

## Codex 为什么重启

用户确认触发条件是 Stop seat 后新建子会话并登录，普通 Reconnect 不属于同一触发条件。

2026-11-05 系统事件记录的直接链路：

1. 17:23:00.569，AppModel-Runtime 208/212：Codex 程序包在配置运行时或加入桌面 AppX 容器时出现 `0x80070005`。
2. 17:23:00.574，AppXDeploymentServer 603，记录 12649480：`sihost.exe` 请求 `RegisterByPackageFullName`，带 `ForceTargetApplicationShutdownOption,RepairAppRegistrationOption`。
3. 同一活动链记录了修复程序包 ACL，随后包注册成功。
4. 17:23:34，主桌面 Codex 重新建立主进程 53852。

Microsoft 对 [ForceTargetApplicationShutdown](https://learn.microsoft.com/en-us/uwp/api/windows.management.deployment.deploymentoptions) 的说明是为了注册而强制关闭目标程序包的相关进程。这支持“Windows 包修复导致这次进程退出和重建”，不能把它说成 Anode 源码直接执行了结束 Codex。

包清单 `C:\Program Files\WindowsApps\OpenAI.Codex_26.1029.7803.0_x64__2p2nqsd0c76g0\AppxManifest.xml` 声明了 `windows.startupTask`，TaskId 为 App，登录自启动初始为启用。实际用户配置在 `HKCU\Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\SystemAppData\OpenAI.Codex_2p2nqsd0c76g0\App`。Microsoft 的 [StartupTask 文档](https://learn.microsoft.com/en-us/uwp/api/windows.applicationmodel.startuptask) 说明此任务可以在用户登录时启动。

本轮通过 Anode 中的 Windows“设置→应用→启动”关闭了显示名为 ChatGPT 的该包登录启动项，实际 State 从 2 变为 1。**它也会使主桌面 Windows 登录时不再自动打开 Codex，手动启动不变。** 没有改 WindowsApps ACL、包清单、NLA、凭据委派策略、系统服务或卸载包。

关闭自启动是针对本机已有证据的可逆处理。事件并未直接记录出错进程 167788 的 SessionId，因此“哪个子会话进程最先导致访问错误”仍不应称为完全确定。

## 如何验收 Codex

- 先以旧事件运行 `check_codex_anode_restart.py --assert-no-repairs`，实际返回 1，识别到记录 12649480；红灯能力已验证。
- 用户进一步澄清普通 Reconnect 不会触发问题，因此不拿该测试代替 Stop seat 后的新会话测试。
- 用户确认保存工作并授权后执行停止/重新启动。停止请求当时报告无可停止会话；随后实际新建 Session 11，18:05:01 ready。它与之前 Session 10 及宿主进程不同，符合“新子会话登录”的验收范围。
- 18:04:47～18:10:50 的独立进程采样持续记录原 Codex PID 53852 和创建时间未变，窗口内未出现新的 Codex 强制修复。新事件检查实际返回 `NO_FORCED_CODEX_REPAIR count=0`。
- 这是一次有界实测，电脑重启、后续 Codex 更新、长期反复新建会话仍为 UNTESTED。

收尾再次核对发现当前 Codex 主进程为 59556，创建于 18:14:11，晚于六分钟采样窗口。补取 18:00 之后的系统事件，18:14:08 旧容器被销毁，18:14:11 有新激活；未发现相同的强制修复注册事件。18:14:14 的 603 是带 DeferRemovalWhenPackagesAreInUse 的 CodexPrimaryRuntime 包移除，不是此前的 Codex 主程序包强制修复，不能混为同一种原因。是否为用户重开、更新或其他退出仍未证实。因此六分钟验收结果只适用于该窗口，整体状态仍是“缓解措施已做，重启根因尚未全部闭环”。补充证据为 `artifacts/codex-anode-restart-20261105-81e13acd/late-events-review.json`；同一严格检查输出 `NO_FORCED_CODEX_REPAIR count=0`，它不等于“没有任何进程重建”。

证据：`artifacts/codex-anode-restart-20261105-81e13acd/events.json`、`startup-before.json`、`startup-after.json`、`cold-seat-test-59ba74a5/verification.json`、`process-watch.jsonl`、`events-after-validated.json`。

进程观察脚本在零事件时曾把空集合导出为空文件，使 JSON 检查失败。原文件保留，已重新明确导出 JSON 空数组并通过同一严格检查，没有把空文件直接当作“无事件”。最初试用 WinRT 查询启动项时，普通 PowerShell 无包身份，返回 `0x80073D54`；该路线停止，改用 Windows 原生设置操作并回读。

## Windows 记住登录的真实状态

活动 v2 允许 Windows 提供保存复选框；用户已确认勾选并登录。随后 `cmdkey /list:TERMSRV/localhost` 的输出只在本机用于判断条目是否存在，证据只输出布尔值，没有获取密码、复制凭据或输出用户密码。

但 v2 普通 Reconnect 仍弹密码框，不能宣称免输成功；过程中还出现过一次 RDP 内部错误，随后用户登录成功，同一 Session 10 和宿主 60368 恢复。

进一步读取发现两个会持续请求交互的路径：`RdpViewer.ConfigureCredentialPrompt` 在记住模式下仍启用 PromptForCredsOnClient；`StartSeatAsync` 在 Stop seat 后重新复制首次 `--sign-in` 参数。v4 修正这两处，同时保留显式 Sign in 修复入口，默认模式不变。实现位置为 `work/anode-remember-source-118f620b/src/Anode/Daemon/RdpViewer.cs:215` 与 `Daemon/AnodeDaemon.cs:648`。

v4 路径：`work/anode-remember-login-20261105-118f620b/publish-v4/anode.exe`；SHA256：`cf96419e6fbf99927ec1f0dadedfc28b980b470e26e699486e0056fe8d18cd57`。

使用已有 .NET SDK 10.0.403 发布；COM 设置、16 种重连策略、首次强制登录后 Stop/Start 不再强制、旧登录行为、参数校验通过。输出 `REMEMBER_LOGIN_LOCAL_CHECKS_OK credentialReads=0 credentialWrites=0 connections=0`。与活动 v2 的 41 个 MCP 工具逐字段相等。没有运行会自动删除临时文件的整个 selftest --quick。

v4 的首次本地检查早于真实切换。候选构建证据在 `artifacts/anode-remember-login-20261105-118f620b/checks-v4.log`、`publish-v4.log`、`current-protocol-v4.json`。不能把本地 COM 检查当作机器已自动登录；后续真实结果见文末。

## Alpha 恢复到主桌面

本轮核对到 Alpha 主进程 221768 在 Session 11，由该会话 Explorer 28792 启动，127.0.0.1:5031 也归它。没有证据区分这次 Explorer 启动是登录启动项还是用户点击，因此只记录进程父子关系。

先关闭窗口确认仍有后台，再打开托盘菜单选“退出”。所有原版 Alpha 进程正常结束后，核验原 `app.asar` SHA256 未变，从原路径在主桌面 Session 1 启动，主进程为 216092，使用 `C:\Users\Zhang\AppData\Roaming\Alpha`。没有直接强杀、复制业务数据库或并发打开两份原版。

随后通过 Windows“设置→应用→启动”关闭 Alpha 登录启动项，避免以后同用户子会话登录时自动拉起原版；仍可从主桌面正常手动启动。**此开关也影响主桌面登录自动启动 Alpha。** 开关前后记录为 `alpha-startup-before.json`、`alpha-startup-after.json`。

收尾已从本机原路径 `C:\Program Files\Weixin\4.1.7\Weixin\Weixin.exe` 在现有 Session 11 打开微信，并进入既有账号；重新枚举后观察到微信主界面的导航控件，主进程 79080 位于 Session 11。没有重建 Anode、切换微信账号、输入密码或发送消息。Alpha 所有进程仍在 Session 1，监控及企微派发健康、进度推进；本次没有自然新增群消息，因此不把主界面恢复当作全链路到达时延验收。当前状态记录见 `artifacts/wechat-cx-rules-20261105-7c10ec0f/final-runtime.json`。

## 用户复核清单

- [ ] Windows 设置→应用→启动：ChatGPT、Alpha 应显示关闭。需要恢复登录自启动时可在此手动打开，但可能重新引入子会话启动。
- [ ] 主桌面 Alpha 可打开原有会话，Anode 不再有原版 Alpha 主窗口；微信本体应放在当前 Anode 子桌面。
- [ ] 下列历史后验检查输出 `NO_FORCED_CODEX_REPAIR`。它验证本次证据，不代表未来绝不复发。

```powershell
& "C:\Users\Zhang\AppData\Local\Programs\Python\Python312\python.exe" -X utf8 -B "F:\AI infrastructure\agent-Windows-operation\scripts\diagnostics\check_codex_anode_restart.py" "F:\AI infrastructure\agent-Windows-operation\artifacts\codex-anode-restart-20261105-81e13acd\cold-seat-test-59ba74a5\events-after-validated.json" --assert-no-repairs
```

- [ ] 如批准切换 v4，另验证普通重连及 Stop seat 后新建会话是否免输；任何仍然出现的密码窗口都视为未通过，不自动填入或读取密码。

## 22:39 自动登录重试及入口冲突修正

用户已明确批准退出 Session 11 并测试 v4。22:08:57 正常退出旧宿主，22:09:10 日志确认 Session 11 已签出；主桌面 Alpha 未随它退出。22:10:23 启动 v4，没有 `--sign-in`，允许 Windows 尝试保存凭据。仍出现 Windows 安全中心，未进入就绪会话，随后出现超时与 RDP 内部错误。用户后续明确更正：**人工登录正常，失败的是自动登录。**

尝试恢复上一版时，22:34:47 有旧统一版从 Windows 入口启动，占用了 main 控制管道；它使用默认 `C:\Users\Zhang\AppData\Local\Anode` 状态目录，且没有记住登录参数。日志只证明它从 Windows 打开，不断言是谁点击。实际查到任务栏 `Anode background Windows desktop.lnk` 仍指向该旧统一版；该宿主返回 disconnect reason 2055，其结果不能算 v4 的自动登录验收。

确认没有座席宿主后正常退出旧版，备份该快捷方式及清单到 `v4-live-switch-20261105-6ce30190/entry-conflict`，将任务栏入口指向 v4，补齐统一状态目录、1920×1080、共享剪贴板与 `--remember-login` 参数，并回读验证 `PINNED_ANODE_ENTRY_READBACK_OK`。全局 MCP 仍通过原固定桥读取活动清单，`ANODE_LOCAL_MCP_ENTRY_OK` 通过。

22:39:15 再次启动已核实的 v4，进程 226568，参数无 `--sign-in`。Windows 原生凭据窗口仍出现，22:41:46 命令等待就绪超时；状态为 connecting、session=null、agentReady=false。只检查到 `TERMSRV/localhost` 条目存在，没有读取密码。机器未加入域，未发现 CredentialsDelegation 显式拒绝策略，DeviceGuard SecurityServicesRunning 为 0；这些证据不能证明凭据有效，也不能证明策略就是原因。未修改 NLA、凭据委派或账户策略。

Microsoft 的 [AllowCredentialSaving 文档](https://learn.microsoft.com/en-us/windows/win32/termserv/imsrdpclientnonscriptable4-allowcredentialsaving) 说明该开关提供保存复选框，不能仅凭它推定自动复用成功。实现 `work/anode-remember-source-118f620b/src/Anode/Daemon/RdpViewer.cs:215` 已区分允许保存、允许必要时提示与显式强制提示；`AnodeDaemon.cs:648` 则避免记住模式在 Stop/Start 后重新强制提示。两处本地回归通过，实际自动启动仍未通过。

## Codex 15 分钟观察：已复现重启

不依赖 Codex 存续的独立 PowerShell 观察器，身份核实为无 Codex 包身份，22:08:45～22:23:48 共采样 208 次。22:21:51 原进程 59556 消失，新主进程 230704 创建，输出 `OBSERVED_CODEX_RESTART`。这是用户实际症状的直接证据，推翻“只因没有强制包修复事件就算通过”的推断。

该窗口只捕获 AppModel 容器销毁和新激活，没有此前 603 强制修复注册，也没有对应 Application 崩溃/挂起事件。旧 Codex 日志持续出现 `primary_runtime_restore_failed`，最早有插件缓存 rename 的 EPERM，之后重复同步失败。只读查看当前包内 `early-bootstrap-632-62c641245a52ef0e.js:1180`，相关 catch 只记录警告并按 30 秒重试，未能证明它直接触发重启。因此仍保留“外部激活／其他退出／运行时恢复故障”的待核查边界，不修改 Codex 包或缓存来碰运气。

严格区分两个检查：`scripts/diagnostics/check_codex_anode_restart.py:6` 只识别强制包修复；新增 `scripts/diagnostics/check_codex_process_continuity.py:7` 识别观察窗口中原进程是否消失。后者对本次数据实际返回 1 / `OBSERVED_CODEX_RESTART`，对未受影响的短测返回 0，红绿识别能力已验证；没有削弱旧检查。

本轮证据根为 `artifacts/anode-remember-login-20261105-118f620b/v4-live-switch-20261105-6ce30190`：`verification.json`、`process-watch.jsonl`、`events-after.json`、`watcher-identity.json`、`v4-local-checks.log`。观察脚本为 `scripts/diagnostics/Watch-CodexAnodeTransition.ps1:29`。运行命令：

```powershell
& "C:\Users\Zhang\AppData\Local\Programs\Python\Python312\python.exe" -X utf8 -B "F:\AI infrastructure\agent-Windows-operation\scripts\diagnostics\check_codex_process_continuity.py" "F:\AI infrastructure\agent-Windows-operation\artifacts\anode-remember-login-20261105-118f620b\v4-live-switch-20261105-6ce30190\process-watch.jsonl"
```

预期返回 1 并输出 `OBSERVED_CODEX_RESTART`，表示诊断正确检测到未解决的问题。不能把这个预期红灯说成修复通过。
