# TRAE 默认 Agent 与本机配置复用

当前入口仍为 `launchers/TRAE-CN-In-Anode.lnk`，需在 Anode 子会话内使用。2026-10-31 已实测冷启动为 Agent、再次点击恢复 Agent，并读取本机已有的 Project Configuration、Read and display file content 等任务及对话。

配置结构：

| 内容 | 当前方式 |
|---|---|
| 窗口/进程运行目录 | 独立 `work/multiopen/trae-cn-shared-agent/profile`，避开主桌面单实例锁 |
| 模型、MCP、快捷键等用户配置 | 每次点击入口读取 `C:\Users\Zhang\AppData\Roaming\Trae CN\User` 中对应文件，刷新到子会话；主文件不由脚本改写 |
| 默认模式 | 子会话覆盖 `window.startupWindowType` 为 `agent`；移除绕过该偏好的 `--new-window` 参数 |
| 任务与对话数据 | 子会话 `profile/ModularData` 以 Junction 共用主桌面的 `ModularData` |
| Agent 资源 | 子会话 `profile/solo-lite` 以 Junction 共用主目录的 `solo-lite` |
| 扩展 | 共用 `C:\Users\Zhang\.trae-cn\extensions` |
| 初始身份状态 | 从主配置复制 `Local State`、machineid、globalStorage 等必要状态，账户数据库用 SQLite backup；没有复制正在使用的 Cookies 数据库 |

“配置复用”包括启动时刷新设置和直接共用业务数据；不承诺主桌面改设置后子会话立刻热更新。若在子会话手工修改这些配置，下次点击入口仍以主配置为准，替换前会保留并校验子会话旧文件，备份在该运行目录的 `config-backups`。

业务数据指向同一文件已通过 `Path.samefile()` 验证，原有任务也在 TRAE 原生界面中可见。任务页的实时刷新策略、双端长时间并发写入和账户切换尚未单独测试；本次没有发送新的模型请求。

冷启动测试：通过正常窗口关闭结束本次新建的测试实例，再由原 `.lnk` 启动；最终进程为 PID 8504、Session 8，返回：

```json
{"windowTitle":"Agent","agentWindowVerified":true,"exitedEarly":false}
```

最小化后再次点击同一入口：恢复同一 PID 8504，记录为 `alreadyRunning: true`、`launchPerformed: false`、`restored: true`、`foreground: true`、`agentWindowVerified: true`。主桌面的 TRAE PID 127528 仍在运行；Anode Session 8 未重启。

源码核对依据（安装版本 3.4.11）：

- 安装包 `out/main.js:49` 的启动模式选择支持 `agent`；`out/vs/workbench/workbench.desktop.main.js:3868` 的配置定义允许 `window.startupWindowType` 为 `system/agent/ide`。
- 安装包 `out/main.js:2637` 的 `openFirstWindow` 在 `--new-window` 分支提前返回，因此该参数会绕过启动偏好；当前入口已去掉它。
- 本项目 `scripts/Start-AppInAnode.ps1:89` 按当前子会话中的进程及标题查找 Agent 窗口，第 137 行等待冷启动 Agent 就绪，避免把加载中的 TRAE CN 临时标题当成最终模式。
- 本项目 `scripts/Prepare-MultiopenProfile.py:12` 读取并同步主配置，叠加子会话默认模式，替换旧文件前保存并核验备份。
- 本项目 `config/multiopen-apps.json:21` 保存 Agent 覆盖项和共享目录参数。

任务数据库不能被标准 Python sqlite3 直接识别，曾返回 `file is not a database`。没有用该工具修改任务数据库；共享验证依赖文件身份和 TRAE 原生界面。账号状态副本 `state.vscdb` 的只读 quick_check 通过。

验证命令（项目目录 PowerShell）：

```powershell
.\scripts\Test-MultiopenLaunchers.ps1 -Live
.\scripts\Test-ProjectSetup.ps1
```

本次实际输出包含 `TRAE_AGENT_MAIN_CONFIG_AND_SHARED_DATA_OK`、`MULTIOPEN_SETUP_OK` 和 `PROJECT_SETUP_OK`。三个软件的恢复检查均通过。

证据位于 `artifacts/trae-agent-shared-20261031/`。其中 `protected-backups` 保留改动前的配置、脚本，以及主配置和业务数据库的原始文件快照；活跃数据库文件快照不宣称事务一致性。旧独立测试目录及其窗口保留，旧 Agent 窗口已收起；当前快捷方式只选择新的共享运行目录。
