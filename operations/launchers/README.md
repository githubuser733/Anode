# 配套工具入口

Anode 的唯一日常主入口在项目根目录：`../../Anode.lnk`。Ghost 主入口为 `../../../ghost/Ghost.lnk`。

- `apps/`：Alpha、WorkBuddy、Notion 等第三方应用的已保留入口。
- `monitoring/`：托盘监控、群消息看板、企微转发启停。
- `maintenance/`：高级 CLI 工作流和远控维护工具。

旧 Anode 多套启动/显示/隐藏入口、重复监控入口和放错项目的 Ghost 入口已归档至 `../artifacts/entrypoints-20261107/retired-launchers/`，没有删除。日后更新统一通过 `../config/launcher-layout.json` 核验当前布局，不再恢复顶层杂乱入口。
