# Text-first chat reading

This extension adds bounded text recovery to Anode v0.11.3 plus the local Timeline baseline.
The reader does not start a keyword monitor or send notifications. The optional finite
experiment below performs local keyword matching and incremental tracking without an LLM.

## Short messages

Use `seat_observe` with `includeScreenshot=false`. Existing defaults are preserved:

| Option | Default | Range / budget |
| --- | --- | --- |
| `maxNameChars` | 500 | 1–20000 per control; all names share 100000 |
| `maxElementTextChars` | 3000 | 1–20000 per document/value |
| `maxTextChars` | 6000 | 0–20000 across document/value and selection text |
| `screenshotOnTextIssue` | false | Optional image when a returned field is incomplete or at least 3000 characters |

CLI examples, with a current desktop lease as described in [MULTI-AGENT.md](MULTI-AGENT.md):

```powershell
anode windows --query Weixin --json
anode inspect <windowId> --max-name 4000 --max-element-text 6000 --max-text 20000 --json
anode inspect <windowId> --fallback-image issue.png --json
```

Use a new output path for images. `--image` / `--html` still request an image every time;
`--fallback-image` requests one only on a text issue. Missing/hidden messages that the app
does not expose cannot be detected solely from text lengths, so inspect the screen when
the expected message or expand/copy control is absent.

## Distinguish a partial tree from clipped text

`truncated` continues to describe tree traversal. `truncationReasons` names `maxElements`,
`maxDepth` or `timeLimit`. A leaf at the depth limit is not a partial tree.

Each returned `name`, `text` or `selectedText` has matching `nameInfo`, `textInfo` or
`selectedTextInfo` metadata: `source`, `returnedLength`, `length`, `lengthAtLeast`,
`complete` and `reason`. Lengths use UTF-16 code units; clipping preserves surrogate pairs.
`length=null` means the provider was probed with a bound or the field was not read.
`complete=false` also covers an exhausted budget and an unavailable provider. Do not treat
an unread empty string as an empty message. `textIncomplete` aggregates these fields.

`longText` detects at least 3000 exposed characters; `needsTextRecovery` is true for long
or incomplete returned text. The fixed aggregate name budget keeps large trees bounded.

Completeness refers to what UI Automation exposes at that moment. A collapsed chat bubble,
virtualized history or an app-provided abbreviation can still be incomplete as a message.
Tree completeness is independent of text completeness.

## Reread one long control

Use a fresh observation and an offered `read_text` action. It consumes the snapshot and
verifies the control's complete name fingerprint, process and runtime identity again.
It does not focus, select, click, copy or use an LLM.

```powershell
anode element <snapshotId> <elementId> read_text --max-chars 20000 --json
anode element <snapshotId> <elementId> read_text --text-source name --json
```

`textSource` is `auto` (value, then document, then name), `name`, `value` or `document`.
Check the returned `textInfo.complete`; 20000 is a response cap, not a promise of a full
message. Password controls do not offer reading. Inspect again before another action.

## Copy fallback

When UIA does not expose the desired message, use a current seat screenshot to locate an
expand/select/copy control. Record a clipboard checkpoint before the copy:

```powershell
anode window <windowId> clipboard_checkpoint --json
# Locate the intended message and invoke its Copy action or Ctrl+C using existing tools.
anode window <windowId> read_clipboard --after-sequence <sequence> --max-chars 20000 --json
```

MCP uses the same `seat_window` actions and the `afterSequence` / `maxChars` arguments.
Readback refuses an unchanged clipboard, an unknown/different owner process, an empty
Unicode text result or a change during reading. Clipboard helper processes may be refused;
inspect the app rather than relaxing the source check. A matching process does not prove
which message was selected: verify the selected message and returned contents.

These actions never write or clear the clipboard. The explicit copy itself changes it and
may also update the parent clipboard when RDP clipboard sharing is enabled. Native reads
run on an STA thread inside the verified child-session worker.

The sequence and owner checks follow Microsoft's
[GetClipboardSequenceNumber](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getclipboardsequencenumber),
[GetClipboardOwner](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getclipboardowner),
[GetWindowThreadProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowthreadprocessid)
and [Clipboard.GetText](https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.clipboard.gettext?view=windowsdesktop-10.0)
contracts.

## Scope reads to a message list

Whole-window inspection can spend its traversal budget on toolbars before it reaches
messages. An additional read-only worker runs through the existing `exec` interface:

```text
anode exec --timeout 30000 --wait 1000 --json -- <reader.exe> --channel main __chat-read --pid <pid> --window-title <title> --list-name <message-list-name> --max-elements 100 --max-chars 20000 --budget-ms 8000
```

`<reader.exe>` must be a verified Anode build containing `__chat-read`. This worker can
run alongside the existing host without restarting its Windows session. Read job output
with `anode job` and its cursor when the first call returns before completion.
`scripts/read-chat-text.ps1` is an optional wrapper to invoke through `exec`; pass the
worker executable as `-AnodeExecutable` plus `-ProcessId`, `-WindowTitle` and `-ListName`.
Do not run either reader on the parent desktop: both refuse it.

The scoped traversal budget defaults to 4000 ms and accepts 1–20000 ms through
`--budget-ms` / `-BudgetMs`. It is checked between provider calls; the enclosing exec job
enforces the hard deadline. Provider load affects timing, so callers must check `truncated`
and choose a budget/job timeout that fits their task. A larger budget is not a low-latency guarantee.

The worker verifies the session with the existing daemon, selects one native window by
PID/title, and searches for a named List using Anode's .NET UIA client. It reads direct
children's names with the same completeness metadata and a 100000-character total budget.
`truncated` still means the list was not exhausted within the node/time budget. This is
the exposed/virtualized list, not a guarantee of all stored chat history. The returned
indexes are diagnostic only; control actions still need a fresh normal observation.

The root process's session/start identity is checked before and after traversal, so its
identity is reused within one read. Embedded controls from other processes still receive
their own session checks. `processCheckMs` reports that validation cost for scoped reads.
No focus, screenshot, clipboard operation or LLM call is performed by the worker.

Add `--tail` (or `-Tail` in the wrapper) to read the newest requested direct children,
returned in chronological list order. `windowComplete` means that the requested tail
window was read or the list ended. It does not mean that all history was read.
`olderItemsOmitted=true` and `truncated=true` are expected when older rows remain outside
that window; `truncationReason=timeLimit` still requires attention. Check each field's
`textInfo.complete` separately.

`elementKey` is a hash of the UIA runtime ID for diagnostics, never a message ID or an
action reference. The installed WeChat provider reused those IDs for different message
rows during live testing. Do not deduplicate chat messages solely by this field.

During live checks on this machine, Windows PowerShell's legacy UIA client saw a shallower
WeChat tree than Anode's .NET client. The wrapper therefore delegates extraction to Anode;
its behavior must be checked again if the installed app/runtime changes.

## Finite monitoring experiment

`scripts/measure-chat-monitor.py` measures an already-open chat through the existing
Anode CLI/exec path. It requires Windows, Python, a ready verified child session, an
existing desktop lease and a reader build supporting `--tail`. It never starts a seat,
sends chat input, copies text or calls an LLM. It stops after the configured duration,
a new `stop.flag` file in its output directory, or three consecutive runtime errors.

```text
python -B scripts/measure-chat-monitor.py --self-test
python -B scripts/measure-chat-monitor.py --config <absolute-config-path>
```

The config is JSON with `root`, `anode` (the active CLI executable), `baselineReader`,
`reader`, `agent`, `session`, `pid`, `windowTitle`, `listName`, `keywords`, `tailCount`,
`durationSeconds`, `baselineSamples` and a new `output` directory. Paths must be absolute.
The lease token is supplied as one JSON line on stdin with the `leaseToken` field; it is
never stored in the config or evidence. The experiment releases its lease when it exits.

Setup takes full-list baseline samples and seeds the latest tail without counting history
as new. Subsequent polls compare ordered text suffix/prefix overlap. A lost overlap causes
one larger tail read; if continuity is still absent, the batch is marked `unresolved` with
a coverage gap. Runtime IDs are ignored for message tracking. Consecutive identical text
and provider omissions can still hide changes; this is not a lossless chat archive.
List items may include time separators or system text, so event counts describe text
items, not necessarily individual chat messages. Changed older text is not guessed to be
an edit.

Newly observed text items are appended to `events.jsonl` before taking one screenshot per
changed batch. Keyword matching is case-insensitive substring matching over the exposed
name, including any sender/system text exposed by the app. It is a demonstration matcher.
Capture stops at 100 images or once retained screenshots reach 100 MiB; the final image
may cross the byte threshold. Skipped captures are recorded, and text logging continues.
Snapshots, polls, errors, capture hashes and progress are retained in
the local output directory; no external notification is sent.

`summary.json` reports end-to-end read time, traversal time, cycle intervals, matching/log
time, and detection-to-screenshot time separately. It does not measure the time from the
message's network arrival to detection. CLI startup, exec IPC, UIA calls, capture and
machine load all affect the measured polling frequency. The final in-flight poll may
finish after the requested duration. Review `errors`, `partialPolls`, `coverageGaps` and
`stopReason` before accepting the result.

## Verification and activation

### Resident keyword monitoring

`scripts/watch-chat-monitor.py` is the long-running alternative to launching one reader
per poll. It holds a verified list reference and requests fresh tail properties on each
cycle. This is polling, not a promise that the application provides new-message events.
An optional `--bulk` read uses [UIA child caching](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/caching-in-ui-automation-clients).

The resident `__chat-read` arguments are `--watch-ms` (1–1200000), `--interval-ms`
(1–10000) and a new absolute `--stream` path. Use `--max-elements 6` for the hot path.
The interval is a lower bound; provider calls and verification can take longer.
The worker writes snapshots on changes and small heartbeat records otherwise. A new
`<stream>.stop` file stops it; appending `catchup` and a newline to `<stream>.requests`
asks for a bulk read of up to 500 exposed children. Each stream is limited to 64 MiB.
The supervisor rotates bounded exec jobs, checks status, renews its lease, and restarts
known failed readers with backoff. A SYNCHRONIZE-only [process wait handle](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject)
detects an owned worker exit without waiting for the heartbeat watchdog. An uncertain launch is recovered by owned job ID or
stops without replay. Human control or a lost lease stops the monitor.

The supervisor config contains the finite experiment's executable/session/target fields,
plus `parentSession`, `stateDirectory`, `output`, `intervalMs`, `segmentMs` and
`durationSeconds` (0 keeps running until stopped). The existing lease token is one JSON
line on stdin and is never saved. `output` must be new; reuse `stateDirectory` only for
the same conversation. Stop with a new `stop.flag` in `output`. Optional `processName`
rebinds a restarted application only when one window exactly matches both title and
process name inside the verified child. A Windows-session change needs the launcher
again with the new verified session. No login or seat startup is automated.

Changed text and notification/capture tasks are committed to an append-only journal and
flushed to disk before dispatch. Separate queues keep capture work out of notification
dispatch. Journal replay restores pending tasks; interrupted file tails are retained in
their original files. A retained OS-locked `owner.lock` prevents concurrent writers.
A crash between notification display and its receipt can cause a repeated reminder;
exactly-once human delivery is not promised. Long-term timing samples are bounded to
10000 entries. Logs and images are never automatically deleted. Screenshot count/byte
budgets stop further capture while text logging and notification continue.

Desktop notifications use an opt-in parent-session helper, independent of the Viewer.
It verifies its session with the daemon and exits when its owner or stop flag ends.
The helper records `requested` after calling the Windows API and `shown` when Windows
raises [BalloonTipShown](https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.notifyicon.balloontipshown?view=windowsdesktop-10.0).
These are not proof that a person read the alert. Windows settings can suppress or merge
balloons; the journal retains every matched text item even when one batch has one popup.

Tail continuity uses ordered text, never recycled runtime IDs. Lost overlap requests a
larger read before generating alerts; unresolved coverage is reported rather than turning
old history into new alerts. Partial fields trigger a warning and catchup. This monitor
does not automate clipboard recovery for fields that remain incomplete. UIA-hidden,
collapsed or omitted text and sufficiently large identical-message bursts remain limits.
The first arrival after an empty baseline is still new. An unexpectedly empty window
does not erase a previously established checkpoint.

`scripts/start-keyword-monitor.ps1 -Config <config> -CheckOnly` verifies paths, hashes and
the existing ready/view-only seat. Its launcher config adds `monitorScript`, `monitorSha256`,
`readerSha256`, and `runsDirectory` to the target/settings fields above. Without `-CheckOnly`
it dispatches an on-demand Windows task into the verified parent session, allowing the
monitor to outlive its calling chat. It uses the existing interactive identity, no stored
password, and no logon/boot trigger. A matching task is reused; a differing task is never
overwritten. Task definitions and run directories are retained rather than deleted.
An existing state lock/mutex prevents a duplicate collector. The private `__chat-lease`
command refuses to start a daemon or stopped seat, even when acquiring the initial lease.

`scripts/stop-keyword-monitor.ps1 -Config <config>` verifies each live process's script
and runtime config before creating its stop flag. It does not stop the Viewer, log out
Windows, terminate WeChat or delete task definitions. Stop before editing target settings
or changing Windows sessions, then start again. The monitor holds the desktop lease while
active; stopping it returns the seat to other agents. Human takeover also ends the run.

Run `python -B scripts/watch-chat-monitor.py --self-test --out <new-absolute-directory>`
for retained-journal, crash-tail, repeated-text, catchup and CLI-status checks.
`scripts/test-chat-monitor-live.py` accepts a config with `root`, `anode`, `reader`,
`agent`, `session`, `parentSession`, `keywords`, `testOutput` (new), and optional
`injectReaderFailure`. Supply the current lease token on stdin. It creates an owned
child-session fixture, waits for monitoring to be ready, publishes 28 known items
(including repeats, a 20-item burst and a 5000-character message), verifies exact text
counts, keyword hits, notifications and screenshot hashes, and closes only its own jobs.
It never sends a real chat message. Fixture latency does not measure WeChat arrival time.

```powershell
& .\anode.exe __text-check | Out-Host
& .\anode.exe __timeline-check --out 'C:\new-absolute-evidence-directory' | Out-Host
```

Expected markers are `ANODE_TEXT_CHECKS_OK`, `ANODE_TIMELINE_CHECKS_OK` and
`ANODE_ACTIVITY_CHECKS_OK`. Use a new native Windows path for Timeline evidence. Text checks
use hidden fixture windows, injected clipboard readers, and native clipboard metadata;
they do not read/write the user's clipboard contents, send input or start a live seat.

The existing full `selftest --quick` also deletes temporary fixture files. It was not run
under the user's no-deletion rule; focused retained-fixture checks cover this change.
Real WeChat reading, real copy recovery and screenshot triggering require a running seat.
New `seat_observe` / `seat_element` / `seat_window` options require a candidate host that
supports them. The scoped `__chat-read` worker uses existing exec jobs and does not require
replacing an already-running host.
