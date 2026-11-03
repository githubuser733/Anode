using System.Drawing.Imaging;
using System.IO.Compression;
using System.Text.Json;
using System.Text.Json.Nodes;
using Anode.Core.Capture;
using Anode.Core.Timeline;
using Anode.Daemon;

namespace Anode.Cli;

/// <summary>Retained fixtures; real recycling and real input are never invoked here.</summary>
internal static class ActivitySettingsChecks
{
    private static int _count;
    private static void Check(bool condition, string error) { if (!condition) throw new InvalidOperationException(error); _count++; }
    private static async Task Until(Func<bool> condition)
    {
        var deadline = DateTime.UtcNow.AddSeconds(4);
        while (!condition() && DateTime.UtcNow < deadline) await Task.Delay(10);
        Check(condition(), "Timed out waiting for a fixture image.");
    }
    private static void Reject(Action action, string error)
    {
        bool rejected = false;
        try { action(); } catch (Exception ex) when (ex is InvalidDataException or IOException or InvalidOperationException) { rejected = true; }
        Check(rejected, error);
    }
    private sealed class SimulatedRecycleBin(int failAt = 0) : IActivityRecycleBin
    {
        internal readonly HashSet<string> Calls = new(StringComparer.OrdinalIgnoreCase);
        public void Recycle(string path)
        {
            Calls.Add(path);
            if (failAt > 0 && Calls.Count == failAt) throw new IOException("simulated recycle unavailable");
        }
        public bool OriginalExists(string path) => !Calls.Contains(path);
    }

    internal static async Task<int> RunAsync(string root)
    {
        _count = 0;
        Check(await WindowsActivityRecycleBin.OnStaAsync(WindowsActivityRecycleBin.Probe), "Windows explicit recycle COM activation or flags failed (no files were queued).");
        string folder = Path.Combine(root, "activity-settings-checks"); Directory.CreateDirectory(folder);
        using var timeline = new OperationTimeline(Path.Combine(folder, "journal"));
        using var bitmap = new Bitmap(32, 18);
        byte[] jpeg = ScreenCapture.Encode(bitmap, 32, "jpeg", 65).Bytes;
        using var pngStream = new MemoryStream(); bitmap.Save(pngStream, ImageFormat.Png); byte[] png = pngStream.ToArray();
        await timeline.TrackAsync(new JsonObject { ["op"] = "screenshot" }, () => Task.FromResult(new JsonObject { ["ok"] = true, ["result"] = new JsonObject { ["data"] = Convert.ToBase64String(png) } }));
        Check(await timeline.FlushAsync(), "Initial screenshot did not flush.");
        string initial = timeline.ScreenshotPath(timeline.Snapshot().Single())!;
        string custom = Path.Combine(folder, "custom-images");
        using (var capture = new TimelineCaptureScheduler(timeline, _ => Task.FromResult<TimelineFrame?>(new(jpeg, DateTimeOffset.UtcNow)), TimeSpan.Zero, TimeSpan.FromMilliseconds(5)))
        {
            Check(capture.BudgetBytes == ActivityCaptureSettings.DefaultBudget && capture.ScreenshotDirectory == timeline.DirectoryPath, "Default capture budget or path changed.");
            capture.Configure(1048576, custom);
            timeline.Human("input.click", "custom capture");
            await Until(() => capture.Captured >= 1); Check(await timeline.FlushAsync(), "Custom image did not flush.");
            var customRow = timeline.Snapshot().Last();
            Check(customRow.ScreenshotDirectory == custom && timeline.ScreenshotPath(customRow)!.StartsWith(custom + Path.DirectorySeparatorChar), "Custom capture did not use its configured directory.");
            Check(File.Exists(initial), "Changing the screenshot folder moved or removed an old picture.");
            capture.Configure(2097152, null);
            timeline.Human("input.click", "default capture");
            await Until(() => capture.Captured >= 2); Check(await timeline.FlushAsync(), "Default image did not flush.");
            Check(timeline.Snapshot().Last().ScreenshotDirectory is null && File.Exists(timeline.ScreenshotPath(customRow)), "Restoring the default path invalidated a custom image.");
            Check(capture.Bytes == jpeg.Length * 2 && capture.BudgetBytes == 2097152, "Budget accounting lost files from a previous screenshot folder.");
            Reject(() => capture.Configure(0, null), "A zero budget was accepted.");
            Reject(() => capture.Configure(1048576, "relative/path"), "A relative image path was accepted.");
            capture.Mode = TimelineCaptureMode.Off;
        }
        using (var restored = new TimelineCaptureScheduler(timeline, _ => Task.FromResult<TimelineFrame?>(null)))
            Check(restored.BudgetBytes == 2097152 && restored.ScreenshotDirectory == timeline.DirectoryPath && restored.Mode == TimelineCaptureMode.Off, "Capture settings did not survive reconstruction.");
        Check(Directory.GetFiles(timeline.DirectoryPath, "capture-options-backup-*.json").Length >= 1, "Changing screenshot settings did not preserve old preferences.");
        string package = Path.Combine(folder, "custom-path-roundtrip.zip");
        var exported = await ActivityArchive.ExportAsync(timeline, package);
        Check(exported.Images == 2, "Portable export failed to deduplicate identical images across folders.");
        using (var zip = new ZipArchive(File.OpenRead(package), ZipArchiveMode.Read))
        using (var reader = new StreamReader(zip.GetEntry("activity.json")!.Open()))
            Check(!(await reader.ReadToEndAsync()).Contains(custom.Replace("\\", "\\\\"), StringComparison.Ordinal), "Portable export leaked the custom storage root.");
        using (var target = new OperationTimeline(Path.Combine(folder, "imported-journal")))
        {
            string destination = Path.Combine(folder, "imported-images"); target.UseImageDirectory(destination);
            await ActivityArchive.ImportAsync(target, package);
            Check(target.Snapshot().Where(e => e.Screenshot is not null).All(e => e.ScreenshotDirectory == destination && target.ScreenshotPath(e) is not null), "Imported images ignored the destination screenshot directory.");
        }
        await CleanupChecks(timeline, folder);
        string unavailable = Path.Combine(folder, "unwritable-journal");
        using (var stream = new FileStream(unavailable, FileMode.CreateNew)) stream.WriteByte(1);
        using (var failed = new OperationTimeline(unavailable))
        {
            await Until(() => failed.WriteError is not null);
            var expected = new JsonObject { ["ok"] = true };
            var actual = await failed.TrackAsync(new JsonObject { ["op"] = "input.click" }, () => Task.FromResult(expected));
            Check(ReferenceEquals(actual, expected) && failed.WriteError is not null, "A journal I/O failure changed the operation result.");
        }
        using (var recovery = new OperationTimeline(Path.Combine(folder, "folder-recovery")))
        {
            new ActivityCaptureSettings(ScreenshotDirectory: unavailable).Save(recovery.DirectoryPath);
            using var capture = new TimelineCaptureScheduler(recovery, _ => Task.FromResult<TimelineFrame?>(null));
            Check(capture.Mode == TimelineCaptureMode.Off && capture.SettingsError is not null, "An unavailable image directory did not disable automatic capture visibly.");
            capture.Configure(1048576, Path.Combine(folder, "recovered-images"));
            Check(capture.Mode == TimelineCaptureMode.Off && capture.SettingsError is null, "Fixing the image directory silently re-enabled capture or kept a stale error.");
        }
        return _count;
    }

    private static async Task CleanupChecks(OperationTimeline timeline, string folder)
    {
        var fakeTrash = new SimulatedRecycleBin();
        var plan = await ActivityCleanup.PrepareAsync(timeline);
        Check(plan.Files.Length >= 4 && plan.Files.All(f => Path.IsPathFullyQualified(f.Path) && Path.GetDirectoryName(f.Path) == f.Scope), "Cleanup inventory did not resolve complete scoped file paths.");
        Check(plan.Files.All(f => f.Path != timeline.JournalPath && !f.Path.Contains("capture-options") && !f.Path.EndsWith(".zip")), "Cleanup included the active journal, preferences, backup or archive.");
        Check(plan.Files.Any(f => f.Path.Contains("custom-images")), "Cleanup missed pictures in a former custom directory.");
        Reject(() => ActivityCleanup.Execute(timeline, plan, "1989", false, fakeTrash), "Cleanup accepted a passphrase before the full file list was displayed.");
        Reject(() => ActivityCleanup.Execute(timeline, plan, "wrong", true, fakeTrash), "Cleanup accepted an incorrect passphrase.");
        Check(fakeTrash.Calls.Count == 0 && plan.Files.All(f => File.Exists(f.Path)), "A rejected cleanup changed fixture files.");
        timeline.Human("input.click", "keep this new activity"); Check(await timeline.FlushAsync(), "Post-inventory event did not flush.");
        var result = ActivityCleanup.Execute(timeline, plan, "1989", true, fakeTrash);
        Check(result.Errors.Length == 0 && result.Recycled == plan.Files.Length, "Cleanup did not verify originals through the recycle provider.");
        Check(timeline.Snapshot().Any(e => e.Summary == "keep this new activity") && timeline.Snapshot().All(e => e.Sequence > plan.Through), "Cleanup removed activity created after its inventory cutoff.");
        Check(plan.Files.All(f => File.Exists(f.Path)), "A simulated recycle test deleted real files.");
        var unavailable = new SimulatedRecycleBin(failAt: 2);
        var partial = ActivityCleanup.Execute(timeline, plan, "1989", true, unavailable);
        Check(partial.Recycled == 1 && partial.Errors.Length == 1 && unavailable.Calls.Count == 2,
            "Cleanup continued after the recycle provider failed.");
        Check(plan.Files.All(f => File.Exists(f.Path)), "A failed simulated recycle changed physical files.");
        using var stale = new OperationTimeline(Path.Combine(folder, "stale-journal"));
        stale.Human("input.click", "stale fixture"); Check(await stale.FlushAsync(), "Stale fixture did not flush.");
        var stalePlan = await ActivityCleanup.PrepareAsync(stale);
        await File.AppendAllTextAsync(stalePlan.Files[0].Path, "{}\n");
        Reject(() => ActivityCleanup.Execute(stale, stalePlan, "1989", true, fakeTrash), "Cleanup recycled a file changed after approval inventory.");
    }

    internal static int Ui(string root)
    {
        int before = _count;
        using var timeline = new OperationTimeline(Path.Combine(root, "settings-ui"));
        using var capture = new TimelineCaptureScheduler(timeline, _ => Task.FromResult<TimelineFrame?>(null));
        using var settings = new ActivityCaptureSettingsDialog(capture, timeline.DirectoryPath);
        Check(settings.BudgetBytes == ActivityCaptureSettings.DefaultBudget && settings.CustomDirectory is null, "Settings UI did not show the default budget and folder.");
        using var picture = new Bitmap(32, 18); string path = Path.Combine(root, "preview-fixture.png");
        using (var output = new FileStream(path, FileMode.CreateNew)) picture.Save(output, ImageFormat.Png);
        using var preview = new ActivityImagePreview(path);
        Check(((PictureBox)preview.Controls.Find("ImagePreviewPicture", true).Single()).Image is { Width: 32, Height: 18 }, "Image preview failed to load its picture.");
        var plan = new ActivityCleanupPlan(0, [new(Path.Combine(root, "shown-file.jsonl"), root, 123, "fixture")]);
        using var cleanup = new ActivityCleanupDialog(plan);
        var list = (TextBox)cleanup.Controls.Find("CleanupFullFileList", true).Single();
        var passphrase = (TextBox)cleanup.Controls.Find("CleanupPassphrase", true).Single();
        Check(list.Text.Contains(plan.Files[0].Path) && passphrase.Text.Length == 0 && !cleanup.FullListDisplayed, "Cleanup UI hid paths, prefilled the passphrase or authorized itself.");
        Check(!((Button)cleanup.Controls.Find("ConfirmActivityCleanup", true).Single()).Enabled, "Cleanup confirmation was enabled before authorization.");
        return _count - before;
    }
}
