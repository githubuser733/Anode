# Anode Activity：图片预览、截图设置与日志管理

当前包：`work/anode-activity-20261102/publish-010/anode.exe`。**2026-11-03 用户完成登录，Session 4 已就绪，1920×1080；主要界面和自动留图已完成实机验证。** 原 Session 3 已于前一天 23:55 从 Viewer 停止，更新时没有子会话应用需要关闭。Notion 的并行入口已另行完成实测，见 `Notion-并行运行与登录复用-20261102.md`。本轮记录见 `Anode-Activity-实机验证-20261103.md`。

当前包通过 38 项原 Timeline 检查、96 项 Activity 检查、41 个 MCP 工具定义对照和离屏界面预览。实机已通过 Agent 输入／保存／自动留图、缩略图预览与缩放、512 MiB 自定义目录保存及重开、恢复默认值、清理完整清单与取消后的文件哈希核对。Windows 原生文件夹选择器、宿主重启后的设置恢复及真实回收仍为 **UNTESTED**；ZIP 文件对话框需用户完成后核对。没有执行真实回收或删除日志。

## 操作区与图片预览

工具栏分为两行：第一行是导入、导出和红色“清理日志…”；第二行是“手动截图”、自动截图策略和“截图设置”。按钮有独立底色、边框、间距和悬停提示。自动截图选择框使用系统文字／背景颜色，避免浅色控件中出现白色文字。

选中有图记录后，下方显示缩略图。点击缩略图、点击表格“图”列，或双击有图记录，打开图片预览窗口。支持适合窗口、100%、放大、缩小、滚动查看，按 Escape 关闭。

实现：[`TimelinePanel.cs:27`](../work/anode-timeline-20261031/source/src/Anode/Daemon/TimelinePanel.cs#L27)、[`ActivityDialogs.cs:5`](../work/anode-timeline-20261031/source/src/Anode/Daemon/ActivityDialogs.cs#L5)。预览图片使用样例记录和合成画面，没有连接第二个 Windows 桌面。

## 截图预算与保存目录

点击“截图设置”：

- 默认自动截图预算为 **256 MiB**，可设置为 **1–65536 MiB**。
- 默认仍保存在当前运行数据下的 `timeline` 目录；本项目配置解析为 `artifacts/anode-timeline-20261031/runtime-state/timeline`。
- 可选择自定义绝对目录，也可恢复默认预算和默认目录。
- 只影响之后保存的图片，不搬动旧图片。每条记录保存自己的图片位置，旧图继续可预览、导出。
- 预算统计自动截图，包括之前选过的保存目录；手动工具返回的图片、导入图片和导出 ZIP 另计。
- 达到预算后停止自动留图，文字记录继续；不会自动删除旧图片。

设置写入当前 Timeline 的 `capture-options.json`，替换前保留旧设置副本。输入非法路径或容量会拒绝应用；启动时图片目录不可用会关闭自动截图并显示错误。严重日志写入错误仍可能需要修复目录和重启，界面不会保证所有 I/O 错误都能原地恢复。

新版本导出 ZIP 会把图片目录转为可携带的归档引用，不将本机自定义保存根目录写入图片引用。导入后使用接收方当前选择的目录。归档仍保留既有 20000 条记录、256 MiB 图片等大小限制。

旧 publish-006 不识别新日志中的自定义图片目录字段；如需回退并查看这些图片，先用新版导出 ZIP，再在旧版导入，或直接打开保留的原始图片。不会自动迁移或删除旧目录。

实现：[`ActivityCaptureSettings.cs:5`](../work/anode-timeline-20261031/source/src/Anode/Core/Timeline/ActivityCaptureSettings.cs#L5)、[`TimelineCaptureScheduler.cs:66`](../work/anode-timeline-20261031/source/src/Anode/Core/Timeline/TimelineCaptureScheduler.cs#L66)。

## 日志状态与清理

表格上方独立显示日志是否正常、待写队列和未入队数量。磁盘写入在后台执行；专用检查实际制造了日志目录 I/O 失败，原操作仍返回成功。界面提示的是记录状态及是否阻断操作，不是 CPU／磁盘开销为零的保证。

“清理日志…”按以下流程工作：

1. 暂停自动留图并轮换到新日志，软件和新文字记录继续。
2. 读取旧日志及其关联截图，列出每个文件的绝对路径、文件数和总大小；逐文件核对所属目录，拒绝文件／目录链接。
3. 完整清单显示后，由用户本人输入约定口令并点击“移入回收站”。助手不能代填口令。
4. 执行前重新核对文件哈希；清单变化则拒绝执行，需要重新预览和确认。
5. 只移入 Windows 回收站，检查原路径是否还存在；失败或取消会显示未完成项，不退回永久删除。

当前新日志、配置、备份与导出 ZIP 不进入清单。不递归删除目录，不清空回收站。没有日志引用的残留图片不自动处理。完成或取消后恢复截图调度。

实现：[`ActivityCleanup.cs`](../work/anode-timeline-20261031/source/src/Anode/Core/Timeline/ActivityCleanup.cs)、[`WindowsActivityRecycleBin.cs`](../work/anode-timeline-20261031/source/src/Anode/Core/Timeline/WindowsActivityRecycleBin.cs)。回收操作在独立 STA 工作线程使用 Windows `IFileOperation`，显式设置 `FOFX_RECYCLEONDELETE`、`FOFX_EARLYFAILURE` 和 `FOF_NOERRORUI`，检查 HRESULT、取消状态和原路径；不支持网络／非固定磁盘，不提供其他删除方式，首项失败即停止。标志和接口顺序已核对 [Microsoft 文档](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperation-setoperationflags)及本地保留的微软 SDK 头文件。无损探测已实际激活 COM、设置标志并读取取消状态，没有提交或执行任何文件操作。真实回收仍为 **UNTESTED**，模拟测试不等于 Windows 回收站实测。

## 用户复验清单

可在项目目录运行离线复验：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-AnodeActivity.ps1 -Preview
```

预期包含：

```text
ANODE_TIMELINE_CHECKS_OK checks=38 realInputSent=false liveSeatStarted=false
ANODE_ACTIVITY_CHECKS_OK checks=96 inputSent=false liveSeatStarted=false
ANODE_TIMELINE_MCP_DISCOVERY_OK ... tools=41 ... schemaUnchanged=true
ANODE_ACTIVITY_CANDIDATE_OK
```

此命令不替换当前桌面、不发送真实键鼠输入、不执行真实回收。完整旧版 `selftest --quick` 内部有删除临时文件的路径，本次未执行，也未弱化其测试。

恢复实测：打开 `launchers/Anode-Timeline-1920x1080.lnk`，点击 Viewer 的 **Reconnect / Sign in…**，由用户在 Windows 原生窗口完成登录。本入口保留 1920×1080 与共享剪贴板；当前空闲宿主的入口只显示 Viewer，不会代填凭据。

登录后待验：

- [ ] 点选有图记录，点击缩略图放大，测试适合窗口／100%／缩放。
- [ ] 将预算改为 512 MiB，选择项目内测试目录，确认新图保存在新位置，旧图仍能打开。
- [ ] 恢复默认预算和目录，重开后检查设置保持。
- [ ] 打开“清理日志…”核对完整清单；直接取消，确认日志与软件继续运行。
- [ ] 如需真实清理，由用户核对具体清单后本人输入口令；确认结果与 Windows 回收站。
- [ ] 真实 Agent／Human 操作、导入导出和隐藏 Viewer 后继续记录。

实机证据：`artifacts/anode-activity-20261103/live-093600/`。此前暂缓登录的历史状态保存在 `artifacts/anode-activity-20261102/deployed-010-login-deferred-status.json`；离线检查和预览保存在 `artifacts/anode-activity-20261102/verify-publish-c0c77b657680467cb449f2d8758f2ff3/`。以后更新前仍需只读核对子会话；如已有新子会话及用户工作，先保存并取得针对该会话的重启同意。
