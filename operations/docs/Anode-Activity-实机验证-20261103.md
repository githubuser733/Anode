# Anode Activity 实机验证 · 2026-11-03

当前版本 `publish-010` 的主要界面和自动留图验证通过；真实回收、宿主重启后的设置恢复、原生文件夹选择器未测试。导入导出的 Windows 文件对话框自动化未完成，已取消，等待用户手动操作后核对。未修改产品源码，未重启或注销当前 Session 4。

## 已执行的检查

| 项目 | 实机结果 | 证据 |
| --- | --- | --- |
| 启动与身份 | 同一 Zhang 账户，Session 4，1920×1080，View only，PointerGuard 已安装，无日志错误 | `initial-status.json` |
| 程序完整性 | 运行包 SHA256 与 publish-010 清单一致，105 个源码文件哈希一致 | 原 `candidate-010-manifest.json` 与本轮检查输出 |
| Agent 输入 | 本轮专用记事本成功输入并保存 `ACTIVITY_010_AGENT_OK_20261103` | `agent-verification.json`、测试 TXT |
| 自动留图 | 输入记录关联真实 JPEG，59,759 bytes，元数据只记字符数；截图本身包含可见内容 | `agent-verification.json` |
| 观察操作 | `desktop.observe` 不自动截图，状态为 `not selected` | `agent-verification.json` |
| 搜索和颜色 | 搜索 `Typed 30` 只显示本次输入记录，四级图例与状态列可见 | Viewer 实机观察 |
| 缩略图预览 | 点击缩略图打开真实图片；80% 适合窗口 → 100% → 125% → 100% → 80% 均成功 | `viewer-image-*.txt`、`viewer-image-preview.jpg` |
| 自定义预算和目录 | 从界面改为 512 MiB 和项目内目录，保存并重开设置仍保持；手动关键帧实际写入该目录 | `custom-settings-verification.json`、`settings-custom-*.txt` |
| 恢复默认值 | 从界面恢复 256 MiB 与默认目录，旧图和自定义目录图片哈希均未变，旧设置备份存在 | `defaults-restored-verification.json` |
| 清理预览与取消 | 完整列出 58 个文件、7.33 MiB；未填口令，执行按钮禁用；取消后原有 60 个文件哈希一致 | `cleanup-cancel-verification.json`、`cleanup-preview-no-authorization.jpg` |
| 取消后继续工作 | 新日志成功写入关键帧，使用默认图片目录，截图调度恢复 | `post-cleanup-capture-verification.json` |

上述证据目录均为 `artifacts/anode-activity-20261103/live-093600/`。Notepad 仅操作本轮新建测试文件；原空文件副本保留在 `notepad-before-save.empty.txt`。

## 截图资源测量

在已核验的子会话内，固定编码的三次探测均返回成功：

| 样本 | 捕获及编码耗时 | JPEG 大小 |
| --- | ---: | ---: |
| 1 | 200.1621 ms | 59,789 bytes |
| 2 | 55.0163 ms | 59,771 bytes |
| 3 | 88.0891 ms | 59,793 bytes |

证据：`child-capture-probe/capture-probe.json`，命令输出 `ANODE_ACTIVITY_CHILD_CAPTURE_OK samples=3 inputSent=false parentDesktopCaptured=false`。这组数据来自当前文本编辑场景，不代表复杂应用、长时间运行或 CPU／GPU 峰值；耗时不含随后把探测图片写入证据目录的时间。

仍使用默认关键操作策略、约 300 ms 延后和至少 3 秒间隔，合并快速连续动作，跳过鼠标移动和观察操作，相同画面可复用。相关实现见 `TimelineCaptureScheduler.cs:51`、`:126`、`:145`；实际探测入口见 `SeatHost.cs:178`。本轮没有将设置改成每次操作截图，也没有用填满磁盘的方式测试预算。

## 异常与未完成项

- 旧 MCP 连接曾报 `Pipe is broken`，重新只读查询恢复；随后旧桥接进程已退出，创建项目内新桥接器后完成子会话测试。未重新创建 Windows 子会话。
- 关闭预览窗口后，主窗口的一次点击报 `coordinate input geometry is unavailable`；重新读取截图后该按钮正常打开。属于本次自动化工具的坐标状态问题，未修改 Anode 代码。
- 取消清理的核对脚本曾因系统默认 GBK 读取 UTF-8 证据而失败；明确 UTF-8 后重跑通过，60 个原文件哈希均一致。
- 导出菜单和“当前筛选”文件对话框已实际打开，但文件名控件索引不可用、焦点无法可靠核对；未向不确定焦点输入路径，已取消对话框。导入导出仍保留此前离线 ZIP 往返验证结果，不能据此宣称本轮实机 ZIP 往返通过。
- Viewer 显示累计截图失败数为 1，测试开始前已存在；本轮成功留图没有新增该计数。计数不等于日志 I/O 错误。
- 原生“选择…”文件夹对话框未测试；本轮自定义目录通过可编辑路径框设置。
- 用户未授权本轮具体清单的真实删除；未输入约定口令、未执行真实回收。回收站真实移动仍为 **UNTESTED**。
- 未重启宿主或注销当前子会话来测试冷启动设置恢复；本轮只验证保存文件和重新打开设置对话框。
- 本轮没有让真人重新接管输入；既往 publish-004 的真人记录结果不等于本轮 publish-010 的真人复验。

## 用户复验清单

- [ ] 在 Viewer 打开 Timeline，清空搜索，选有图记录，点击缩略图检查预览与缩放。
- [ ] 打开“截图设置”，应为 256 MiB 和默认目录。
- [ ] 打开“清理日志…”查看完整清单，直接取消，软件和记录应继续运行。
- [ ] 当前筛选测试记录导出 ZIP 后再导入，核对记录标为导入、截图可打开。
- [ ] 如需后续真实回收，先核对当次完整清单，再由用户本人输入口令；之前授权不适用于新清单。

可复验此前离线包：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\AI infrastructure\agent-Windows-operation\scripts\Test-AnodeActivity.ps1" -Preview
```

预期：38 项 Timeline、96 项 Activity、41 个 MCP 工具定义保持一致，末尾 `ANODE_ACTIVITY_CANDIDATE_OK`。

## 本轮结束状态

`verification-summary.json` 汇总了 37 个证据文件的大小和 SHA256。Session 4 保持 ready，View only，日志无错误；本轮记事本进程已正常退出，测试文件保留，桌面租约已释放。截图预算保持默认 256 MiB，保存目录为默认值。ZIP 往返仍等待手动文件对话框操作，尚未发现指定的 `filtered-activity.zip`；未将未完成项标为通过。活动配置的整体 `liveVerified` 暂保持 false，具体已验证范围以本报告和汇总 JSON 为准。
