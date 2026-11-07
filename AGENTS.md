# Working on Anode

Anode is a Windows-only .NET 10 application. One executable implements the CLI,
WinForms/RDP daemon, child-session host, setup and MCP stdio server.

## Build and verify

Run from the repository root in PowerShell:

```powershell
dotnet build Anode.sln --nologo
& .\src\Anode\bin\Debug\net10.0-windows\win-x64\anode.exe selftest --quick | Out-Host
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -QuickTest
```

The last command publishes and tests `dist\anode.exe`. Build output is ignored by
Git. Use the freshly built executable, since an existing `dist` may be stale.
An Anode or MCP client running from `dist\anode.exe` locks it; then build elsewhere with
`scripts\build.ps1 -QuickTest -OutputDirectory artifacts\pkg-build` (add
`-ArchiveDirectory artifacts\pkg-release -Package` to test the package, see
[CONTRIBUTING.md](CONTRIBUTING.md#build-while-anode-is-running)). Use a separate
`git worktree` for parallel work so builds do not share `src\Anode\obj` and `bin`.
Pipe GUI-executable output to `Out-Host` so PowerShell waits and displays it;
check `$LASTEXITCODE` for failures. There is no separate `dotnet test` project:
regression checks live in `Cli/SelfTest.cs`, `Cli/TransportChecks.cs` and `Cli/McpChecks.cs`.

`selftest --quick` uses private test pipes and a disposable MCP subprocess. It
does not create a seat or inject input. Full `selftest` also captures the current
desktop, launches a scheduler test process and exercises a machine-wide gamepad;
choose it only when those actions are appropriate to the task.

Debug builds are the `dev` channel, with their own pipes, daemon mutex, logs
(`%LOCALAPPDATA%\Anode-dev`) and viewer title, so they cannot reach or stop the
user's Anode. Release builds (`dist`, `artifacts\pkg-build`) are the main channel
and share the installed Anode's `anode-control` pipe: never run `start`, `up`,
`lease` or any other seat command from one while the user's daemon runs; put
`--channel dev` before the command instead. Windows allows one seat per session,
so a dev seat refuses to start while the user's seat runs; a live check needs the
user to close their seat first. `selftest --quick` is private-pipe only and stays
safe. See [a separate dev Anode](docs/DEVELOPMENT-TESTING.md#a-separate-dev-anode).
Read a command's usage with `anode help <command>`, which never dispatches it.

`doctor` reads prerequisites and exits nonzero when setup is missing. Live seat
tests require Remote Desktop and child sessions. `setup` changes machine settings
through UAC; do not treat a missing setup as a failed build.

## Design constraints

- Preserve the seat host's refusal to run in an unverified or parent session.
  The parent daemon resolves child-session identity; the host verifies it before
  serving input. Keep input and capture inside the seat host.
- Keep `Daemon/PointerGuard.cs` installed before the viewer connects. The Remote Desktop control
  moves the real pointer when a seat program calls `SetCursorPos` and the pointer is over the
  viewer's rectangle, even hidden or view-only; the guard lets that through only while the user has
  taken control in a visible, focused viewer. A live test is meaningless unless the real pointer is
  inside that rectangle; `scripts/test-pointer-isolation.ps1` handles this.
- Keep CLI and MCP daemon startup on the shared `Core/Launch/DaemonLauncher.cs`
  path so Task Scheduler can detach the daemon from an agent's process lifetime.
  Only `seat_start` and `seat_lease` acquisition start a daemon over MCP, and only
  `start`, `up` and `lease acquire` on the CLI; status and diagnostics never do.
- Keep MCP stdout exclusively newline-delimited JSON. Diagnostics go to the log
  or stderr. MCP tool discovery must work without machine setup or a live seat.
- Keep protocol processing independent of long tool calls. Ordinary tools execute
  in order; Stop uses a separate daemon connection and cancels outstanding work.
  Validate arguments before any daemon startup or action. Test MCP scheduling
  with injected handlers and private pipes, without creating a real seat.
- Pipe deadlines include queue time. Never replay a timed-out input command:
  its effects may already have happened. Reconnect before subsequent requests.
- Keep Stop able to cancel startup and avoid waiting on long input operations.
- Screenshot click coordinates use the original screen size, even when the
  returned image is smaller. Gamepads are visible across Windows sessions unless HidHide jails
  them to the seat (`Core/Gamepad/PadIsolation.cs`); list a pad before it exists, since HidHide
  checks opens, not open handles.

See `docs/ARCHITECTURE.md`, `docs/PROTOCOL.md`, `docs/SECURITY.md` and
`docs/CONNECTING-AGENTS.md` for the runtime model and client setup.

## Live testing without disturbing the user

Use Anode's CLI/MCP tools for seat screenshots, mouse, keyboard and application
launches, holding a desktop lease (`seat_lease` or `anode lease acquire`); each desktop
command extends it, so renew only across long pauses, and release it after cleanup so a waiting
agent gets its turn. The user explicitly wants their
foreground desktop left available.
Do not invoke foreground computer use, activate the parent viewer, or show it as
a fallback when seat capture fails. Diagnose that failure through code and logs;
stop input that requires a fresh screenshot. Show the viewer only when the user
requests it or agrees to a specific interactive step. Virtual gamepads are
machine-wide without HidHide and require separate consideration before input tests.

Use `windows`/`inspect` or `seat_windows`/`seat_observe` for accessible controls.
Control actions consume their snapshot; inspect again instead of replaying an
uncertain action. `scripts/test-desktop.ps1` is an opt-in test using a disposable
fixture in an already ready seat; it does not start the seat or show its viewer.

`seat_exec`/`exec` runs noninteractive builds and tests with bounded command jobs.
Use an absolute cwd, read output incrementally with `seat_job`/`job`, and cancel only
your own jobs. Recover IDs with `jobs` after an interrupted request; never replay an
uncertain start. `seat_wait` provides bounded UI predicates and a fresh snapshot on
match. `scripts/test-development.ps1 -VerifyInput` tests an owned browser in the seat,
including real mouse/keyboard delivery; it requires Node, Chrome and local Playwright.
`capabilities` reports actual capture and known input blockers. A ready connection or
successful SendInput call alone does not prove that an application received input.

## 本机双仓库路径

本机工作先读取 `../AGENTS.md` 的用户保护规则及 `LOCAL.md`。上文上游构建示例仍保留；本机 SDK 在 `tools/`，集成工具与活动运行清单在 `operations/`。不得用历史日期目录替代 Git 版本管理，亦不得将运行数据、凭据或日志纳入提交。

人工日常启动只使用仓库根目录 `Anode.lnk`，复用活动清单及已运行实例。配套入口归类在 `operations/launchers/apps`、`monitoring`、`maintenance`；不要重新创建并列的 Start/Show/Hide/Timeline/MCP 主入口。`Initialize-ProjectLaunchers.ps1 -CheckOnly` 核验当前清单；`Consolidate-ProjectEntries.ps1` 是已执行的一次性迁移，不用作日常启动或版本发布。

## UU 日常免提权（2026-11-07）

原 `operations/launchers/maintenance/UU-Console-Enable.cmd` 已改为普通权限入口；完整绑定时不重启 worker，需要重绑时请求已安装的固定 SYSTEM 任务。Enable/Restore-v1 两任务只按需运行，没有开机/登录/定时触发器。不要恢复日常 `Start-Process -Verb RunAs`，也不要关闭 UAC 或放宽原服务权限。首次安装已成功，不重复触发 UAC。

固定副本 `operations/work/uu-console-task-v1/scripts/` 与任务输出目录由管理员控制；任务启动时锁定并核对依赖哈希。源脚本更新不能直接覆盖部署副本或原任务，需另做明确的部署升级。当前只读核对过 UU 4.46.0.4716 的控制分支；写操作继续保留版本门禁。

本轮普通 CMD 和普通令牌请求 SYSTEM Enable 均成功，当前正确绑定时为 noChangeNeeded=true；13 项行为检查与项目检查通过，UU/Anode 原进程保持。三个 CMD 还在自己的子进程隔离 PowerShell 7 模块路径污染，不修改父进程或系统环境。新版真实错绑后的 Apply/Restore、远端重连键鼠和电脑重启仍为 UNTESTED，不能把任务退出 0 当作远控已验收。说明与复核命令：`operations/docs/UU-日常免管理员启动-20261107.md`；证据：`operations/artifacts/uu-console-nonadmin-20261107-7a280d75/`。
