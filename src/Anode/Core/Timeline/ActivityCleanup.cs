using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Anode.Core.Timeline;

internal sealed record ActivityCleanupFile(string Path, string Scope, long Bytes, string Sha256);
internal sealed record ActivityCleanupPlan(long Through, ActivityCleanupFile[] Files)
{
    public long Bytes => Files.Sum(file => file.Bytes);
}
internal sealed record ActivityCleanupResult(int Recycled, string[] Errors);
internal interface IActivityRecycleBin
{
    void Validate(string path) { }
    void Recycle(string path);
    bool OriginalExists(string path);
}

/// <summary>Explicit file inventory, unchanged-content verification and recoverable deletion only.</summary>
internal static class ActivityCleanup
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    private static readonly Regex JournalName = new(@"^timeline-(?:import-)?[a-zA-Z0-9-]+\.jsonl$", RegexOptions.CultureInvariant);
    private static readonly Regex ImageName = new(@"^shot-(?:auto-|import-)?[a-fA-F0-9]{32}\.(?:png|jpg|jpeg)$", RegexOptions.CultureInvariant);

    internal static bool Confirmed(string passphrase, bool fullListDisplayed) => fullListDisplayed && passphrase == "1989";

    internal static async Task<ActivityCleanupPlan> PrepareAsync(OperationTimeline timeline)
    {
        var cut = await timeline.RotateAsync().ConfigureAwait(false);
        return await Task.Run(() =>
        {
            var paths = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (string file in Directory.EnumerateFiles(timeline.DirectoryPath, "timeline-*.jsonl"))
            {
                if (file.Equals(timeline.JournalPath, StringComparison.OrdinalIgnoreCase) || !JournalName.IsMatch(Path.GetFileName(file))
                    || Path.GetFileName(file).Contains("backup", StringComparison.OrdinalIgnoreCase)) continue;
                paths[file] = timeline.DirectoryPath;
                if (new FileInfo(file).Length > 64L * 1024 * 1024) throw new IOException("A journal is too large to preview safely.");
                foreach (string line in File.ReadLines(file))
                {
                    if (line.Length > 16384) throw new IOException("A journal row is too large to preview safely.");
                    TimelineEntry? row;
                    try { row = JsonSerializer.Deserialize<TimelineEntry>(line, Json); }
                    catch (JsonException) { continue; }
                    if (row?.Screenshot is not { } name || !ImageName.IsMatch(name)) continue;
                    if (timeline.ScreenshotPath(row) is { } image) paths[image] = Path.GetDirectoryName(image)!;
                }
            }
            var files = paths.OrderBy(item => item.Key, StringComparer.OrdinalIgnoreCase).Select(item => Inspect(item.Key, item.Value)).ToArray();
            return new ActivityCleanupPlan(cut.Through, files);
        }).ConfigureAwait(false);
    }

    private static ActivityCleanupFile Inspect(string path, string scope)
    {
        path = Path.GetFullPath(path); scope = Path.TrimEndingDirectorySeparator(Path.GetFullPath(scope));
        if (!string.Equals(Path.GetDirectoryName(path), scope, StringComparison.OrdinalIgnoreCase)) throw new IOException("Cleanup target escaped its displayed scope.");
        var file = new FileInfo(path);
        if (!file.Exists || (file.Attributes & FileAttributes.ReparsePoint) != 0) throw new IOException("Cleanup cannot follow file links or missing files.");
        for (var parent = file.Directory; parent is not null; parent = parent.Parent)
            if ((parent.Attributes & FileAttributes.ReparsePoint) != 0) throw new IOException("Cleanup cannot follow directory links; choose a physical screenshot folder.");
        using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        return new(path, scope, input.Length, Convert.ToHexString(SHA256.HashData(input)));
    }

    internal static ActivityCleanupResult Execute(OperationTimeline timeline, ActivityCleanupPlan plan, string passphrase,
        bool fullListDisplayed, IActivityRecycleBin? recycleBin = null)
    {
        if (!Confirmed(passphrase, fullListDisplayed)) throw new InvalidOperationException("Show the complete file list, then enter the agreed cleanup passphrase.");
        // Recheck all targets before the first recycle operation. A changed list needs a new confirmation.
        foreach (var expected in plan.Files)
            if (Inspect(expected.Path, expected.Scope) != expected) throw new IOException("The cleanup list changed; preview and confirm it again.");
        recycleBin ??= new WindowsActivityRecycleBin();
        foreach (var file in plan.Files) recycleBin.Validate(file.Path);
        int recycled = 0; var errors = new List<string>();
        foreach (var file in plan.Files)
            try
            {
                recycleBin.Recycle(file.Path);
                if (recycleBin.OriginalExists(file.Path)) throw new IOException("original path still exists");
                recycled++;
            }
            catch (Exception ex) { errors.Add(file.Path + ": " + ex.Message); break; }
        if (errors.Count == 0)
        {
            timeline.ForgetThrough(plan.Through);
            timeline.ArchiveEvent("archive.clear", recycled);
        }
        return new(recycled, errors.ToArray());
    }
}
