using System.Drawing.Imaging;
using System.IO.Compression;
using System.Text.Json;
using System.Text.Json.Nodes;
using Anode.Core.Capture;
using Anode.Core.Timeline;
using Anode.Daemon;
using Anode.Seat;
using System.Diagnostics;

namespace Anode.Cli;

/// <summary>Data/fixture checks: retained evidence, no input injection or live desktop creation.</summary>
internal static class ActivityChecks
{
    private static int _checks;
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    private static void Check(bool value, string message) { if (!value) throw new InvalidOperationException(message); _checks++; }
    private static async Task Reject(Func<Task> action, string message)
    {
        bool rejected = false;
        try { await action(); } catch (Exception ex) when (ex is InvalidDataException or IOException or JsonException) { rejected = true; }
        Check(rejected, message);
    }

    internal static async Task<int> RunAsync(string root)
    {
        _checks = 0;
        var basic = new TimelineEntry(1, "fixture", DateTimeOffset.UtcNow, "Human", "Human", "input.click", "left click", "sent", 0);
        Check(ActivityPresentation.Cue(basic).Level == "交互", "Click classification failed.");
        Check(ActivityPresentation.Cue(basic with { Operation = "screenshot" }).Level == "观察", "Read classification failed.");
        Check(ActivityPresentation.Cue(basic with { Operation = "run" }).Level == "变更", "Launch classification failed.");
        Check(ActivityPresentation.Cue(basic with { Operation = "ps.kill" }).Level == "高影响", "Process termination classification failed.");
        Check(ActivityPresentation.Cue(basic with { Operation = "desktop.window", Action = "close" }).Level == "高影响", "Close-window impact was hidden.");
        Check(ActivityPresentation.Cue(basic with { Outcome = "failed" }).Level == "交互" && ActivityPresentation.Status("failed") == "失败", "Failure and operation impact were conflated.");
        Check(ActivityPresentation.Matches(basic, "CLICK", "Human", "鼠标", "交互", "完成"), "Combined filters failed.");
        Check(!ActivityPresentation.Matches(basic, actor: "Agent") && !ActivityPresentation.Matches(basic, imagesOnly: true), "Actor/image filters failed.");
        Check(ActivityPresentation.Matches(basic with { Summary = "literal [*]" }, "[*]"), "Search unexpectedly interpreted a regular expression.");
        Check(!TimelineCaptureScheduler.IsKey(basic with { Operation = "desktop.observe" }) && TimelineCaptureScheduler.IsKey(basic), "Key-action policy failed.");
        foreach (string op in new[] { "screenshot", "input.click", "run", "ps.kill" })
        {
            var color = ActivityPresentation.Cue(basic with { Operation = op }).Color;
            double Linear(byte channel) { double s = channel / 255.0; return s <= .04045 ? s / 12.92 : Math.Pow((s + .055) / 1.055, 2.4); }
            double luminance = .2126 * Linear(color.R) + .7152 * Linear(color.G) + .0722 * Linear(color.B);
            Check(1.05 / (luminance + .05) >= 4.5, "Level badge contrast is below 4.5:1.");
        }
        await ArchivesAsync(root);
        await CaptureAsync(root);
        _checks += await ActivitySettingsChecks.RunAsync(root);
        using var report = new FileStream(Path.Combine(root, "activity-result.json"), FileMode.CreateNew);
        JsonSerializer.Serialize(report, new { status = "ANODE_ACTIVITY_CHECKS_OK", checks = _checks, inputSent = false, liveSeatStarted = false });
        return _checks;
    }

    private static async Task ArchivesAsync(string root)
    {
        string folder = Path.Combine(root, "archive-checks"); Directory.CreateDirectory(folder);
        using var source = new OperationTimeline(Path.Combine(folder, "source"), 3);
        for (int i = 0; i < 7; i++) source.Human("input.click", "click " + i);
        using var bitmap = new Bitmap(32, 18); using var picture = new MemoryStream(); bitmap.Save(picture, ImageFormat.Png);
        byte[] png = picture.ToArray();
        await source.TrackAsync(new JsonObject { ["op"] = "screenshot", ["agentId"] = "fixture" },
            () => Task.FromResult(new JsonObject { ["ok"] = true, ["result"] = new JsonObject { ["data"] = Convert.ToBase64String(png) } }));
        var pending = new TaskCompletionSource<JsonObject>(TaskCreationOptions.RunContinuationsAsynchronously);
        var running = source.TrackAsync(new JsonObject { ["op"] = "input.text", ["text"] = "ARCHIVE_PRIVATE_TEXT" }, () => pending.Task);
        string package = Path.Combine(folder, "roundtrip.zip");
        var exported = await ActivityArchive.ExportAsync(source, package);
        pending.SetResult(new JsonObject { ["ok"] = true }); await running;
        Check(exported.Entries == 9 && exported.Images == 1, "Full export lost rows outside the memory ring or images.");
        byte[] originalPackage = File.ReadAllBytes(package);
        await Reject(() => ActivityArchive.ExportAsync(source, package), "Export overwrote an existing archive.");
        Check(originalPackage.AsSpan().SequenceEqual(File.ReadAllBytes(package)), "Existing archive contents changed.");
        using var target = new OperationTimeline(Path.Combine(folder, "target"), 3);
        target.Human("input.click", "Live record must survive import");
        int completions = 0; target.Completed += _ => completions++;
        var imported = await ActivityArchive.ImportAsync(target, package);
        Check(imported.Entries == 9 && imported.Images == 1 && completions == 0, "Import replayed/completed an operation or lost rows.");
        Check(target.Snapshot().Any(e => e.Summary == "Live record must survive import"), "Import evicted the live ring.");
        Check(target.Snapshot().Count(e => e.Source is not null) == 9, "Imported provenance was lost.");
        Check(target.Snapshot().Any(e => e.Source is not null && e.Outcome == "interrupted; effects unknown"), "An archived running operation appeared live.");
        Check(target.Snapshot().Where(e => e.Screenshot is not null).All(e => target.ScreenshotPath(e) is not null), "Imported image missing.");
        var second = await ActivityArchive.ImportAsync(target, package);
        Check(second.Entries == 0 && second.Images == 0 && second.Skipped == 9, "Duplicate import was not idempotent.");
        using (var restored = new OperationTimeline(target.DirectoryPath))
            Check(restored.Snapshot().Count(e => e.Source is not null) == 9, "Imported history did not survive reopening.");
        using var zip = new ZipArchive(new MemoryStream(originalPackage), ZipArchiveMode.Read);
        using var text = new StreamReader(zip.GetEntry("activity.json")!.Open());
        string manifest = await text.ReadToEndAsync();
        var doc = JsonSerializer.Deserialize<ActivityArchive.Document>(manifest, Json)!;
        Check(!manifest.Contains("ARCHIVE_PRIVATE_TEXT"), "Typed text leaked into export.");
        string imageName = doc.Images.Keys.Single();
        async Task<string> Fixture(string name, ActivityArchive.Document payload, bool image = true, bool extra = false)
        {
            string path = Path.Combine(folder, name);
            using var stream = new FileStream(path, FileMode.CreateNew); using var archive = new ZipArchive(stream, ZipArchiveMode.Create);
            using (var json = archive.CreateEntry("activity.json").Open()) await JsonSerializer.SerializeAsync(json, payload, Json);
            if (image) { using var data = archive.CreateEntry("images/" + imageName).Open(); await data.WriteAsync(png); }
            if (extra) { using var data = archive.CreateEntry("../outside.txt").Open(); data.WriteByte(1); }
            return path;
        }
        await target.FlushAsync(); int beforeFiles = Directory.GetFiles(target.DirectoryPath).Length;
        string bad = await Fixture("unsafe-name.zip", doc with { Entries = doc.Entries.Select(e => e.Screenshot is null ? e : e with { Screenshot = "..\\secret.png" }).ToArray() });
        await Reject(() => ActivityArchive.ImportAsync(target, bad), "Unsafe image path accepted.");
        bad = await Fixture("unsupported.zip", doc with { SchemaVersion = 999 });
        await Reject(() => ActivityArchive.ImportAsync(target, bad), "Unsupported schema accepted.");
        bad = await Fixture("missing-image.zip", doc, image: false);
        await Reject(() => ActivityArchive.ImportAsync(target, bad), "Missing image accepted.");
        bad = await Fixture("extra-member.zip", doc, extra: true);
        await Reject(() => ActivityArchive.ImportAsync(target, bad), "Unexpected path member accepted.");
        bad = await Fixture("bad-checksum.zip", doc with { Images = new() { [imageName] = new(png.Length, new string('0', 64)) } });
        await Reject(() => ActivityArchive.ImportAsync(target, bad), "Image checksum mismatch accepted.");
        bad = await Fixture("oversized-image.zip", doc with { Images = new() { [imageName] = new(16 * 1024 * 1024 + 1, doc.Images[imageName].Sha256) } });
        await Reject(() => ActivityArchive.ImportAsync(target, bad), "Oversized image accepted.");
        bad = await Fixture("duplicate-row.zip", doc with { Entries = doc.Entries.Append(doc.Entries[0]).ToArray() });
        await Reject(() => ActivityArchive.ImportAsync(target, bad), "Duplicate IDs accepted.");
        Check(Directory.GetFiles(target.DirectoryPath).Length == beforeFiles, "Invalid imports wrote into the live timeline.");
    }

    private static async Task Until(Func<bool> predicate, string message)
    {
        var deadline = DateTime.UtcNow.AddSeconds(4);
        while (!predicate() && DateTime.UtcNow < deadline) await Task.Delay(10);
        Check(predicate(), message);
    }

    private static async Task CaptureAsync(string root)
    {
        using var bitmap = new Bitmap(32, 18);
        byte[] jpeg = ScreenCapture.Encode(bitmap, 32, "jpeg", 65).Bytes;
        using var timeline = new OperationTimeline(Path.Combine(root, "capture-checks"));
        int calls = 0;
        var started = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        var complete = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        var firstStored = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        using (var capture = new TimelineCaptureScheduler(timeline, async cancel =>
        {
            int call = Interlocked.Increment(ref calls);
            if (call == 1) { started.SetResult(true); await complete.Task.WaitAsync(cancel); }
            if (call == 2) await firstStored.Task.WaitAsync(cancel);
            return new TimelineFrame(jpeg, DateTimeOffset.UtcNow);
        }, TimeSpan.FromMilliseconds(30), TimeSpan.FromMilliseconds(5)))
        {
            timeline.Human("input.click", "first");
            await started.Task.WaitAsync(TimeSpan.FromSeconds(3));
            for (int i = 0; i < 30; i++) timeline.Human("input.click", "burst " + i);
            complete.SetResult(true);
            await Until(() => capture.Captured >= 1, "First queued capture did not finish.");
            Check(await timeline.FlushAsync(), "First image did not reach the writer.");
            firstStored.SetResult(true);
            await Until(() => capture.Captured >= 2, "Queued capture did not finish.");
            Check(calls == 2 && capture.Coalesced >= 29, "Input burst generated one image per event.");
            Check(await timeline.FlushAsync(), "Image journal did not flush.");
            Check(timeline.Snapshot().Count(e => e.Screenshot is not null) == 2, "Images were attached to the wrong number of events.");
            Check(capture.Reused == 1 && capture.Bytes == jpeg.Length && Directory.GetFiles(timeline.DirectoryPath, "shot-auto-*").Length == 1, "Identical frames consumed duplicate image storage.");
            capture.Mode = TimelineCaptureMode.Off;
            timeline.Human("input.text", "Typed 4 characters");
            await Task.Delay(80);
            Check(calls == 2, "Off mode still captured input.");
            timeline.Checkpoint();
            await Until(() => capture.Captured == 3, "Explicit checkpoint did not capture in off mode.");
        }
        using (var restored = new TimelineCaptureScheduler(timeline, _ => Task.FromResult<TimelineFrame?>(null)))
            Check(restored.Mode == TimelineCaptureMode.Off, "Screenshot preference did not persist.");
        using var limitedTimeline = new OperationTimeline(Path.Combine(root, "budget-checks"));
        int limitedCalls = 0;
        using (var limited = new TimelineCaptureScheduler(limitedTimeline, _ => { limitedCalls++; return Task.FromResult<TimelineFrame?>(new(jpeg, DateTimeOffset.UtcNow)); }, TimeSpan.Zero, TimeSpan.FromMilliseconds(5), jpeg.Length - 1))
        {
            limitedTimeline.Human("input.click", "budget");
            await Until(() => limited.BudgetReached, "Image quota was not enforced.");
            limitedTimeline.Human("input.click", "after quota"); await Task.Delay(70);
            Check(limitedCalls == 1 && limited.Captured == 0 && limited.Bytes == 0, "Quota accounting was inaccurate or kept capturing.");
        }
        using var failedTimeline = new OperationTimeline(Path.Combine(root, "capture-failure-checks"));
        using var failed = new TimelineCaptureScheduler(failedTimeline, _ => Task.FromException<TimelineFrame?>(new IOException("fixture")), TimeSpan.Zero, TimeSpan.FromMilliseconds(5));
        var expected = new JsonObject { ["ok"] = true };
        var response = await failedTimeline.TrackAsync(new JsonObject { ["op"] = "input.click" }, () => Task.FromResult(expected));
        await Until(() => failed.Failed > 0, "Capture failure was invisible.");
        Check(ReferenceEquals(response, expected) && failedTimeline.Snapshot().Single().Outcome == "ok", "A screenshot failure changed operation success.");
    }

    internal static int Ui(string root)
    {
        int before = _checks;
        using var timeline = new OperationTimeline(Path.Combine(root, "activity-ui"));
        timeline.Human("input.click", "Human click");
        timeline.TrackAsync(new JsonObject { ["op"] = "run", ["agentId"] = "fixture" }, () => Task.FromResult(new JsonObject { ["ok"] = true })).GetAwaiter().GetResult();
        using var panel = new TimelinePanel { Size = new Size(520, 620) };
        panel.Bind(timeline); _ = panel.Handle;
        var rows = (ListView)panel.Controls.Find("TimelineEntries", true).Single();
        Check(rows.Items.Count == 2 && rows.Columns.Count == 6, "Activity table did not render its levels/results/image columns.");
        var actor = panel.Controls.OfType<TableLayoutPanel>().Single().Controls.OfType<ComboBox>().First(c => c.AccessibleName == "Timeline actor filter");
        actor.SelectedIndex = 2;
        Check(rows.Items.Count == 1 && ((TimelineEntry)rows.Items[0].Tag!).Actor == "Human", "Actor filter did not update the table.");
        timeline.ImportEntriesAsync([new TimelineEntry(0, "fixture-imported", DateTimeOffset.UtcNow, "Human", "Human", "input.click", "Imported click", "sent", 0, Source: "import:fixture")]).GetAwaiter().GetResult();
        var origin = panel.Controls.Find("活动来源筛选", true).OfType<ComboBox>().Single(); origin.SelectedIndex = 2;
        Check(rows.Items.Count == 1 && ((TimelineEntry)rows.Items[0].Tag!).Source is not null, "Imported activity filter failed.");
        origin.SelectedIndex = 1;
        Check(rows.Items.Count == 1 && ((TimelineEntry)rows.Items[0].Tag!).Source is null, "Live activity filter failed.");
        var search = (TextBox)panel.Controls.Find("ActivitySearch", true).Single(); search.Text = "Human";
        ((Button)panel.Controls.Find("ResetActivityFilters", true).Single()).PerformClick();
        Check(search.Text == "Human" && actor.SelectedIndex == 0 && origin.SelectedIndex == 0, "Reset categories erased search or failed to reset selection.");
        panel.SetColors(Color.FromArgb(25, 25, 25), Color.White, Color.Silver);
        Check(rows.Items[0].SubItems[2].BackColor != rows.BackColor, "Level badge disappeared in dark mode.");
        panel.Size = new Size(520, 315); panel.PerformLayout();
        Check(rows.ClientSize.Height >= 65, "Compact layout hid the activity rows.");
        Check(panel.Controls.OfType<ToolStrip>().Count() == 2 && panel.Controls.Find("ActivityHealth", true).Length == 1, "Archive/capture groups or independent log health are missing.");
        var captureSelector = panel.Controls.OfType<ToolStrip>().SelectMany(strip => strip.Items.OfType<ToolStripComboBox>()).Single();
        Check(captureSelector.ForeColor == SystemColors.WindowText && captureSelector.BackColor == SystemColors.Window, "Capture mode selector lost readable system text colors.");
        _checks += ActivitySettingsChecks.Ui(root);
        return _checks - before;
    }

    internal static async Task<int> ProbeAsync(string root)
    {
        var measurements = new List<object>();
        for (int i = 0; i < 3; i++)
        {
            if (i > 0) await Task.Delay(1100);
            var clock = Stopwatch.StartNew();
            var response = await SeatHost.ProbeTimelineCaptureAsync();
            if (response["ok"]?.GetValue<bool>() != true) throw new InvalidOperationException("Verified child capture probe failed.");
            byte[] data = Convert.FromBase64String(response["result"]!["data"]!.GetValue<string>());
            ActivityArchive.CheckImage(data);
            using (var output = new FileStream(Path.Combine(root, $"capture-{i}.jpg"), FileMode.CreateNew)) await output.WriteAsync(data);
            measurements.Add(new { elapsedMs = clock.Elapsed.TotalMilliseconds, bytes = data.Length });
        }
        using var report = new FileStream(Path.Combine(root, "capture-probe.json"), FileMode.CreateNew);
        JsonSerializer.Serialize(report, new { status = "ANODE_ACTIVITY_CHILD_CAPTURE_OK", samples = measurements, inputSent = false, parentDesktopCaptured = false });
        Console.WriteLine("ANODE_ACTIVITY_CHILD_CAPTURE_OK samples=3 inputSent=false parentDesktopCaptured=false");
        return 0;
    }
}
