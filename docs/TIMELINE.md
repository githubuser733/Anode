# Operation timeline

This local extension adds an optional Timeline panel to the native Anode viewer.
It was inspired by the activity timeline in [BgDesk](https://github.com/enwong93-sketch/background-computer-use).
The Anode request path, seat ownership and pointer guard remain the input boundary.

The panel shows Agent, Human and CLI activity, control handoffs, running and completed
operations, duration and available screenshots. Human observations are scoped to input
messages sent to this viewer while its visible, focused window has control and Windows
sign-in has completed. They do not consume messages, inject input or install global hooks.
Text is represented by character or key-event counts. Request bodies, lease tokens,
command arguments and raw error text are not written to the timeline.

Screenshot previews use images already returned by Anode tools. A row is not a promise
that a new screenshot was taken immediately after that operation. Human rows do not
automatically capture the screen. Activity outside Anode's request path or native viewer
is not observed.

Each daemon writes a new append-only JSONL journal under its state directory's `timeline`
folder. The project launcher supplies an artifact directory as `--state-dir`. Up to 500
recent activities are held in memory, 300 displayed, and the latest eight journals of
at most 32 MB each are replayed when opening the viewer. Older files are retained.
Interrupted operations are shown as having unknown effects; history is never replayed
as input. The UI reports persistence errors or unqueued events. Journaling is asynchronous
and a logging failure does not change the tool response or the Stop path.

Timeline is collapsible. Enabling it reserves a minimum viewer width of 900 logical pixels;
collapsing it restores the original 560-pixel minimum. Existing desktop sizing and pointer
protection remain in use.

## Verification

The private `__timeline-check --out ABSOLUTE_NEW_DIRECTORY` command checks the model,
the actual daemon entry point, redaction, persistence, concurrency, retained evidence,
human message filtering and coordinate conversion. It creates neither a live seat nor
real input. The same check is registered in `selftest`.

`__viewer-preview --timeline --out NEW_DIRECTORY` renders sample data in cloaked windows;
it does not install the human input observer or connect to a seat. These are design previews,
not evidence of a live agent or human handoff. Real viewer input still requires a live test.

The parent project's `Test-AnodeTimeline.ps1 -Preview` also compares all 41 MCP tool
definitions with the verified original release. It retains every generated test artifact.

The original `selftest --quick` and `build.ps1 -QuickTest` perform permanent temporary-file
cleanup. They were not run in this workspace under its explicit no-deletion rules.
Use the retained-evidence checks above for the validated scope, and treat full upstream
self-tests and live acceptance as outstanding until separately completed.
