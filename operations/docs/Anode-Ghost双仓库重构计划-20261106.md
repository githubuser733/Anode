# Anode / Ghost 双仓库重构 Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` in this chat, task by task. This plan replaces the earlier `项目目录收敛计划-20261106.md`. Preserve all existing user authorizations and file-deletion rules.

**Goal:** 将 `F:\AI infrastructure\agent-Windows-operation` 作为总览目录，建立独立的 `anode`、`ghost` Git 仓库，并在同一 Codex 本地项目中使用；迁移后分别进行实际功能验收。

**Architecture:** 父目录不建总 Git 仓库。Anode 源码放在 `anode` 根目录，保留既有提交、分支和当前未提交功能；配套的本机自动化放入 `anode/operations`，与上游源码的 `scripts`、`docs` 分开。Ghost 源码放在 `ghost` 根目录，固定当前 0.23.4 基线；本机程序、入口和证据放入 `ghost/local`。运行数据与凭据不提交 Git。

**Tech Stack:** Windows 11、Git、.NET 10、Python 3.12、PowerShell、Ghost 0.23.4/Rust、MCP stdio、Windows 计划任务、Codex 多文件夹本地项目。

**Spec:** 本次用户请求（2026-11-06）：“父目录作为简单标签聚合文件夹；anode、ghost 分别建立 Git；加入 agent-1-Windows-operation-后台操作系统；交付必须实测两个项目功能”；父目录 `AGENTS.md` 的保护规则继续有效。

## Global Constraints

- 根目录固定为 `F:\AI infrastructure\agent-Windows-operation`；迁移前后核对绝对路径，不触及父目录或盘符根目录。
- 不删除任何文件，不执行永久覆盖、Git hard reset、强制 push；普通编辑先保留可恢复副本。若最终确需回收冗余副本，另列完整路径、数量和大小，取得该清单对应的 `1989`。
- 不关闭用户应用、不结束 Anode 子会话、不搬动仍有运行进程的程序目录。当前 `seat_status` 为 stopped；正式验收启动新子桌面属于本次明确的功能验证，不借机启动其他业务应用。
- 现用微信采集、看板和企微转发的数据库、进度、策略、已发送记录、停用状态必须保持；不手工读取群消息、不新增发送测试。
- Windows 凭据保留在原生凭据存储，不读取或导出密码。Alpha、WorkBuddy 和企业微信的外部业务配置不迁移。
- 父目录只承担总览和规则；两个子仓库分别提交、打标签，不自动上传 GitHub。
- 所有通过结论必须有本次执行证据。无法执行的桌面、重连或源码构建检查明确为 UNTESTED，不替换为路径检查。

## 已核实的能力和基线

- 官方文档确认：本地项目可以添加多个文件夹；Review 可展示多个仓库。主目录决定新聊天默认 cwd、Git/PR/worktree 和 AGENTS.md/config/skills 自动发现；附加目录仍可搜索、读取和修改。并不是自动把全部源码塞进每次上下文。来源：https://learn.chatgpt.com/docs/projects#use-local-projects-for-folders-and-codebases
- 本机 Codex `26.1029.9216.0`；目标项目 ID `27bb1107-5c6e-49b9-8dc9-68300320e14b` 当前只挂父目录。同机另一项目已保存两个 rootPaths，支持能力与本机配置一致。项目工具只有读取/组织能力，没有新增目录接口；执行时使用应用的“编辑项目”，不直接修改运行中的内部状态文件。
- Anode 原 Git 主库在 `work/anode-fork`，当前有效源码在其 linked worktree `work/anode-remember-source-118f620b`；基础提交 `f3c18607eae740dc38fdf4d98a1965cd0f33df7a`，有 10 个 tracked 修改和 5 个新文件。Timeline、文本增强及响应优化已在提交链内，记住登录/重连/单窗截图仍未提交。证据：本次 git status、worktree list、diff --check；版本与源码配置见 `config/anode-local-mcp.json:5`。
- Anode 现用程序 SHA256 `c001edaf1de970e0e1936113ac534268bc3e28d7dfb3876de9fd1a87e4532fc8`；本次两个 cwd 的 MCP 实测均返回 41 工具，扩展文本参数存在，seat_status=stopped；结果 `artifacts/anode-local-mcp-check-20261106-184938-fa3f76/result.json`。
- Ghost 源码是无 .git 的解压目录。`Cargo.toml:23` 为 0.23.4，已通过 GitHub 连接器核对上游 `NORTHTEKDevs/ghost` 的 `v0.23.4/Cargo.toml`。本次 285 文件/42,794,153 字节哈希一致；doctor 全部必需项通过，仅可选 vision key 警告。
- Ghost MCP 本次 initialize/tools/list/实际窗口列表调用通过：54 工具、serverInfo=0.23.4、EOF 正常退出；无键鼠输入。证据 `artifacts/project-restructure-preflight-20261106/ghost-mcp-readonly.json`。
- `Test-ProjectSetup.ps1` 本次返回 `PROJECT_SETUP_OK`；当前还有常驻监控、看板、转发和多个 Codex 管理的 Anode MCP 客户端引用旧位置。它们的路径必须迁移，不能只移动源码。
- 当前 PATH 与标准 `.cargo` 位置未发现 Rust 工具链；不能承诺 Ghost 源码构建已通过。先用原始已校验二进制验收实际功能，源码构建条件另列。

## 目标目录

```text
agent-Windows-operation/
  AGENTS.md                    共用规则和子项目路由
  README.md                    总览、版本、入口、验收命令
  anode/
    .git/                      原仓库完整历史
    src/ Anode.sln ...         当前有效源码
    scripts/ docs/ ...         上游及 Anode 源码自身的工具/文档
    operations/
      scripts/ config/         本机集成、监控、MCP 入口（审查后纳入 Git）
      launchers/ docs/         日常入口和本机说明
      work/                    现用运行包/应用数据，Git 忽略
      artifacts/               状态、报告、历史证据/恢复材料，Git 忽略
    tools/                     本机 SDK、NuGet 缓存，Git 忽略
  ghost/
    .git/ Cargo.toml crates/ ...
    local/
      runtime/                 原 0.23.4 三个二进制，Git 忽略
      scripts/ config/ launchers/
      artifacts/ archives/     证据/原始下载包，Git 忽略
```

这里先保留 Anode 配套系统的 `work/artifacts` 内部结构，避免为目录美观修改微信数据库行为；它们与源码和 SDK 已分开。旧历史证据原文不改写，当前使用入口改写为新路径。源码只用 Git 管理版本，今后不再建立日期版源码目录。

## Review Focus

1. linked worktree 的 .git 是指向主库的文件；仅搬 .git 文件会断链。Task 1 验证全部 refs、15 文件哈希及 `git fsck`。
2. 运行中的 stdio 子进程占用旧 exe；Task 3 必须通过正常退出/客户端重载释放，不能强杀或留下两份活动发布路径。
3. 任务/快捷方式/数据库报告记录含绝对路径；Task 3 按映射核对活跃入口与报告下载，不能只检查文件存在。
4. 数据目录可能含 junction；Task 3 清单只记录链接本身及目标，不递归搬动外部数据，不重新建立采集基线。
5. Codex “主目录”不等于汇总目录；Task 4 回读三个目录和主目录，验证两个 Git 仓库改动入口，防止只添加了普通标签。

## Task 1：Anode Git 收敛

**Files:** 当前两套 `work/anode-*` 源码 → `anode/`；迁移记录 → `anode/operations/artifacts/restructure-20261106/`。

- [ ] 保存 15 文件快照、Git diff、全部 refs、现用配置/快捷方式/任务定义；秘密值不进入输出。
- [ ] 将现有已验证功能提交到本地分支，并记录提交前后源码 SHA256；不合并上游新版本。
- [ ] 迁移原主库，修复 worktree 关联；根目录得到真正的 `.git`，在固定开发分支使用当前完整源码。过渡副本作为恢复材料集中保留，不冒充第二个活动版本。
- [ ] 执行 `git fsck --full`、`git diff --check`、全 refs 与 15 文件核对；从新目录构建到新的受控输出，执行 Timeline/文本/凭据/截图的专用检查。
- [ ] 建立本地版本标签和发布记录，关联源码提交、二进制 SHA256、测试证据；不声称旧二进制包含后加的版本字符串。

## Task 2：Ghost Git 和固定入口

**Files:** 解压源码 → `ghost/`；便携程序 → `ghost/local/runtime/`；原 Ghost 启动/验证脚本 → `ghost/local/scripts/`，配置和入口分别放 `config/`、`launchers/`。

- [ ] 对照上游 `v0.23.4` 建立 Git 基线，核对原解压源码差异；保留已有本地修改，不升级版本。
- [ ] 迁移原二进制及原始下载材料，重新生成逐文件位置映射；SHA256 与 285 文件原清单逐项匹配，不能删掉校验来使测试通过。
- [ ] 固定背景策略 `GHOST_FOCUS_POLICY=background`、`GHOST_FOCUS_LOCK=on`，更新 CLI/MCP/doctor 入口。
- [ ] 新路径运行版本/doctor、stdio initialize/tools/list/只读窗口发现；再对专用测试窗执行后台文本操作和回读，验证前台焦点未变化，正常关闭测试窗。
- [ ] 本地提交/标签；源码编译若工具链未就绪，报告 UNTESTED，不能混称为执行二进制验收。

## Task 3：配套系统、MCP 和快捷方式迁移

**Files:** 根 `scripts/config/docs/launchers/work/artifacts/README.txt/.gitignore` → `anode/operations/`（Task 1/2 已迁出的部分除外）；`work/deps` → `anode/tools`；用户级 Anode MCP 配置只改固定入口路径。

- [ ] 导出路径映射，扫描任务、`.lnk`、活跃配置和脚本引用；验证移动目标不越界且无运行应用占用。
- [ ] 保存监控/看板/转发的原 enabled 状态，正常暂停自己的常驻服务，等待退出，校验数据库检查点与发送去重状态；不强杀用户应用。
- [ ] 处理 Codex 管理的 MCP 旧连接，正常释放后迁移程序；无法正常释放则保留当前程序原位并明确阻塞，不能违规则强行移动。
- [ ] 迁移目录并修改活跃入口；备份后更新桌面、开始菜单和任务栏中属于本项目的快捷方式，保留其他软件入口。
- [ ] 更新计划任务路径，保留原触发器/身份/启用状态；旧测试任务保持非生产，不新增监控副本。
- [ ] 新入口跑 `Test-ProjectSetup.ps1`、MCP 双 cwd 检查、三个服务 Status；恢复原来启用的服务，核对检查点推进和单实例，验证看板健康及旧报告读取。

## Task 4：Codex 项目和总览

**Files:** 父 `README.md`、`AGENTS.md` 路由补记；两个子仓库 Git ignore 与维护说明。

- [ ] 在项目“编辑项目”加入 `anode` 和 `ghost`，保留父目录，设 `anode` 为主目录；项目名称/已有聊天不变。
- [ ] 从应用回读目录列表及主目录，核实本机 Git 识别；不能用手工改内部 JSON 代替应用保存。
- [ ] 父 README 给出两个 Git 状态命令、统一入口和版本表；明确 Ghost 的目录规则需按任务读取，附加目录不会自动注入所有上下文。
- [ ] Git 跟踪源码、脚本、模板和说明；逐项检查拟提交清单，忽略运行日志/数据库/浏览器档案/下载包/密钥，不自动 push。

## Task 5：最终实机验收与交付

- [ ] Anode：在新路径启动已校验程序，核对 ready/session/host path、原生已保存凭据复用；若出现安全窗口，等待用户原生输入，不能代填密码。
- [ ] Anode：专用子桌面测试窗完成启动、UIA 观察、输入/点击、回读和截图；验证 Timeline 和单窗预览左右切换。关闭自己创建的测试窗、释放租约，保留用户会话。
- [ ] Anode：同一会话连续三次普通重连，核对同一 session、真实错误状态和 Codex 进程连续性；不做 Stop seat，不宣称历史间歇重启问题已全部解决。
- [ ] Ghost：新固定入口实际操作测试窗并回读；MCP 只读调用与正常退出通过，背景策略不抢焦点。
- [ ] Git：两库 `status`、`fsck`、标签、提交日志可核对；全局 MCP、新快捷方式、任务栏、计划任务均回读新位置。
- [ ] 输出迁移映射、真实测试结果、失败/UNTESTED 项及回退步骤。任何必要检查失败都不标记“两个项目功能没有问题”。

## 用户复核

迁移完成后在 Codex 的项目“编辑项目”应看到三个目录，主目录为 `anode`；新聊天能按需读取 `ghost`。在 PowerShell 分别运行 `git -C "F:\AI infrastructure\agent-Windows-operation\anode" status` 和对应 `ghost` 命令，应识别两个独立仓库。正式验收报告将给出新快捷方式、测试输出和日志位置。

计划自检：已覆盖两仓库、父总览、Codex 多目录、历史功能保全、运行入口迁移、数据/凭据保护及真实桌面验收。当前只完成读取与基线检查；目录迁移、项目目录添加与新路径实机验收尚未执行。

## 执行更新（2026-11-06）

Task 1/2/3 的目录、Git 基线、运行入口、配套服务迁移和核心功能验证已执行。Task 4 按用户最新指示改为用户自行添加目录，助手未修改项目设置。Task 5 中 Anode 冷启动凭据复用、15 项桌面操作、15 项截图预览、Ghost 隐藏桌面中文输入/点击回读通过；三次连续工具栏重连被输入保护中止（并非密码拒绝），保持未通过，不重试；Rust 源码构建因工具链未安装为 UNTESTED。最终可核查结果以《双仓库迁移验收-20261106.md》为准。
