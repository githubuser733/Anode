using System.Drawing.Imaging;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Anode.Core.Timeline;
using Anode.Daemon;

namespace Anode.Cli;

/// <summary>Private, no-seat checks. Test evidence is retained; nothing is deleted or sent as real input.</summary>
internal static class TimelineChecks
{
    private static int _checks;
    private static void Require(bool value, string detail)
    {
        if (!value) throw new InvalidOperationException(detail);
        _checks++;
    }

    public static int Run(string[] args)
    {
        ConsoleBridge.Attach();
        _checks = 0;
        try
        {
            string root = new Args(args).Value("out") ?? throw new ArgumentException("--out requires a new absolute project evidence directory.");
            if (!Path.IsPathFullyQualified(root) || Directory.Exists(root) || File.Exists(root))
                throw new ArgumentException("Evidence directory must be absolute and new; existing files were left unchanged.");
            Directory.CreateDirectory(root);
            if (args.Contains("--capture-probe")) return ActivityChecks.ProbeAsync(root).GetAwaiter().GetResult();
            if (args.Contains("--responsiveness-only"))
            {
                Application.EnableVisualStyles();
                int checks = RunUiChecks(() => ActivityResponsivenessChecks.Run(root));
                Console.WriteLine($"ANODE_ACTIVITY_RESPONSIVENESS_OK checks={checks} inputSent=false liveSeatStarted=false");
                return 0;
            }
            ModelAsync(root).GetAwaiter().GetResult();
            int activityChecks = ActivityChecks.RunAsync(root).GetAwaiter().GetResult();
            Application.EnableVisualStyles();
            int uiChecks = RunUiChecks(() =>
            {
                HumanMessages(root);
                HumanThreadMessages(root, !args.Contains("--managed-only"));
                return ActivityChecks.Ui(root);
            });
            using var report = new FileStream(Path.Combine(root, "result.json"), FileMode.CreateNew, FileAccess.Write, FileShare.Read);
            JsonSerializer.Serialize(report, new { status = "ANODE_TIMELINE_CHECKS_OK", checks = _checks, activityChecks = activityChecks + uiChecks, realInputSent = false, liveSeatStarted = false });
            Console.WriteLine($"ANODE_TIMELINE_CHECKS_OK checks={_checks} realInputSent=false liveSeatStarted=false");
            Console.WriteLine($"ANODE_ACTIVITY_CHECKS_OK checks={activityChecks + uiChecks} inputSent=false liveSeatStarted=false");
            return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex.ToString()); return 1; }
    }

    private static int RunUiChecks(Func<int> checks)
    {
        // DoEvents alone tears down the WinForms synchronization context on return.
        // Keep a real message loop, as the viewer does, so async previews resume on the UI thread.
        // A hidden dispatcher control is enough; no form, seat or real input is needed.
        using var context = new ApplicationContext();
        using var dispatcher = new Control();
        _ = dispatcher.Handle;
        int result = 0;
        Exception? failure = null;
        dispatcher.BeginInvoke(new Action(() =>
        {
            try { result = checks(); }
            catch (Exception ex) { failure = ex; }
            finally { context.ExitThread(); }
        }));
        Application.Run(context);
        if (failure is not null) System.Runtime.ExceptionServices.ExceptionDispatchInfo.Capture(failure).Throw();
        return result;
    }

    internal static string ForSelfTest()
    {
        string evidence = Path.Combine(Path.GetTempPath(), "anode-timeline-check-" + Guid.NewGuid().ToString("N"));
        if (Run(["--out", evidence]) != 0) throw new InvalidOperationException("Timeline checks failed; evidence: " + evidence);
        return $"{_checks} checks; evidence retained at {evidence}";
    }

    private static async Task ModelAsync(string root)
    {
        const string secret = "DO_NOT_LOG_SECRET_TIMELINE_20261031";
        string journal = Path.Combine(root, "journal");
        long count;
        using (var timeline = new OperationTimeline(journal, 500))
        {
            timeline.Changed += () => throw new InvalidOperationException("A view listener failed.");
            int calls = 0;
            var response = new JsonObject { ["ok"] = true };
            var reply = await timeline.TrackAsync(new JsonObject { ["op"] = "status" }, () => { calls++; return Task.FromResult(response); }).ConfigureAwait(false);
            Require(calls == 1 && ReferenceEquals(reply, response) && timeline.Snapshot().Count == 0, "Polling was logged or changed by the recorder.");
            var typed = new JsonObject { ["op"] = "input.text", ["agentId"] = "check-agent", ["agentName"] = "Test agent", ["leaseToken"] = secret, ["text"] = secret };
            reply = await timeline.TrackAsync(typed, () => Task.FromResult(response)).ConfigureAwait(false);
            Require(ReferenceEquals(reply, response) && timeline.Snapshot().Single().Summary == $"Typed {secret.Length} characters", "Typing metadata or response identity changed.");
            await timeline.TrackAsync(new JsonObject { ["op"] = "input.click", ["agentId"] = "check-agent", ["x"] = 123, ["y"] = 456 },
                () => Task.FromResult(new JsonObject { ["ok"] = false, ["error"] = secret })).ConfigureAwait(false);
            Require(timeline.Snapshot().Last().Outcome == "failed" && timeline.Snapshot().Last().Summary.Contains("123,456"), "A failed click was reported as successful.");
            try { await timeline.TrackAsync(new JsonObject { ["op"] = "input.click" }, () => Task.FromException<JsonObject>(new OperationCanceledException())).ConfigureAwait(false); }
            catch (OperationCanceledException) { }
            Require(timeline.Snapshot().Last().Outcome.Contains("unknown"), "Cancellation claimed that input had no effects.");
            timeline.Control(true);
            timeline.Human("input.click", "left click at 960,540");
            timeline.Control(false);
            Require(timeline.Snapshot().Count(e => e.Actor == "Human") == 3, "Human control boundaries were not retained.");
            var finished = new TaskCompletionSource<JsonObject>(TaskCreationOptions.RunContinuationsAsynchronously);
            var inFlight = timeline.TrackAsync(new JsonObject { ["op"] = "input.text", ["text"] = secret }, () => finished.Task);
            int inFlightCount = timeline.Snapshot().Count;
            Require(timeline.Snapshot().Last().Outcome == "running", "An in-flight operation was invisible until it completed.");
            finished.SetResult(new JsonObject { ["ok"] = true });
            await inFlight.ConfigureAwait(false);
            Require(timeline.Snapshot().Count == inFlightCount && timeline.Snapshot().Last().Outcome == "ok", "Completion did not update its existing activity.");

            using var bitmap = new Bitmap(16, 16);
            using var image = new MemoryStream();
            bitmap.Save(image, ImageFormat.Png);
            await timeline.TrackAsync(new JsonObject { ["op"] = "screenshot", ["agentId"] = "check-agent" }, () => Task.FromResult(new JsonObject {
                ["ok"] = true, ["result"] = new JsonObject { ["mimeType"] = "image/png", ["data"] = Convert.ToBase64String(image.ToArray()) }
            })).ConfigureAwait(false);
            Require(await timeline.FlushAsync().ConfigureAwait(false), "Journal did not flush.");
            var shot = timeline.Snapshot().Last();
            Require(timeline.ScreenshotPath(shot) is { } path && File.Exists(path), "An existing screenshot was not preserved.");
            Require(timeline.ScreenshotPath(shot with { Screenshot = "..\\secret.txt" }) is null, "Preview escaped the timeline directory.");
            string contents = ReadJournal(timeline.JournalPath);
            Require(!contents.Contains(secret) && !contents.Contains("leaseToken") && !contents.Contains(Convert.ToBase64String(image.ToArray())), "Sensitive request, error or binary contents leaked into JSONL.");
            var concurrent = Enumerable.Range(0, 100).Select(i => Task.Run(() => timeline.Human("input.click", "check " + i)));
            await Task.WhenAll(concurrent).ConfigureAwait(false);
            Require(await timeline.FlushAsync().ConfigureAwait(false), "Concurrent records did not flush.");
            var rows = timeline.Snapshot();
            Require(rows.Select(e => e.Id).Distinct().Count() == rows.Count && rows.Zip(rows.Skip(1)).All(pair => pair.First.Sequence < pair.Second.Sequence), "Concurrent order or IDs collided.");
            count = rows.Count;

            using var daemon = new AnodeDaemon(new SeatOptions(), findChild: () => null, blockingSummary: () => null, timeline: timeline);
            var denied = await daemon.HandleControlAsync(new JsonObject { ["op"] = "input.click", ["agentId"] = "check-agent", ["leaseToken"] = secret, ["x"] = 1, ["y"] = 2 }).ConfigureAwait(false);
            Require(denied["ok"]?.ToString() == "false" && timeline.Snapshot().Count == count + 1 && timeline.Snapshot().Last().Actor == "Agent", "The real daemon request path did not record its rejected operation.");
            count++;
            Require(await timeline.FlushAsync().ConfigureAwait(false), "Daemon record was not persisted.");
        }
        using (var history = new OperationTimeline(journal))
            Require(history.Snapshot().Count == count, "Restart did not restore persisted records.");
        using (var bounded = new OperationTimeline(Path.Combine(root, "bounded"), 3))
        {
            for (int i = 0; i < 7; i++) bounded.Human("input.click", i.ToString());
            Require(bounded.Snapshot().Count == 3 && bounded.Snapshot()[0].Summary == "4", "Memory retention was unbounded or discarded the newest entries.");
            Require(await bounded.FlushAsync().ConfigureAwait(false), "Bounded memory lost durable records.");
            Require(ReadJournal(bounded.JournalPath).Split('\n', StringSplitOptions.RemoveEmptyEntries).Length == 7, "Memory retention deleted historical records.");
        }
        string blocked = Path.Combine(root, "not-a-directory");
        using (var file = new FileStream(blocked, FileMode.CreateNew)) file.WriteByte(1);
        using (var unavailable = new OperationTimeline(Path.Combine(blocked, "child")))
        {
            var reply = await unavailable.TrackAsync(new JsonObject { ["op"] = "input.click" }, () => Task.FromResult(new JsonObject { ["ok"] = true })).ConfigureAwait(false);
            Require(reply["ok"]?.ToString() == "true", "A logging failure changed the operation's response.");
            Require(!await unavailable.FlushAsync().ConfigureAwait(false) && unavailable.WriteError is not null && unavailable.Snapshot().Count == 1, "A logging failure was hidden or lost the in-memory record.");
        }
    }

    private static void HumanMessages(string root)
    {
        using var timeline = new OperationTimeline(Path.Combine(root, "human"));
        using var viewer = new Panel { Size = new Size(960, 540) };
        using var other = new Panel();
        _ = viewer.Handle;
        _ = other.Handle;
        bool control = false;
        using var recorder = new HumanInputRecorder(viewer, () => control, () => new Size(1920, 1080), timeline, () => Keys.None);
        using (var paintGuard = new HumanInputRecorder(viewer, () => throw new InvalidOperationException("Non-input message inspected control state."), () => viewer.Size, timeline))
        {
            var paint = Message.Create(viewer.Handle, 0x000F, IntPtr.Zero, IntPtr.Zero);
            Require(!paintGuard.PreFilterMessage(ref paint), "Painting was intercepted by input recording.");
        }
        var click = Message.Create(viewer.Handle, 0x0201, IntPtr.Zero, new IntPtr((270 << 16) | 480));
        Require(!recorder.PreFilterMessage(ref click) && timeline.Snapshot().Count == 0, "View-only input was recorded or intercepted.");
        control = true;
        var foreign = Message.Create(other.Handle, 0x0201, IntPtr.Zero, IntPtr.Zero);
        recorder.PreFilterMessage(ref foreign);
        Require(timeline.Snapshot().Count == 0, "Input from another window was recorded.");
        var up = Message.Create(viewer.Handle, 0x0202, IntPtr.Zero, click.LParam);
        Require(!recorder.PreFilterMessage(ref click) && !recorder.PreFilterMessage(ref up) && timeline.Snapshot().Last().Summary.Contains("960,540"), "Human click geometry or passthrough is incorrect.");
        recorder.PreFilterMessage(ref click);
        var dragUp = Message.Create(viewer.Handle, 0x0202, IntPtr.Zero, new IntPtr((300 << 16) | 600));
        recorder.PreFilterMessage(ref dragUp);
        Require(timeline.Snapshot().Last().Operation == "input.drag", "A human drag was recorded as a click.");
        var key = Message.Create(viewer.Handle, 0x0100, new IntPtr((int)Keys.A), IntPtr.Zero);
        recorder.PreFilterMessage(ref key);
        recorder.Flush();
        Require(timeline.Snapshot().Last().Summary == "Keyboard input: 1 key events", "ActiveX keyboard paths without WM_CHAR were omitted.");
        foreach (char ch in "password")
        {
            var character = Message.Create(viewer.Handle, 0x0102, new IntPtr(ch), IntPtr.Zero);
            recorder.PreFilterMessage(ref character);
        }
        recorder.Flush();
        Require(timeline.Snapshot().Last().Summary == "Typed 8 characters", "Human typing was not aggregated as counts.");
        Require(HumanInputRecorder.RemotePoint(new Point(-1, 1), viewer.Size, new Size(1920, 1080)) is null
            && HumanInputRecorder.RemotePoint(new Point(959, 539), viewer.Size, new Size(1920, 1080)) == new Point(1918, 1078), "Coordinate boundaries are incorrect.");
        Require(timeline.FlushAsync().GetAwaiter().GetResult() && !ReadJournal(timeline.JournalPath).Contains("password"), "Human text leaked into the journal.");
    }

    private static void HumanThreadMessages(string root, bool nativeThreads)
    {
        using var timeline = new OperationTimeline(Path.Combine(root, "native-human"));
        using var viewer = new Panel { Size = new Size(960, 540) };
        IntPtr parent = viewer.Handle;
        using var ready = new ManualResetEventSlim();
        using var stop = new ManualResetEventSlim();
        IntPtr child = IntPtr.Zero, foreign = IntPtr.Zero;
        Exception? error = null;
        var worker = new Thread(() =>
        {
            var target = new NativeWindow();
            var outside = new NativeWindow();
            try
            {
                target.CreateHandle(new CreateParams { Parent = parent, Style = 0x40000000, Width = 960, Height = 540 });
                outside.CreateHandle(new CreateParams { Width = 1, Height = 1 });
                child = target.Handle;
                foreign = outside.Handle;
                ready.Set();
                // A native queue on a different thread, like RDP's IHWindowClass; no WinForms message filter here.
                while (!stop.Wait(1))
                    while (PeekMessage(out var message, IntPtr.Zero, 0, 0, 1)) DispatchMessage(ref message);
            }
            catch (Exception ex) { error = ex; ready.Set(); }
            finally { target.DestroyHandle(); outside.DestroyHandle(); }
        }) { IsBackground = true };
        worker.SetApartmentState(ApartmentState.STA);
        worker.Start();
        try
        {
            Require(ready.Wait(3000) && error is null && child != IntPtr.Zero, "Native input fixture could not start: " + error);
            bool control = true;
            using var recorder = new HumanInputRecorder(viewer, () => control, () => new Size(1920, 1080), timeline,
                () => Keys.None, nativeThreads: nativeThreads);
            void Pump()
            {
                var deadline = DateTime.UtcNow.AddMilliseconds(350);
                do { Application.DoEvents(); Thread.Sleep(2); } while (DateTime.UtcNow < deadline);
                recorder.Flush();
            }
            Require(PostMessage(child, 0x0102, new IntPtr('q'), IntPtr.Zero), "Fixture message could not be queued.");
            Pump();
            Require(timeline.Snapshot().Any(e => e.Summary == "Typed 1 characters"), "CROSS_THREAD_HUMAN_INPUT_MISSING: native viewer-thread input did not reach Timeline.");
            Require(timeline.Snapshot().Count == 1, "A native character was recorded twice.");
            PostMessage(child, 0x0201, IntPtr.Zero, new IntPtr((270 << 16) | 480));
            PostMessage(child, 0x0202, IntPtr.Zero, new IntPtr((270 << 16) | 480));
            Pump();
            Require(timeline.Snapshot().Last().Summary == "left click at 960,540", "Native-thread click was missing or its coordinates changed.");
            int count = timeline.Snapshot().Count;
            PostMessage(foreign, 0x0102, new IntPtr('x'), IntPtr.Zero);
            Pump();
            Require(timeline.Snapshot().Count == count, "Native observer recorded an unrelated window.");
            control = false;
            PostMessage(child, 0x0102, new IntPtr('z'), IntPtr.Zero);
            Pump();
            Require(timeline.Snapshot().Count == count, "Native observer recorded input in view-only mode.");
            Require(timeline.FlushAsync().GetAwaiter().GetResult(), "Native-thread journal did not flush.");
        }
        finally { stop.Set(); if (!worker.Join(3000)) throw new InvalidOperationException("Native fixture thread did not exit."); }
    }

    [System.Runtime.InteropServices.DllImport("user32.dll", EntryPoint = "PostMessageW", SetLastError = true)]
    private static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr wparam, IntPtr lparam);
    [System.Runtime.InteropServices.DllImport("user32.dll", EntryPoint = "PeekMessageW")]
    private static extern bool PeekMessage(out ViewerThreadMessages.NativeMessage message, IntPtr hwnd, uint first, uint last, uint remove);
    [System.Runtime.InteropServices.DllImport("user32.dll", EntryPoint = "DispatchMessageW")]
    private static extern IntPtr DispatchMessage(ref ViewerThreadMessages.NativeMessage message);

    private static string ReadJournal(string path)
    {
        using var input = new StreamReader(new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite));
        return input.ReadToEnd();
    }
}
