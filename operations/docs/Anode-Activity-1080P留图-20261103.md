# Anode Timeline 1080P 留图

新候选包：`work/anode-activity-20261103/publish-011/anode.exe`。源码和构建已完成，当前 Session 4 的日常留图仍由旧 publish-010 进程处理；尚未切换运行版本。

## 改动

Timeline 的截图宽度上限从 1280 改为 1920。当前 1920×1080 子桌面的留图会保留 1920×1080，较小桌面不会被放大。JPEG 质量仍为 65，关键操作选择、300 ms 延后、至少 3 秒间隔、容量预算和保存目录保持原逻辑。

唯一源码改动：`work/anode-timeline-20261031/source/src/Anode/Seat/SeatHost.cs:171`。普通 MCP 截图请求仍由调用方自己的参数控制，本次只改 Timeline 自动留图和手动关键帧使用的截图路径。

旧的 720P 文件继续保留，不能通过放大恢复为原生 1080P 细节。切换后的新截图才会使用新参数。

## 已执行验证

- Release 自包含发布成功，无构建错误。
- `ANODE_TIMELINE_CHECKS_OK checks=38 realInputSent=false liveSeatStarted=false`。
- `ANODE_ACTIVITY_CHECKS_OK checks=96 inputSent=false liveSeatStarted=false`。
- 41 个 MCP 工具定义对照通过，未改变协议。
- 在现有 Session 4 内运行新包的只读截图探针，三次均成功；逐张解码 JPEG，实际尺寸均为 **1920×1080**。
- 探针未新建 Windows 子会话、未发送输入，也未替换当前日常宿主。
- 与 publish-010 源码清单对照，仅 SeatHost.cs 的一行变化。

证据：`artifacts/anode-activity-20261103/resolution-1080p/`。`probe-dimensions-verification.json` 保存三张图片的尺寸、大小和 SHA256；`resolution-change.diff` 是精确改动；源文件、配置和文档旧副本均保留。

## 切换与复验

当前进程已加载旧代码，需切换到新包后，日常留图才能使用新参数。标准退出会注销当前 Session 4，并关闭其中的微信、TickTick、QuickLook、PowerToys 等程序；必须先保存工作。主桌面应用保留，重新启动可能要求用户在 Windows 原生窗口登录。

切换前不把新包标为日常留图已生效。旧包和旧图片保留。

用户复验清单：

- [ ] 打开 `launchers/Anode-Timeline-1920x1080.lnk`，确认桌面为 1920×1080。
- [ ] 点“手动截图”生成新记录，再点击缩略图打开预览；图片尺寸应显示 **1920×1080**。
- [ ] 打开“截图设置”，预算应仍为默认 256 MiB，目录仍为默认值。
- [ ] 新版自动留图的日常路径仍需切换后实机确认；真实回收、冷启动设置恢复和已暂缓的 ZIP 实机往返不在本次验证结论内。

离线复验命令：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\AI infrastructure\agent-Windows-operation\scripts\Test-AnodeActivity.ps1"
```

预期末行 `ANODE_ACTIVITY_CANDIDATE_OK`，检查数为 38、96，MCP 工具为 41。
