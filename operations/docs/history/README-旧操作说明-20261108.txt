当前路径补记（2026-11-06）
本文件以下为历史使用记录；配套根目录现为 F:\AI infrastructure\agent-Windows-operation\anode\operations。当前总览见父目录 README.md，本轮验收见 docs\双仓库迁移验收-20261106.md。历史 BgDesk 入口不代表仍可用。

Windows 后台桌面操作项目
项目目录：F:\AI infrastructure\agent-Windows-operation

日常使用
1. 打开 launchers\Anode-Show.lnk：显示当前 Anode 子会话。
2. 在子会话里的文件资源管理器中打开本项目 launchers 目录，
   双击 WorkBuddy-Shared-In-Anode.lnk：共用主桌面聊天配置启动 WorkBuddy。
   直接双击 WorkBuddy 普通图标可能因跨会话单实例锁退出。
3. 使用 Anode-Hide.lnk 隐藏 Viewer，子会话应用继续运行。
4. Anode 尚未运行时，使用 Anode-Start-1920x1080-Clipboard.lnk。
   启动配置为 1920×1080、共享剪贴板、Windows 原生登录。
   现有会话可直接 Show/Hide，不必重复登录；退出整个 Anode 会话会关闭其应用。

项目内容
launchers：本项目快捷方式。
scripts：可复用启动、MCP 工具桥和检查脚本。
config\verified-desktop.json：当前已验证参数。
docs\操作说明.txt：路径、共享配置机制、工具调用和验证步骤。
artifacts\history-20261031：从旧对话目录校验复制的全部历史证据。
artifacts\migration-manifest.json：逐文件 SHA256 校验清单。
artifacts\mcp：此后工具调用日志及截图，每次新建独立运行目录。
work：已下载程序、源码和运行数据；沿用现有目录以保持当前会话。

在项目目录打开 PowerShell，执行：
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-ProjectSetup.ps1
预期末行：PROJECT_SETUP_OK

已打开主、子桌面 WorkBuddy 时，可进一步执行：
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-ProjectSetup.ps1 -Live
预期包含：VERIFY_SHARED_WORKBUDDY_OK checks=4，且末行 PROJECT_SETUP_OK。
该检查只读取现有会话，不启动或关闭应用。

旧对话目录副本保留，未删除。今后的本项目交付和证据写在本项目内。
应用自身的主配置、Windows 登录信息和系统日志仍使用各自标准用户目录。
artifacts 和 work 已列入 .gitignore，避免将历史日志和应用运行数据纳入版本库。

Grok Bot 与 BgDesk 后续测试
当前 BgDesk 修复版入口：launchers\BgDesk-Start-Fixed.lnk。
活动宿主参数：config\bgdesk-runtime.json。
运行与修复说明：docs\BgDesk-运行与修复.txt。
Grok Bot 多开、登录复用和维护比较：docs\GrokBot-方案比较.txt。
真实托盘与人工接管结果仍待用户反馈，自动输入目前暂停。

后续真人反馈与现场复验（2026-10-31）
原生窗口已由用户确认正常；浏览器随后发现仍在运行旧页面代码。
已重新载入真实页面并通过9点坐标检查，真人点击复验待反馈。
详情：docs\BgDesk-浏览器偏移复验-20261031.txt（本文件位于 docs 时，见同目录文件）。

原生任务栏功能（2026-10-31 最新活动版本）
当前宿主已改为 work\bgdesk-taskbar-20261031\publish-001。
已加入任务栏按钮及最小化/还原；自动检查通过，真实任务栏及最小化后刷新待验收。
使用与证据见 docs\BgDesk-任务栏最小化.txt（docs 内见同目录文件）。

Anode Timeline 候选版（2026-10-31）
BgDesk 后续优化已按用户要求停止，保留当前会话及所有证据。
Anode 已制作操作时间线候选版，离线30项和41个MCP定义检查通过，实机待验收。
使用、覆盖范围和未完成检查见 docs\Anode-Timeline-候选版.txt。
运行 scripts\Test-AnodeTimeline.ps1 -Preview 可重复执行保留证据的验证。

Anode Timeline 实机进度（2026-10-31）：候选配置现指 publish-004；38项专用检查通过，41个MCP工具接口不变。
publish-002真人输入记录缺失已复现，跨线程修正已离线验证，publish-004正在等待登录以复验。不能视为正式验收完成。
最新证据：artifacts\anode-timeline-20261031\candidate-manifest-004.json。

2026-10-31 最终实机记录：publish-004 的真人输入、点击、接管/归还、Agent接续、隐藏后记录、历史恢复、筛选和截图预览均已验证。
日常使用 launchers\Anode-Timeline-1920x1080.lnk；已有候选会话时直接显示 Viewer，不重复登录。
完整结果与未验证事项见 docs\Anode-Timeline-使用与实测.txt；它取代前述“等待登录/等待实机”状态。原版快捷方式保留。

2026-10-31 清理后当前状态（取代上文旧 BgDesk 活动版本说明）
BgDesk 已停用：批准清单中的程序、源码、脚本及快捷方式已移入 Windows 回收站。
旧 Anode publish-001/002/003、调试构建和已解压 SDK 安装包也已移入回收站。
日常入口仍为 launchers\Anode-Timeline-1920x1080.lnk，当前版本 publish-004。
源码、构建工具、原版 Anode MCP、登录数据、配置、备份和历史证据保留；现有子会话未重启。
上文 BgDesk 路径及测试进度仅作历史记录，不再作为可用启动说明。
清理结果与恢复方法：docs\项目清理结果-20261031.md。
维护评估：docs\Anode-维护评估-20261031.md。

2026-10-31 Grok Bot、TRAE、OpenCode 多开入口
在 Anode 子会话的文件资源管理器中打开本项目 launchers 目录，使用：
GrokBot-In-Anode.lnk、TRAE-CN-In-Anode.lnk、OpenCode-In-Anode.lnk。
三者均已实测启动及最小化后恢复；主桌面误用会提示在 Anode 内打开。
TRAE 使用首次复制的独立账户/配置，不实时共享任务列表；OpenCode 共用原有后台服务和业务数据。
使用与边界见 docs\多开快捷方式-使用与实测.md。
验证：scripts\Test-MultiopenLaunchers.ps1；三个软件都在运行时可加 -Live。

2026-10-31 TRAE 最新配置（取代上述“独立配置、不共享任务”说明）
同一个 TRAE-CN-In-Anode.lnk 现默认启动 Agent，并共用本机 ModularData 和 solo-lite 数据。
模型、MCP、快捷键等在每次点击入口时读取主配置，Agent 默认模式仅覆盖子会话设置。
冷启动、Agent 窗口恢复、已有任务读取及主子会话共存均已实测。
当前运行目录：work\multiopen\trae-cn-shared-agent；旧独立测试目录保留。
说明与验证：docs\TRAE-Agent-共享配置-20261031.md。

2026-11-02 Anode Activity 第一阶段（历史验证）
新增带截图的 ZIP 导入导出、顶部组合筛选、四级颜色和限频关键操作截图。
候选包：work\anode-activity-20261102\publish-006\anode.exe。
38 项原有检查、54 项 Activity 检查、41 个 MCP 工具定义和离屏布局预览通过。
publish-006 已按用户授权完成切换；当前追加改进候选见本文末尾。
说明、截图策略与未验证事项：docs\Anode-Activity-使用与验收-20261102.md。
复验：powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-AnodeActivity.ps1 -Preview
预期末尾包含 ANODE_ACTIVITY_CANDIDATE_OK；该检查不会替换或重启当前桌面。

2026-11-02 Notion 并行入口已实测
在 Anode 子桌面使用 launchers\Notion-In-Anode.lnk。
复用初始登录，使用独立运行目录；已验证主子桌面同时运行、关闭后恢复和正常退出重开后的登录保持。
说明：docs\Notion-并行运行与登录复用-20261102.md。
验证：powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Test-NotionConcurrent.ps1 -Live
预期末行 NOTION_CONCURRENT_SETUP_OK。旧 Notion-Shared-In-Anode.lnk 保留，现请使用上述新入口。
Anode 曾按用户授权切换到 Activity publish-006、Session 3；该子会话于 23:55 从 Viewer 停止并注销。

2026-11-03 Timeline 追加改进（主要界面已实机验证）
当前包：work\anode-activity-20261102\publish-010\anode.exe。
新增缩略图放大预览、截图预算和目录设置、日志健康提示，以及清单＋口令＋回收站清理流程。
归档与截图工具分成两行，按钮有边框和间距；清理入口单独标红。
38 项原检查、96 项 Activity 检查和 41 个 MCP 工具定义对照通过。
Windows 明确回收接口的无损探测通过；真实回收未执行，真实界面交互与部署后设置恢复待验收。
用户已登录 Session 4，1920×1080；Agent 输入与自动留图、图片预览及缩放、截图预算与目录、恢复默认值、清理预览和取消均通过。
清理取消后核对 60 个原文件哈希一致，未执行真实删除；截图设置已恢复 256 MiB 与默认目录。
导入导出文件对话框仍待手动复验；真实回收及宿主重启后的设置恢复未测试。
实机记录：docs\Anode-Activity-实机验证-20261103.md。
完整说明：docs\Anode-Activity-截图设置与日志管理-20261102.md。

2026-11-03 Timeline 1080P 留图候选（待切换）
新包：work\anode-activity-20261103\publish-011\anode.exe。
Timeline 留图宽度上限从 1280 改为 1920；JPEG 质量、限频、预算和目录逻辑不变。
38 项 Timeline、96 项 Activity、41 个 MCP 工具定义对照通过；新包在现有子会话的三张实拍图片均为 1920×1080。
当前日常进程仍为 publish-010，需保存当前 Session 4 的工作后切换；尚不声明日常留图已生效。
说明与复验：docs\Anode-Activity-1080P留图-20261103.md。

2026-11-03 UU 主桌面兼容模式（已实测）
Anode 保持运行时，UU 已通过断开重连复验：原始主桌面、键盘和鼠标均正常。
启用：launchers\UU-Console-Enable.cmd；回退到 UU 默认行为：launchers\UU-Console-Restore.cmd。
重复启用、手动恢复及再次启用均已实际执行。回退到默认行为后可能再次进入 Anode 子桌面。
当前为版本限定的运行时绕过；未安装开机任务。UU 服务或电脑重启后如复发，请重新启用。
UU 升级、锁屏、Anode 子会话重建及异常自动恢复尚未验证。ToDesk 仍未修复。
说明、源码/反汇编依据、证据与复核命令：docs\UU-主桌面兼容模式-20261103.md。
2026-11-04 Ghost 项目迁入（Anode 会话保持原状）
Ghost 完整项目已移动到 work\ghost，与 Anode 源码 work\anode-fork 并列。
便携包：work\ghost\ghost-portable-v0.23.4；源码：work\ghost\ghost-source-archive-1\ghost-0.23.4。
已核验配置：config\ghost-runtime.json；说明：docs\Ghost-项目位置与入口-20261104.txt。
双击 launchers\Ghost-Check.cmd 检查版本、全部迁移文件哈希和 doctor，预期 GHOST_PROJECT_SETUP_OK。
Agent 的原生 MCP 入口为 launchers\Ghost-MCP.cmd；仅校验入口用 -CheckOnly。
CLI 入口为 launchers\Ghost-CLI.cmd；例：.\launchers\Ghost-CLI.cmd --version。
本次迁移 285 个文件，SHA256 全部相符；doctor 必需项通过。MCP 冷启动和 GUI 未在此轮重测（UNTESTED）。
未重启 Anode、未注销子会话、未移动任何活动 Anode 运行目录。历史归档保持原样。
其他旧测试材料尚未回收，需按本批完整清单重新确认口令；此前的清理授权不适用于本批。
迁移与本批清单：artifacts\ghost-reorganization-20261104-2bdd75c9。
2026-11-04 回收执行补记（取代上文的本批“尚未回收”状态）
用户查看完整清单并回复本批口令后，12 项旧 Trope/MouseMux 测试材料已移入 Windows 回收站。
共 353 个文件、285,144,514 字节，回收内容 SHA256 全部核对一致，原路径均不存在；回收站未清空。
Ghost、Anode、历史证据、备份和已安装 MouseMux 服务保持不变；三个 Anode 进程及服务进程身份核对通过。
完整结果与用户复核：docs\Ghost-迁移与回收结果-20261104.txt；执行审计在 artifacts\ghost-reorganization-20261104-2bdd75c9\cleanup-result.json。
2026-11-04 统一版本与本地 MCP
Timeline 最新源码已包含于文本/监控分支；统一发布分支为 codex/anode-integrated-20261104，提交 5b42191。
本机用户级 Codex MCP 已注册为 anode：使用 scripts/anode_local_mcp.py 与 config/anode-local-mcp.json，跨工作区复用，不使用云端账号授权。
统一桌面入口：launchers/Anode-Integrated-Start.cmd；旧宿主仍在运行时该入口会拒绝直接替换。
统一包和 MCP 已验证，当前 Session 6 的桌面宿主仍为 publish-010；须保存工作后安排切换，不能当作新宿主已实机验收。
说明、验证结果、更新方式及未完成事项：docs/Anode-统一版本与本地MCP-20261104.md。

2026-11-04 统一版切换与登录暂缓（取代上一节的旧 Session 6 仍运行状态）
用户确认工作已保存并允许切换后，旧 Session 6 已正常退出，统一版 Anode 程序已启动。
用户暂时无法完成 Windows 原生登录，当前子桌面未就绪；不要把 MCP 可发现当作 GUI 已可操作。
日常 Timeline 入口和默认监控配置已指向统一包。原有 275 个状态文件完整保留，273 个相同，2 个仅追加。
稍后打开 launchers\Anode-Integrated-Start.cmd 完成 Windows 原生登录，再继续实机验收。
当前状态与证据：docs\Anode-统一版本与本地MCP-20261104.md，artifacts\anode-activation-20261104-045618-905c3c。

2026-11-04 统一版子桌面实测（更新前文的登录暂缓状态）
Session 7 已 ready，统一宿主 PID 67980；原生 Anode MCP 已在本对话实际操作成功。
真实鼠标/中文输入、8192 字完整读取、截断补图、Timeline 日志与 1080P 自动留图通过。
受控监控：28 条消息全部记录、26 条命中、0 漏记/重复、5 张截图；真实微信和长期稳定性未测。
测试进程已退出、租约已释放，Alpha 多开任务暂停。QuickLook/PowerToys 错误窗口未处理。
证据：artifacts/anode-child-live-20261104T030503-6fb731/result.json；截图：同目录 child-desktop.png。
只读复核：python -B .\artifacts\anode-child-live-20261104T030503-6fb731\verify.py，预期 ANODE_CHILD_LIVE_VERIFIED。

2026-11-04 Alpha 子桌面快捷方式（轮流使用，未实现同时多开）
在 Anode 子桌面内双击 launchers\Alpha-Shared-In-Anode.lnk；原数据目录为 C:\Users\Zhang\AppData\Roaming\Alpha。
已实际启动到 Session 7，原聊天可读；重复点击复用同一实例，窗口设置在主桌面可读回。
Alpha 0.3.5 自带单实例锁，主、子桌面按顺序切换，先正常退出另一端。
说明：docs\Alpha-子桌面共享快捷方式-20261104.md；参数：config\alpha-shared.json。
证据：artifacts\alpha-shortcut-20261104-140713-a480b3\result.json。

2026-11-04 Alpha 共享数据只读副窗口
已实测主桌面原 Alpha + Anode 只读副窗口同时打开；原 Alpha 是唯一数据后台。
日常主桌面入口：launchers\Alpha-ReadOnly-In-Anode.lnk。原 Alpha HTTP API 须保持运行，Anode 须已登录就绪。
支持会话、消息、搜索与分页；图片音视频和原版完整界面尚未接入。旧 Shared 入口仍用于顺序切换。
说明与复核命令：docs\Alpha-共享数据只读副窗口-20261104.md。

微信提醒与摘要工作流（2026-11-04）：docs/微信群提醒与摘要工作流-20261104.md
入口：launchers/WeChat-Monitor.cmd；配置：config/wechat-monitor.json。仅限两个新群，发送目标为文件传输助手。

微信群常驻关键词提醒（2026-11-04）
启动／停止：launchers 下“微信群关键词监控-启动.lnk”和“微信群关键词监控-停止.lnk”。
查看状态：launchers\WeChat-Resident.cmd Status；本机模拟弹窗：同一入口 TestNotification。
常驻只读两个新群，每 2 秒匹配“收／UA”，首条立即本机提醒，后续密集命中合并；完整摘录保存在 artifacts\wechat-resident\reports。
保活每分钟运行，登录后恢复；停止入口会持久停用。不会自动发微信、调用摘要模型或操作键盘。
说明、限制与复核清单：docs\微信群常驻关键词提醒-20261104.md。

托盘入口更新（2026-11-04）
双击 launchers\微信群关键词提醒-托盘.lnk，程序在右下角通知区运行；图标可能收在“^”内。
悬停显示“微信群关键词提醒”；右键可查看状态、暂停／恢复、打开报告、测试提醒、退出并停止。
日常启动／停止快捷方式已直接调用原生托盘 EXE，Windows 保活不再定时启动 PowerShell 控制台。
详细验证与频率说明：docs\微信群托盘与检查频率-20261104.md。

群消息网页看板（2026-11-05）
打开 http://127.0.0.1:5033/，或双击 launchers\群消息监测看板.lnk。
页面包含状态总览、关键词命中趋势、群聊／关键词／时间／提醒状态筛选、原文详情、本机提醒回执及 Markdown 导出。
看板只读现有 SQLite 和日志，关键词监控继续由原托盘进程负责；关闭网页不会停止记录。
网页服务启停／状态：scripts\Control-WeChatDashboard.ps1 -Action Start / Stop / Status。
服务入口为 pythonw.exe，计划任务每分钟保活并在登录后恢复；完整重启仍待实测。
说明与证据：docs\微信群消息看板-20261105.md，artifacts\wechat-dashboard-20261105。

延迟与跨平台接口评估（2026-11-05）
Codex 小时检查已按用户要求停用，Windows 本地保活继续运行。
当前真实样本的消息时间戳到本机显示约 5.72～7.21 秒；2 秒检查不是两秒送达保证。
企业微信仅评估，未安装／授权或启用发送。报告：docs\监控延迟与企业微信接口评估-20261105.md。

企业微信完整命中转发（2026-11-05，更新前述仅评估状态）
用户已授权转发完整命中；已核验“狐狸的机器人”向授权本人“张虹”发送。
既有 6 条命中已合并为一批获得企微接口接受，后续完整文本自动排队，密集时至少间隔 60 秒合并。
开启／停止：launchers\企微命中转发-启动.lnk、企微命中转发-停止.lnk，均为隐藏控制台的 GUI 入口。
状态：scripts\Control-WeChatWeComForward.ps1 -Action Status；只预览不发送：-Action Preview。
本机看板 http://127.0.0.1:5033/ 已增加企微接受条数、待发分段及最近派发回执。
仍沿用原有唯一采集器；停止企微转发不停止采集，暂停原监控也会暂停企微发送。
说明与复核清单：docs\微信群命中企微转发-20261105.md；证据：artifacts\wechat-wecom-deploy-20261105-cc58fc40。
自然新消息跨平台到达、手机到达/已读、电脑重启及全天运行仍为 UNTESTED。

2026-11-05 当前更新：CX 业务筛选已启用
条件为每人恰好 8.8 万分、人民币每万单价不少于 985 元；先沿用“收／UA”关键词，再判断业务条件。
不符不提醒，信息不足只在本机看板待核实；原文、旧回执保留，其他不含 CX 的命中行为不变。
看板：http://127.0.0.1:5033/#hits；可按“业务待核实／不符合业务规则”筛选。
企微仍每 2 秒查队列：空闲时首条下一轮派发，上次派发后不足 60 秒则合并排队，未擅自提高频率。
本地规则中位耗时 0.0135ms；真实微信到手机两秒内送达未验收，不作保证。
说明和复核清单：docs\微信群CX规则与转发延迟-20261105.md；57 项相关检查通过。

2026-11-05 当前更新：Anode 登录与应用会话
Alpha 原版已正常恢复主桌面；子桌面可使用既有只读副窗口，不要并发启动两个原版写入。
Windows 设置中的 ChatGPT、Alpha 登录启动项已关闭，也会影响主桌面登录自启动；需要时仍可手动打开。
Anode 记住登录 v2 已运行，但普通重连仍曾要求密码；v4 仅完成本地检查，等待本次切换确认，未启用。
不创建新账户、不写死密码。真实免输、重启复用仍待验收。
Codex 的一次重启已查到 Windows 包强制修复；新会话六分钟观察通过，但稍后有原因未明的新启动，尚未完全闭环。
证据与复核：docs\Anode-登录与Codex重启实测-20261105.md。

2026-11-05 22:39 后续实测更新（取代前述 v4 待切换状态）
v4 已获准运行；任务栏旧 Anode 入口已备份并改为与活动版本一致，回读检查通过。
自动启动仍弹 Windows 凭据窗口。用户确认人工登录正常，并最新选择“保留当前窗口，暂不登录”。
当前不再输入、重连或 Stop seat；同会话自动重连待以后手动登录后验收。微信本体未在新子桌面恢复，不能只凭 API 健康声称实时来源在线。
15 分钟独立观察实际捕获 Codex 重启，关闭登录自启动尚不足以解决；详情见上述实测说明。
Alpha 已解除 Codex 包身份继承，保持主桌面原程序与原数据；快捷方式文件本身目标正确、哈希未改。
说明与复核命令：docs\Alpha-快捷方式与Codex分组修复-20261105.md；任务栏视觉归属待用户确认。

2026-11-06 当前更新：Anode 凭据保存修复候选
当前 v4 Session 12 已登录并保持运行；此前等待登录的记录已过时。
v5 已补齐成功后保存及显式复用，本地与发布检查通过，41 个 MCP 工具定义一致。
活动宿主和全局 MCP 入口仍为 v4；候选入口 scripts\Start-AnodeRememberLogin.ps1 -CheckOnly 可只读核验。
尚未切换，真实保存、免输重连和冷启动仍为 UNTESTED；Codex 重启问题仍未闭环。
详情、凭据处理边界和复核清单：docs\Anode-保存凭据流程修复候选-20261106.md。

2026-11-06 连续重连最新状态（取代上面的候选阶段状态）
正式入口已为 v5；真实凭据保存成功，但快速连续重连会出现 -1 / 258。
v6 候选加入断开完成等待并修正错误分类；同一 Session 13 三次诊断重连成功。
正式宿主仍是 v5，尚未换版；工具栏实测与新子会话自动登录仍待验收。Codex 重启仍未解决。
候选只读检查：scripts\Start-AnodeRememberLogin.ps1 -CheckOnly，当前应为 v6 且 blockedByExistingHost=true。
说明、证据和复核清单：docs\Anode-连续重连排查与v6候选-20261106.md。

2026-11-06 v6 正式验收完成（取代上述待切换状态）
v5 已在用户保存工作确认后正常退出；v6 免输密码建立 Session 14，正式工具栏连续三次 Reconnect 全部通过，约 6～7 秒一次。
活动宿主、MCP 活动配置和任务栏入口均为 v6。只读检查 Start-AnodeRememberLogin.ps1 -CheckOnly 应为 blockedByExistingHost=false。
当前 Session 14 保持 ready，Alpha 主桌面原进程未变。Windows 重启后的凭据复用尚未测试。
Codex 在换版后八分钟内未重启，但 09:14 又有新进程，原因未核实，重启问题仍未解决。
正式实测与复核清单：docs\Anode-连续重连排查与v6候选-20261106.md；证据：artifacts\anode-login-repair-20261106-7e839c\v6-live-20261106-0758。

2026-11-06 截图预览新版本候选
新版复用同一预览窗口，左右方向键按当前 Timeline 可见截图切换；发布版测试与真实方向键测试通过。
目前 v6 仍在运行，快捷方式和任务栏的新入口仅已预备，尚未切换；旧版本清理需完整清单和本次口令。
实测与复核：docs\Anode-截图单窗口预览-20261106.md。

2026-11-06 截图预览新版已启用（取代上面的候选状态）
新版已免输密码进入 Session 15，预览单窗、左右键、缩放及边界检查通过；项目入口与任务栏已更新。
指定 5 个旧版目录、20 个文件已按确认移入回收站并逐文件验哈希；v6 留作回退。
真实用户截图点击、任务栏双击和 Windows 重启后的凭据复用尚未实测。Codex 历史间歇重启仍未解决。
说明与复核：docs\Anode-截图单窗口预览-20261106.md。

2026-11-06 旧版本清理与入口迁移完成（取代旧候选入口说明）
当前 Anode 程序与本地 MCP 均以 config\anode-local-mcp.json 为准；旧 MCP 桥和历史启动别名已迁移。
旧版专用的 Activity、Text、ChatReader、Timeline 候选对照测试因旧程序移除不再能按原样运行；旧无触发器烟测任务已禁用。
WorkBuddy 校验脚本仅读取当前 Anode 路径；实际跨桌面校验未在本轮执行。旧版本目录已按清单移入回收站，不代表已释放空间。
详细记录与复核：docs\Anode旧版本清理与入口迁移-20261106.md。
