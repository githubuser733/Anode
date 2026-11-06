# Anode Activity 候选版（2026-11-02）

候选发布包是 `work/anode-activity-20261102/publish-006/anode.exe`。38 项原 Timeline 检查、54 项 Activity 检查、41 个 MCP 工具定义对照与离屏布局预览通过。

**尚未切换当前会话。** 日常入口仍运行 publish-004。新版本的真实 Agent／Human 连续留图、导入导出原生对话框和长期资源占用仍为 **UNTESTED**，需要保存当前 Session 2 的工作后再重启验收。Notion 多开任务按用户要求暂停。

## 截图策略

默认使用“关键操作截图”，还可选择“关闭自动截图”或“所有动作（限频）”。设置在当前 Timeline 目录的 `capture-options.json` 中保存；更新设置保留旧配置副本。

| 操作 | 默认是否留图 | 原因 |
| --- | --- | --- |
| 点击、拖拽、整段文字输入、命名快捷键 | 是 | 通常产生可见结果 |
| UI 元素操作、窗口操作、启动应用、命令、会话和控制权变更 | 是 | 影响应用或控制状态 |
| 失败、结果未确认 | 是 | 保留排查现场 |
| 移动、按下／松开鼠标、逐键按下／松开 | 否 | 高频且常是一次完整操作的中间步骤 |
| 滚动、普通读取／观察 | 默认否 | 可切到“所有动作（限频）” |
| 导入、导出、导入的历史记录 | 否 | 不代表当前子桌面发生变化 |

截图在操作完成后异步进行，默认等待至少 300 ms；两次捕获开始至少间隔 3 秒。密集操作保留最新待拍记录，其他记录显示合并状态。它保存的是稍后的现场，可能包含后续动作，详情会显示实际截图时间，不能当作每一步的精确录像。

“留图”按钮用于手动记录关键画面；即使关闭自动截图也可用，仍受频率与容量限制。已有工具返回的截图直接关联到记录，不重复拍摄。

自动截图只在已验证的 Windows 子会话捕获，宽度 1280、JPEG 质量 65。当前桌面的预测约 90 KB／张，常见单次约 60–80 ms；首次预热样本为 173 ms。这些是捕获与调用耗时，不能换算成 CPU 占用率。按每 3 秒一张、90 KB 估算约 100 MiB／小时，实际受合并和画面变化影响。

连续完全相同的 JPEG 内容复用文件，节省磁盘但仍需捕获后才能判断。自动图片累计预算为 **256 MiB**，达到后暂停留图，继续记录文字。预算会在重启后重新统计；不会自动删除旧文件。手动工具截图、导入图片及导出包另计，项目总占用并不限制在 256 MiB。

图片留在项目的本地运行数据中，不自动加入 MCP 响应或发送给模型。只有用户或工具显式读取图片时才涉及后续处理。

实现入口：[`TimelineCaptureScheduler.cs:62`](../work/anode-timeline-20261031/source/src/Anode/Core/Timeline/TimelineCaptureScheduler.cs#L62)、[`SeatHost.cs:153`](../work/anode-timeline-20261031/source/src/Anode/Seat/SeatHost.cs#L153)。

## 颜色与顶部筛选

| 颜色 | 级别 | 例子 |
| --- | --- | --- |
| 蓝色 | 观察 | 截图、读取窗口／UI、等待 |
| 绿色 | 交互 | 点击、输入、拖动、普通 UI 操作 |
| 橙色 | 变更 | 启动应用、执行命令、控制权、会话／显示、归档 |
| 红色 | 高影响 | 停止进程、取消命令、停止会话、关闭窗口 |

分级依据已知的接口动作。程序无法知道某次普通点击是否点中了业务系统的删除按钮，也不分析任意命令内容来判断风险。失败／未确认使用独立的红色结果标记，不会改写动作级别。

参考本地工具中心，表格顶部提供搜索、操作者、类别、级别、结果、现场／导入来源和“仅有截图”组合筛选。“重置分类”保留搜索文字；旁边“×”清空搜索。表格点选记录查看详情与已有图片，小窗口优先保留表格行。

实现入口：[`ActivityPresentation.cs:12`](../work/anode-timeline-20261031/source/src/Anode/Core/Timeline/ActivityPresentation.cs#L12)、[`TimelinePanel.cs:27`](../work/anode-timeline-20261031/source/src/Anode/Daemon/TimelinePanel.cs#L27)。

## 导入与导出

点击 Activity 工具栏“导出”，可选“全部历史（含截图）”或“当前筛选（含截图）”，保存为新的 ZIP。默认目录为当前 Timeline 下的 `exports`；建议使用项目 `artifacts` 内的路径。拒绝覆盖已有归档。

导入只合并记录和图片，标记来源，不执行或回放任何操作。同一归档重复导入会跳过重复记录；导入时尚在进行的动作标记为结果未确认。导入记录不会挤掉现场记录的最近缓存。

界面加载最近 500 条现场记录与最多 5000 条导入记录，一次最多显示 300 行；“当前筛选”导出所有已加载且匹配的记录，不只这 300 行。“全部历史”读取已保留的本地日志，可包含早于现场缓存的记录，单包最多 20000 条、图片总计最多 256 MiB。超限会拒绝，需缩小筛选范围。

ZIP 内含 `activity.json` 和被引用的 PNG／JPEG。导入先验证版本、大小、哈希、重复项、图片格式与路径，再提交本地记录；不接受其他成员或路径穿越。I/O 失败时可能保留未完成的新文件供检查，不自动清理。

实现入口：[`ActivityArchive.cs:19`](../work/anode-timeline-20261031/source/src/Anode/Core/Timeline/ActivityArchive.cs#L19)、[`TimelinePanel.cs:167`](../work/anode-timeline-20261031/source/src/Anode/Daemon/TimelinePanel.cs#L167)。

## 当前验证与复验方法

在本项目目录执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-AnodeActivity.ps1 -Preview
```

预期包含：

```text
ANODE_TIMELINE_CHECKS_OK checks=38 realInputSent=false liveSeatStarted=false
ANODE_ACTIVITY_CHECKS_OK checks=54 inputSent=false liveSeatStarted=false
ANODE_TIMELINE_MCP_DISCOVERY_OK ... tools=41 ... schemaUnchanged=true
ANODE_ACTIVITY_CANDIDATE_OK
```

该命令使用专用测试数据和离屏窗口，不启动／替换真实桌面，不发送键鼠输入。每次在项目内新建证据目录。项目整体检查 `scripts/Test-ProjectSetup.ps1` 当前也以 `PROJECT_SETUP_OK` 通过。

实际子会话捕获函数已用前一候选构建 build-005 在当前 Session 2 内执行 3 次，输出 `ANODE_ACTIVITY_CHILD_CAPTURE_OK`；父会话拒绝捕获检查也已通过。最终 publish-006 已通过上述专用检查，但尚未替换运行中的 daemon／seat host，因此不能用这项探测代替端到端验收。

完整旧版 `selftest --quick` 的实现会删除临时文件，受项目删除规则约束，本次未执行；没有修改或跳过其内部测试。Windows 更新后的兼容性及长期运行资源占用仍为 UNTESTED。

实机切换后待验证：

- [ ] Agent 在测试记事本点击、输入后出现关键截图，连续动作显示合并状态。
- [ ] Human 接管、逐字输入并归还后，记录、截图与操作者一致。
- [ ] 点选有图记录可预览；关闭自动截图后，文字记录继续增加。
- [ ] 组合筛选、颜色、当前筛选导出、全历史导出符合上表。
- [ ] 导出 ZIP 后导入，出现导入来源；重复导入跳过，任何动作都不回放。
- [ ] 隐藏／显示 Viewer 后记录继续；重开后截图策略和历史可恢复。

当前证据目录：`artifacts/anode-activity-20261102/verify-publish-6997e969d7d648749233a14aa6277a0b`。截图性能证据：同目录上级的 `capture-baseline.json` 与 `child-capture-probe-005/capture-probe.json`。
