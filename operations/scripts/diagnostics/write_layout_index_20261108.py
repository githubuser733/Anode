"""Write a new human-readable layout index from the verified move receipt."""
import json
from pathlib import Path

OPS = Path(__file__).resolve().parents[2]
REPORT = OPS / 'artifacts/directory-layout-20261108'
receipt = json.loads((REPORT / 'receipt.json').read_text(encoding='utf-8'))
plan = json.loads((REPORT / 'plan-v2.json').read_text(encoding='utf-8'))
assert receipt['verified'] and receipt['completed'] == plan['moves']
lines = ['# work / artifacts 用途与历史路径索引', '',
         '本次只做可逆归档，没有删除文件，没有释放磁盘空间。活动程序、上一运行版、应用配置和监控数据库保留原路径。', '',
         '## 日常入口', '',
         '- Anode：仓库根目录 `Anode.lnk`。',
         '- 应用：`operations/launchers/apps/`。',
         '- 监控与看板：`operations/launchers/monitoring/`。',
         '- UU：`operations/launchers/maintenance/UU-Console-Enable.cmd`、`UU-Console-Restore.cmd`；Setup 是安装维护入口，不要日常重复运行。',
         '- 维护目录新增 UU 当前记录、历史材料、此说明的三个只读快捷方式。',
         '- OpenCode 配置里的原程序不存在，预检查返回 `Installed application not found`，未部署不可用入口。', '',
         '## work 保留原因', '',
         '| 目录 | 用途 |', '| --- | --- |',
         '| anode-current | 当前运行 EXE，活动清单 config/anode-local-mcp.json |',
         '| anode-screenshot-viewer-20261106-734d2b | 上一运行版，config/anode-previous.json |',
         '| multiopen | Notion、TRAE、OpenCode 等隔离运行数据；当前配置仍引用，不能整体删除 |',
         '| alpha-readonly-guest | 当前只读副窗口数据，config/alpha-readonly.json |',
         '| grokbot-multiopen | GrokBot 的配置运行目录 |',
         '| recheck-20261031-shared | 当前 WorkBuddy Electron 数据 |',
         '| recheck-20261031-0206 | 旧隔离应用数据，旧归档清单中仍出现；本轮未深入逐项审核，保留 |',
         '| grokbot-comparison-20261031 | 旧任务仍引用，本轮保留 |',
         '| uu-console-task-v1 | 固定 SYSTEM 任务部署副本，不是 launcher 的冗余备份 |',
         '| wechat-resident-tray-20261104-224309-1f8eaf46 | 正在使用的托盘 EXE |',
         '| sources | 空目录，本轮没有执行删除 |', '',
         '## artifacts 分类', '',
         '- `wechat-resident`、`wechat-dashboard`、`wechat-wecom`、`wechat-monitor-*`：状态、消息去重、报告、任务日志；不重置。',
         '- `anode-timeline-20261031/runtime-state`：当前 Anode 会话状态，不搬动。',
         '- `uu-console-task-v1`：当前 UU 任务输出，部署路径由 config/uu-console-task.json 指定。',
         '- `history/consolidated-20261108/evidence`：本次集中归档的 60 个旧证据目录。',
         '- `history/consolidated-20261108/inactive-application-data`：旧 Alpha 浏览器数据与下载的参考文档。原数据完整保留。',
         '- `recovery`、`restructure-20261106`、`migration-backups`、`history-20261031`：历史恢复材料与已校验归档。',
         '- 其他原位目录：脚本/配置/任务仍有引用，详细原因见 plan-v2.json 的 retained 字段。被诊断脚本引用不代表当前生产程序必须依赖，仍需逐项审核后再清理。', '',
         '## 历史路径变化', '',
         '旧报告内记录的原绝对路径不修改。查找旧证据时按以下映射定位；历史诊断脚本不能未经复核重新运行。', '',
         '| 原路径（相对 operations） | 新路径（相对 operations） | 文件数 |',
         '| --- | --- | ---: |']
for entry in receipt['completed']:
    lines.append(f"| `{Path(entry['source']).relative_to(OPS)}` | `{Path(entry['destination']).relative_to(OPS)}` | {entry['files']} |")
lines += ['', '## 复核', '',
          '`powershell.exe -NoProfile -ExecutionPolicy Bypass -File operations/scripts/Test-ProjectSetup.ps1` 应返回 `PROJECT_SETUP_OK`。',
          '归档逐文件清单及 SHA256 位于 `operations/artifacts/directory-layout-20261108/receipt.json`；62 个目录、11,056 个文件、724,491,407 字节已逐文件验证。',
          '没有实机重开应用或执行 UU 切换；这些交互不属于本次整理验收范围。']
target = OPS / 'docs/目录用途与归档索引-20261108.md'
with target.open('x', encoding='utf-8') as stream:
    stream.write('\n'.join(lines) + '\n')
print(f'LAYOUT_INDEX_OK moves={len(receipt["completed"])}')
