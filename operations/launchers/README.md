# 配套工具入口

Anode 的唯一日常主入口在项目根目录：`../../Anode.lnk`。Ghost 主入口为 `../../../ghost/Ghost.lnk`。

Anode.lnk 现在直接指向 `../work/anode-current/anode.exe`，使用原生 `open` 命令。GUI 和全局 MCP 均不经过 Python 主启动包装；应用副窗口和监控内部所需的 Python 工具继续保留。

- `apps/`：Alpha、WorkBuddy、Notion 等第三方应用的已保留入口。
- `monitoring/`：托盘监控、群消息看板、企微转发启停。
- `maintenance/`：高级 CLI 工作流和远控维护工具。

旧 Anode 多套启动/显示/隐藏入口、重复监控入口和放错项目的 Ghost 入口已归档至 `../artifacts/entrypoints-20261107/retired-launchers/`，没有删除。日后更新统一通过 `../config/launcher-layout.json` 核验当前布局，不再恢复顶层杂乱入口。
