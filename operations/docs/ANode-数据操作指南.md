# Anode 数据操作指南（本机双仓库版）

> 适用环境：Windows 11；Anode 本地分支 `codex/local-current`；Ghost 备用分支 `local/current`。编写与复核日期：2026-11-06。
>
> 本指南说明如何让 Agent 在 Anode 子会话中安全地访问和处理原有业务数据。**指南不是对删除、结束用户会话、共享数据库并发写入或发送消息的新授权。** 最新路径以活动清单和 Git 状态为准；历史文档中的 `work/anode-fork`、旧 `scripts/` 等路径不是当前入口。

## 一、数据与桌面隔离的边界

Anode 使用独立 Windows 子会话：子会话有自己的窗口、焦点、键盘输入队列和桌面进程，但仍以当前 Windows 用户身份访问文件系统和用户配置。**隔离的是桌面操作，并非自动隔离应用的业务数据库。** 两边同账号登录、看到相同会话，不能单独证明读写的是同一份数据。

| 数据层 | 核验要求 | 不可据此推断 |
| --- | --- | --- |
| GUI/进程 | 先确认主桌面和子会话中已有的实例、所属 Session 与前台工作状态 | 主桌面没看到窗口 ≠ 进程不存在 |
| 运行配置 | 核对每个启动器的实际参数、环境变量和独立 `user-data-dir` | 独立运行目录 ≠ 独立业务数据 |
| 业务数据 | 核对原始绝对路径、真实文件身份、后端 API 或官方同步机制 | 复制登录状态 ≠ 数据实时一致 |
| 并发写入 | 只有已验证应用明确支持两端并发写入时才允许 | 共用目录 ≠ 支持同时打开两个写入实例 |
| 历史证据 | 记录检查时间、原始文件与结果；不复写旧报告 | 旧测试通过 ≠ 新版本或新会话已验收 |

## 二、当前仓库和运行入口

```text
F:\AI infrastructure\agent-Windows-operation\
├── AGENTS.md / README.md       总览与本机操作规则
├── anode\                      Anode 独立 Git 仓库
│   ├── LOCAL.md
│   ├── src\ / docs\ / scripts\   上游源码和文档
│   ├── tools\                 本机 .NET SDK（不提交）
│   └── operations\
│       ├── config\             活动清单和业务策略
│       ├── scripts\            本机 MCP / 业务脚本
│       ├── launchers\          已核实的入口
│       ├── docs\               本机使用说明
│       ├── work\               活动程序与应用运行数据（不提交）
│       └── artifacts\          历史证据与运行状态（不提交）
└── ghost\                      Ghost 独立 Git 仓库
    └── local\                  便携程序、入口、诊断与证据
```

- Anode 唯一活动清单：`anode/operations/config/anode-local-mcp.json`。程序产品版本 `0.11.3`；本地发布标签 `local-2026.11.06.1`。
- 本机 MCP stdio 入口：`anode/operations/scripts/anode_local_mcp.py`。**连接/发现 MCP 不会自动把其他 Computer Use 工具转发到子桌面。** 使用 Anode 的 `seat_*` 操作子会话。
- Ghost 本机运行状态与校验入口：`ghost/local/config/runtime.json`、`ghost/local/scripts/Test-GhostProject.ps1`；源码标签 `local-v0.23.4-2026.11.06.1`。
- Codex 本地项目的 `anode` 与 `ghost` 目录由用户自行添加；不得直接更改 Codex 运行中的项目注册数据。

## 三、操作前：只读预检

在 PowerShell 中执行；下列命令只检查配置和已有状态，不创建子会话，也不获取桌面控制权：

```powershell
$root = 'F:\AI infrastructure\agent-Windows-operation'
$anode = Join-Path $root 'anode'
$ops = Join-Path $anode 'operations'
$cfg = Get-Content -LiteralPath (Join-Path $ops 'config\anode-local-mcp.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$exe = Join-Path $ops $cfg.executable

python.exe -B (Join-Path $ops 'scripts\anode_local_mcp.py') --check
& $exe --channel $cfg.channel status --json | Out-Host
powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ops 'scripts\Test-ProjectSetup.ps1')
powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'ghost\local\scripts\Test-GhostProject.ps1')
git -C $anode status --short --branch
git -C (Join-Path $root 'ghost') status --short --branch
```

检查 `state`、`session`、`agentReady`、`lastError`、`viewOnly`、`lease`，以及程序路径是否与清单一致。`ready` 只说明已有子会话可用，不能证明目标应用的数据正确；`reused` 只说明 Windows 凭据被复用，不代表跨用户共享或 Windows 重启后必定无提示。

2026-11-06 本轮只读复核：Anode `ready`、Session 16、宿主 PID 25724、`rememberedLogin.state=reused`、`signInPrompt=false`、`lastError=null`、`viewerVisible=false`，且无活动桌面租约；原生 MCP 重新发现 41 个工具。**这些 ID 与运行状态只对采样时刻有效，不应作为脚本常量。**

## 四、Agent 操作原有业务数据的标准流程

1. **先查主桌面。** 识别原有窗口、进程、Session、是否有未保存草稿；再查询 Anode `seat_status`。看不到应用窗口时仍要检查后台实例，不能直接启动第二套。
2. **确定唯一的业务数据来源。** 获取官方应用的数据路径、当前版本、启动器参数、配置映射或现有后端。记录“应用本体运行目录”和“业务数据目录”各自的绝对路径；必要时验证文件身份或只读回读。
3. **选读写模式。** 若原应用在主桌面运行且有受支持的只读 API/代理，优先只读副窗口。若必须操作原版 GUI，则先保存并正常关闭原实例，再在子会话使用验证过的入口；未验证并发写入时禁止两端同时写同一数据库。
4. **进入现有子会话。** 先 `seat_status`，确认就绪；取得当前任务授权后才调用 `seat_lease` 的 `acquire`。注意：在会话停止时，`seat_lease acquire` **可能隐式启动新子会话**；本项目默认不得为恢复 Anode 优先级自行启动。
5. **观察 → 执行 → 回读。** 使用 `seat_windows` / `seat_observe` 取得新快照，优先通过 `seat_element` 操作被识别的控件；等待用 `seat_wait`。窗口或快照变化后重新观察，不重复提交超时或结果未知的输入。截图缩放时按工具返回的原始屏幕坐标语义处理，不用缩略图像素直接点击。
6. **验证数据一致性。** 保存后用原应用或只读后端回读相同业务对象；按操作类型核对内容、时间、对象 ID 或文件哈希。对于不支持并发的应用，应顺序切换并核实读回结果，不把一次成功登录当作一致性证明。
7. **收尾。** 关闭自己创建的测试窗口、清理本次工具作业、释放租约；保留已有子会话和其他用户应用。停止整个 seat 会结束该子会话内的应用，仅在本次任务明确允许时执行。

### 操作决策表

| 现场情况 | 推荐路径 | 必须停止的情况 |
| --- | --- | --- |
| 应用未运行，数据目录和版本已核实 | 已验证的子会话启动器 | 入口指向未知配置或版本 |
| 主桌面应用正在运行、只需阅读 | 已验证的只读官方 API / 本机代理 | 代理不可用、需要直接打开被锁定数据库 |
| 主桌面已打开、需修改同一业务数据 | 保存并正常退出主实例，随后顺序操作和双端回读 | 未保存工作、实例身份不明、写入锁或同步语义未知 |
| 任务涉及发送、删除、覆盖或付款 | 核实对象、完整操作范围和本次授权后执行 | 目标不明确、操作结果未知、授权未覆盖 |
| Anode 不可用 | 核验 Ghost 固定入口，再评估 Computer Use | 回退渠道指向主桌面且会和用户输入争抢 |

## 五、本机已有应用案例

### WorkBuddy：共享业务数据，隔离 Electron 运行锁

原有业务配置在 `C:\Users\Zhang\.workbuddy`。历史验证使用独立 Electron 运行目录与共享业务配置，避免两个窗口争用同一运行锁。启动参数、应用版本和现用目录以 `operations/config/verified-desktop.json` 与配套启动器为准。2026-10-31/11-03 的验证记录不能替代本次迁移后针对 WorkBuddy 的实时并发读写验收；升级应用后重新核对路径和行为。

### Alpha：原主桌面实例 + 只读子窗口

已验证过的模式是主桌面运行原 Alpha 后端（`127.0.0.1:5031`），子会话通过本机只读代理 `127.0.0.1:5032` 读取账号、会话、消息和搜索结果；浏览器不接收原令牌，副窗口不直接打开业务 SQLite。该代理只提供受限 GET，不能被当成原 Alpha 的完整 GUI 或发送接口。需要写入/发送时操作原实例并遵守授权边界。

参考：[Alpha 只读副窗口记录](Alpha-共享数据只读副窗口-20261104.md)。

### 微信关键词提醒、企微转发和消息看板

常驻监控、企微转发和看板已经保留原状态、去重记录及路径映射；**不得为测试另起一个监控采集器、重置进度或重发旧批次**。看板 `http://127.0.0.1:5033/` 只读原 SQLite 数据和历史报告，系统 `shown` 不是用户已阅读。群消息及命中提醒不能自动授权发送微信；发送目标、正文和通道必须按项目现行策略另行核对。

参考：[看板文档](微信群消息看板-20261105.md)、[迁移验收](双仓库迁移验收-20261106.md) 和父目录 `AGENTS.md`。

## 六、证据、回退和权限

- 代码版本由两个独立 Git 仓库、提交与标签管理；生产程序由活动清单选择。`git reset --hard`、覆盖数据库和重新建立监控基线均不属于通常回退。
- 源码、日志、状态、凭据和业务库属于不同层。**切换 Git 标签不会自动恢复 Windows 计划任务、快捷方式、运行程序、用户凭据或监控记录。** 先核对当前会话及备份清单，保存用户工作，再评估版本切换。
- 私密登录凭据保留在 Windows Credential Manager；不在命令行、Git、报告或聊天里导出。截图和业务消息可能包含敏感数据，默认留在 `operations/artifacts/`，不自行上传。
- 任意测试记录至少说明：时间、Session/PID（仅当次）、活动程序 SHA/提交、操作是否写数据或发送输入、实际回读、失败与 `UNTESTED` 项。超时或反馈不确定时不盲目重放。
- 不删除旧发布、恢复材料或运行数据。历史源代码和恢复材料在 `operations/artifacts/restructure-20261106/`；完整列表及当前状态见[双仓库迁移验收](双仓库迁移验收-20261106.md)。

## 七、2026-11-06 续验结论及剩余边界

**本次通过（新采样）：** `PROJECT_SETUP_OK`（Anode PowerShell/Python、4 个启动器、4 个脚本哈希、191 个历史文件）；`GHOST_PROJECT_SETUP_OK`（285 迁移文件、3 运行程序哈希、版本/doctor、54 个 MCP 工具且窗口只读发现）；两仓库 `git fsck --full` / `git diff --check` 均为 exit 0；活动 Anode `status --json` 及 MCP `--check` 为 exit 0；原生 Anode MCP `tools/list` 确认 41 个工具。两仓库在检查时均无未提交改动。

**仍未通过：** 迁移后连续三次真实工具栏 Reconnect 的输入保护验收。历史 `live-anode/toolbar-uia/1-action.json` 表明 UIA 目标唯一且 `invoked=true`，但 `focus_preserved=false`、`cursor_preserved=false`；`summary.json` 是 `TOOLBAR_RECONNECT_FAIL`。既有两次采样不能确定由 UIA 还是用户同步操作导致。**不自动重放此真实主桌面输入动作**；本机输入法/焦点冲突任务仍按此前要求暂停。

**UNTESTED：** Ghost Rust 重新编译（本机未找到 `cargo`/`rustc`）；每款第三方应用的迁移后并发业务数据写入；Windows 重启后的凭据恢复；全天稳定性；真实微信新消息在线状态。此前某次 v6 三轮重连成功，不等于这轮迁移后的焦点保护合格。

迁移前已通过的隔离测试（Anode 桌面 15 项、预览 15 项、Ghost GUI 中文输入/点击）保留原始证据，但本轮只读检查没有重放它们。

## 八、参考（当前有效）

- [上级 AGENTS 规则](../../../AGENTS.md) / [本仓库 LOCAL](../../LOCAL.md)
- [双仓库迁移验收](双仓库迁移验收-20261106.md)
- [上游 Agent/MCP 工具说明](../../docs/CONNECTING-AGENTS.md)
- [Anode 截图单窗口预览](Anode-截图单窗口预览-20261106.md)
- [Anode 连续重连排查与候选](Anode-连续重连排查与v6候选-20261106.md)（历史路径需按本指南转换）

**停止点：** 要求真正三轮工具栏重连验收之前，先排除同期用户输入和窗口激活冲突，并确认允许触发真实 Reconnect；不得把本次只读、构建或隔离窗口检查冒充该项通过。
