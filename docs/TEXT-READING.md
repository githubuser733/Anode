# Text-first chat reading

This extension adds bounded text recovery to Anode v0.11.3 plus the local Timeline baseline.
It does not start a keyword monitor or send notifications. Use local keyword matching and
incremental message tracking before involving an LLM.

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

During live checks on this machine, Windows PowerShell's legacy UIA client saw a shallower
WeChat tree than Anode's .NET client. The wrapper therefore delegates extraction to Anode;
its behavior must be checked again if the installed app/runtime changes.

## Verification and activation

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
