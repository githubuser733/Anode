# 本机维护入口

先读取上级 `../AGENTS.md`。本仓库保留 Anode 上游历史及本机 Timeline、文本增强、监控、登录、重连、截图修改；当前分支 `codex/local-current`。

本次入口整理快照：`local-2026.11.07.1`；上一迁移快照：`local-2026.11.06.1`。这些是源码与配套工具的 Git 标签，活动程序的原始构建提交仍以运行清单为准。

- `operations/scripts`、`operations/config`、`operations/launchers`、`operations/docs`：原父目录的配套系统，纳入 Git。
- `operations/work`：当前程序和应用运行数据；`operations/artifacts`：监控数据库、报告、历史证据、恢复副本；`tools`：SDK/缓存。这些均不提交。
- 活动程序、原始源码提交、SHA256：`operations/config/anode-local-mcp.json`。当前程序产品版本仍为 `0.11.3`，本地发布标签独立命名。
- 全局 MCP 入口：`operations/scripts/anode_local_mcp.py`；不会因 Codex 云账号切换改变本机配置，但仍属于本 Windows 用户的配置，不声称跨 Windows 用户共享凭据。
- 主、子桌面业务数据读取、写入与一致性核验流程：`operations/docs/ANode-数据操作指南.md`。操作前先确认原实例、业务数据真实路径和并发读写边界。
- 唯一日常入口：根目录 `Anode.lnk`。已运行时显示原窗口，未运行时按活动清单启动；关闭查看器窗口可隐藏，不等于 Stop seat。旧的 Start/Show/Hide/Timeline/Remember/MCP 快捷入口已归档。
- 配套入口：`operations/launchers/apps` 放第三方应用，`monitoring` 放监控和看板，`maintenance` 放高级命令工具。
- 只读复核：`powershell -NoProfile -ExecutionPolicy Bypass -File operations/scripts/Test-ProjectSetup.ps1`。
- 构建：使用 `tools/dotnet-10.0.403/dotnet.exe`，`DOTNET_CLI_HOME` 指向 `tools/dotnet-home`、`NUGET_PACKAGES` 指向 `tools/nuget-packages`；输出到 `operations/artifacts/` 下新的目录，避免覆盖被占用的活动程序。

开发只修改固定源码目录并提交。源码回滚通过 Git 标签；运行目录切换必须先验证哈希及当前会话，保留当前版和一份明确可回退的发布。历史恢复材料暂存于 `operations/artifacts/restructure-20261106/`，删除仍需用户按完整清单授权。
