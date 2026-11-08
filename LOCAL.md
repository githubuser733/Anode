# 本机维护入口

先读取上级 `../AGENTS.md`。本仓库保留 Anode 上游历史及本机 Timeline、文本增强、监控、登录、重连、截图修改；当前分支 `codex/local-current`。

当前运行版：`local-2026.11.08.1`，固定路径 `operations/work/anode-current/anode.exe`；上一版由 `operations/config/anode-previous.json` 记录。当前维护快照 `local-2026.11.08.2` 只整理文档、历史脚本和检查工具，没有替换运行程序。原生入口验收见 `operations/docs/Anode-原生入口与精简-20261108.md`；本次整理见 `operations/docs/双项目精简与当前布局-20261108.md`。

- `operations/scripts`、`operations/config`、`operations/launchers`、`operations/docs`：原父目录的配套系统，纳入 Git。
- `operations/maintenance/history`：退出日常链路的一次性脚本，原内容以 `.txt` 保存；`operations/docs/history`：完整旧说明与根 Agent 历史。原始下载包归入 `operations/artifacts/recovery/original-downloads`。
- `operations/work`：当前程序和应用运行数据；`operations/artifacts`：监控数据库、报告、历史证据、恢复副本；`tools`：SDK/缓存。这些均不提交。
- 活动程序、原始源码提交、SHA256：`operations/config/anode-local-mcp.json`。当前程序产品版本仍为 `0.11.3`，本地发布标签独立命名。
- 全局 MCP 直接运行 `operations/work/anode-current/anode.exe --channel main mcp`；兼容脚本 `operations/scripts/anode_local_mcp.py` 留给既有监控/副窗口内部复用。不会因 Codex 云账号切换改变本机配置，Windows 凭据属于当前 Windows 用户。
- 主、子桌面业务数据读取、写入与一致性核验流程：`operations/docs/ANode-数据操作指南.md`。操作前先确认原实例、业务数据真实路径和并发读写边界。
- 唯一日常入口：根目录 `Anode.lnk`，直接指向项目内 EXE，使用带固定参数的 `open` 命令。已有宿主时只显示原窗口，未运行时启动；现有宿主的停止/断开状态不会因重复点击而自动重建。Python/PowerShell 主入口包装及旧别名脚本已归档。
- 配套入口：`operations/launchers/apps` 放第三方应用，`monitoring` 放监控和看板，`maintenance` 放高级命令工具。
- 只读复核：`powershell -NoProfile -ExecutionPolicy Bypass -File operations/scripts/Test-ProjectSetup.ps1`。
- 构建：使用 `tools/dotnet-10.0.403/dotnet.exe`，`DOTNET_CLI_HOME` 指向 `tools/dotnet-home`、`NUGET_PACKAGES` 指向 `tools/nuget-packages`；输出到 `operations/artifacts/` 下新的目录，避免覆盖被占用的活动程序。

开发只修改固定源码目录并提交。源码回滚通过 Git 标签；运行目录切换必须先验证哈希及当前会话，保留当前版和一份明确可回退的发布。历史恢复材料暂存于 `operations/artifacts/restructure-20261106/`，删除仍需用户按完整清单授权。

Timeline 的离线超时已修正；Codex 历史偶发重启、输入/连续重连保护和冷回退登录尚未全面闭环。输入法与导入导出文件对话框真人验收继续暂停，不为目录整理开启这些实验。
