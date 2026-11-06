# Alpha 与 Codex 分组修复

Alpha 已恢复独立进程身份，原数据连接可读；任务栏视觉分组还需要用户核对。上次从 Codex 终端直接打开 Alpha 时带入了 Codex 包身份，是代理启动方式的问题。桌面、开始菜单与任务栏三个 Alpha 快捷方式的目标均仍是原 Alpha.exe，没有被改成 Codex。

## 证据与处理

- 修复前，`scripts/diagnostics/check_process_app_identity.py:11` 对 Alpha 主进程 216092 调用只读 Windows 身份查询，得到 `OpenAI.Codex_26.1029.7803.0_x64__2p2nqsd0c76g0` 和 `OpenAI.Codex_2p2nqsd0c76g0!App`；`--assert-not-codex` 实际返回 1。
- 单纯 `New-Object -ComObject Shell.Application` 再 ShellExecute 的探针仍继承该身份，没有继续使用它作为修复方法。由已有 Windows Explorer 打开 .lnk 的探针，以及借用现有桌面对象 `Document.Application.ShellExecute` 的探针，均返回 package=null、applicationId=null。只启动无界面探针，没有打开第二份 Alpha 数据后台。
- 用户确认保存并允许正常退出、独立重开后，核对到旧 Alpha 进程已结束，新主进程 233812 位于主桌面 Session 1，原 Alpha 主界面显示已有账号、数据已连接。没有调用强杀；不凭父进程已经消失推断具体退出者。
- 修复后同一只读身份检查返回 0：`codexIdentityPids=[]`。Alpha 子进程参数仍明确使用 `C:\Users\Zhang\AppData\Roaming\Alpha`；原 app.asar SHA256 与 `config/alpha-shared.json` 一致，5031 归新主进程。未修改业务配置、Codex API 桥、令牌、数据库或用户目录。
- 三个快捷方式事前备份，并复核 SHA256 保持一致。因此没有重写本来正确的 Alpha 入口。以后代理启动主桌面 Alpha，使用已验证的现有桌面 Shell 或用户在资源管理器打开原快捷方式，避免直接作为 Codex 子进程启动。

Windows 使用应用身份与窗口属性关联任务栏分组，参考 [AppUserModelID](https://learn.microsoft.com/en-us/windows/win32/shell/appids)。身份修正证据不单独等于用户眼中的任务栏位置、固定图标与跳转列表全部验收通过。

证据：`artifacts/alpha-shortcut-repair-20261105-6ce30190`，含前后进程身份、探针结果、`shortcuts-before.json` 与三个原 .lnk 的备份。原文与配置不复制到其他数据目录。

## 用户复核清单

- [ ] 点击桌面原 `Alpha.lnk`，应显示 Alpha 原界面和原账号，任务栏应与 Codex 分开。
- [ ] 执行下列命令，应输出 `codexIdentityPids: []` 且退出码 0；进程号运行时查询，不照抄旧 PID。

```powershell
$alphaMain = Get-CimInstance Win32_Process -Filter "Name='Alpha.exe'" | Where-Object { $_.CommandLine -notmatch '--type=' }
& "C:\Users\Zhang\AppData\Local\Programs\Python\Python312\python.exe" -X utf8 -B "F:\AI infrastructure\agent-Windows-operation\scripts\diagnostics\check_process_app_identity.py" $alphaMain.ProcessId --assert-not-codex
```

- [ ] 看板 <http://127.0.0.1:5033/#hits> 中读取和企微转发应恢复健康，历史条数保持，不能因重开而重发旧命中。

仍未验收：真实任务栏人工确认、以后软件升级与 Windows 登录后的重复表现。没有为了验证分组发送消息或修改业务数据。
