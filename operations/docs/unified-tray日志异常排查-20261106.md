# unified-tray 日志异常排查（2026-11-06）

## 结论

这是已复现的程序缺陷。控制管道被占用时，原版控制循环反复立即重试并记录相同异常，缺少退避和错误限频；日志按天区分文件，但没有大小上限。排查没有修改、重启正在运行的工具中心，没有清理、覆盖或移动已有日志，也没有启动 Anode。

截至本轮检查，目录 F:\AI infrastructure\LocalToolsManager\unified-tray\logs 下日志总计 38,028,536,495 字节（约 35.42 GiB）；其中 11 月 5 日单文件为 11,455,985,497 字节（约 10.67 GiB）。11 月 6 日日志 15:33:29 后未见增长；F 盘末次检查剩余 12,771,356,672 字节。

## 直接证据

- 实际日志分段抽样几乎全部为“Controller error: 所有的管道范例都在使用中。”11 月 5 日一个 262,144 字节样本有 2,818 条相同错误，约 1,578 条每秒；11 月 6 日类似样本约 1,618 条每秒。这些是采样估算，不是对全部 35.42 GiB 逐行扫描。
- F:\AI infrastructure\LocalToolsManager\unified-tray\src\TrayHost.cs 第 448～478 行创建最多一个服务器实例，捕获异常后立即再次循环；第 481～486 行只按日期命名并追加写日志，没有大小上限。
- 隔离复现程序使用现役发布版 LocalToolsTray.dll 的真实 RunPipeAsync 方法，在全新随机管道名和独立目录中运行：管道空闲时 759 ms 内 0 条错误；被测试进程占用时 763 ms 内 829 条错误并返回预期的 FAIL: PIPE_ERROR_FLOOD。测试没有连接生产管道。
- 当前主桌面进程 PID 70188 仍在；其状态接口返回 40 行、4 个兼容锁、LastError 为空。这个只说明检查时控制接口可用，不证明长期故障已修复。

## 触发条件的判断

主、子桌面使用同一 Windows 用户时，跨会话争用很可疑：Program.cs 第 19～22 行的管道名包含用户 SID 和实例名，却不包含会话 ID；第 44 行的单实例 mutex 使用 Local\ 前缀，仅在当前会话内生效。微软文档说明会话内核对象命名空间与管道名称的作用范围不同：

- https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces
- https://learn.microsoft.com/en-us/windows/win32/ipc/pipe-names

11 月 6 日真实日志最后一条在 15:33:29.650，介于 Anode 开始退出子桌面（15:33:18.991）和 Session 15 注销完成（15:33:33.496）之间。但历史记录未捕获当时管道的持有者，不能仅凭时间认定占用者必然是 Anode 里的第二个工具中心实例。

## 建议及限制

修复时应同时处理跨会话的实例协调、管道错误的有界重试与重复日志限频，并设置日志容量上限与清晰提示。修改管道名称时，服务器和全部本地控制客户端必须共同适配；日志留存策略不能未经授权自动删除现有文件。修复仍需构建、隔离冲突回归以及真实启动／状态／退出流程验收。本轮未修复或部署；Anode 子桌面重新登录后，冲突仍有可能再次出现。

原始日志可能体积很大，不建议用文本编辑器直接整文件打开。若要释放空间，须按项目删除规则先逐项展示范围并获得本次口令；移入回收站不会立即释放空间。

## 复核

~~~powershell
Get-ChildItem -LiteralPath 'F:\AI infrastructure\LocalToolsManager\unified-tray\logs' -File |
  Sort-Object Length -Descending | Select-Object Name,Length,LastWriteTime

python 'F:\AI infrastructure\agent-Windows-operation\scripts\diagnostics\check_unified_tray_log_flood.py' 'F:\AI infrastructure\LocalToolsManager\unified-tray\logs\unified-tray-20261105.log'

dotnet run --no-build --project 'F:\AI infrastructure\agent-Windows-operation\artifacts\unified-tray-log-investigation-20261106\repro\TrayPipeRepro.csproj' -c Release -- 'F:\AI infrastructure\agent-Windows-operation\artifacts\unified-tray-log-investigation-20261106' free

dotnet run --no-build --project 'F:\AI infrastructure\agent-Windows-operation\artifacts\unified-tray-log-investigation-20261106\repro\TrayPipeRepro.csproj' -c Release -- 'F:\AI infrastructure\agent-Windows-operation\artifacts\unified-tray-log-investigation-20261106' busy
~~~

采样检查预期输出 LOG_FLOOD_DETECTED；free 应 PASS，busy 应复现 PIPE_ERROR_FLOOD 并以 1 退出。复现器每次创建全新测试目录，使用随机管道名，不修改生产日志。
