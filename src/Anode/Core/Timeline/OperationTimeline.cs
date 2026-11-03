using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Channels;

namespace Anode.Core.Timeline;

internal sealed record TimelineEntry(long Sequence, string Id, DateTimeOffset At, string Actor,
    string ActorName, string Operation, string Summary, string Outcome, long ElapsedMs, string? Screenshot = null,
    string? Action = null, string? Source = null, string? CaptureStatus = null, DateTimeOffset? CapturedAt = null,
    string? ScreenshotDirectory = null);

/// <summary>Append-only activity, separate from command execution and from the seat's input policy.</summary>
internal sealed class OperationTimeline : IDisposable
{
    private sealed record Pending(TimelineEntry? Entry = null, byte[]? Image = null, TaskCompletionSource<bool>? Barrier = null, string? RotateTo = null);
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    private readonly object _gate = new();
    private readonly HashSet<string> _imageDirectories = new(StringComparer.OrdinalIgnoreCase);
    private readonly Queue<TimelineEntry> _recent = new();
    private readonly Dictionary<string, TimelineEntry> _imported = new();
    private readonly Queue<string> _importOrder = new();
    private readonly SemaphoreSlim _importGate = new(1, 1);
    private readonly Channel<Pending> _writes;
    private readonly Task _writer;
    private readonly int _capacity;
    private long _sequence;
    private int _disposed, _notQueued;
    private string? _writeError;

    public string DirectoryPath { get; }
    public string JournalPath { get; private set; }
    public string ImageDirectory { get; private set; }
    public int PendingWrites => _writes.Reader.CanCount ? _writes.Reader.Count : 0;
    public string? WriteError => Volatile.Read(ref _writeError);
    public int NotQueued => Volatile.Read(ref _notQueued);
    public event Action? Changed;
    internal event Action<TimelineEntry>? Completed;

    public OperationTimeline(string directory, int capacity = 500)
    {
        if (!Path.IsPathFullyQualified(directory)) throw new ArgumentException("Timeline directory must be absolute.");
        if (capacity < 1 || capacity > 5000) throw new ArgumentOutOfRangeException(nameof(capacity));
        DirectoryPath = Path.GetFullPath(directory);
        ImageDirectory = DirectoryPath;
        _imageDirectories.Add(DirectoryPath);
        _capacity = capacity;
        JournalPath = Path.Combine(DirectoryPath, $"timeline-{DateTime.UtcNow:yyyyMMdd-HHmmss}-{Guid.NewGuid():N}.jsonl");
        _writes = Channel.CreateBounded<Pending>(new BoundedChannelOptions(1024) { SingleReader = true, FullMode = BoundedChannelFullMode.Wait });
        ReadHistory();
        _writer = Task.Run(WriteLoopAsync);
    }

    public IReadOnlyList<TimelineEntry> Snapshot()
    {
        lock (_gate) return _recent.Concat(_imported.Values).OrderBy(e => e.Sequence).ToArray();
    }

    public static bool Records(JsonObject request)
    {
        string op = Text(request, "op") ?? "";
        if (op == "lease") return Text(request, "action") is "acquire" or "release";
        return op.StartsWith("input.", StringComparison.Ordinal)
            || op.StartsWith("desktop.", StringComparison.Ordinal) && op != "desktop.capabilities"
            || op is "screenshot" or "run" or "exec.start" or "exec.cancel" or "display.set" or "browser.open"
                or "steam.launch" or "ps.kill" or "seat.start" or "seat.stop" or "seat.show" or "seat.hide"
                or "seat.control" or "quit" or "kill";
    }

    public async Task<JsonObject> TrackAsync(JsonObject request, Func<Task<JsonObject>> execute)
    {
        if (!Records(request)) return await execute().ConfigureAwait(false);
        // Keep only an allowlisted description: request bodies, lease tokens and error text never enter the journal.
        string operation = Clean(Text(request, "op") ?? "operation", 80);
        string actor = Text(request, "agentId") is null ? "Client" : "Agent";
        string name = Clean(Text(request, "agentName") ?? Text(request, "agentId") ?? "CLI", 64);
        string summary = Describe(request);
        var clock = Stopwatch.StartNew();
        string? action = Text(request, "action") is { } verb && new[] { "invoke", "focus", "set_value", "toggle", "select", "expand", "collapse", "scroll", "scroll_into_view", "set_range", "close", "minimize", "maximize", "restore", "move", "raise", "acquire", "release" }.Contains(verb) ? verb : null;
        string activity = Add(actor, name, operation, summary, "running", 0, action: action);
        try
        {
            var response = await execute().ConfigureAwait(false);
            byte[]? image = null;
            string? imageName = null;
            bool succeeded = response["ok"] is JsonValue ok && ok.TryGetValue<bool>(out bool value) && value;
            if (succeeded)
                (image, imageName) = ExistingScreenshot(operation, response);
            Add(actor, name, operation, summary, succeeded ? "ok" : "failed", clock.ElapsedMilliseconds, image, imageName, activity, action);
            return response;
        }
        catch (OperationCanceledException)
        {
            Add(actor, name, operation, summary, "cancelled; effects unknown", clock.ElapsedMilliseconds, activityId: activity, action: action);
            throw;
        }
        catch
        {
            Add(actor, name, operation, summary, "error; effects unknown", clock.ElapsedMilliseconds, activityId: activity, action: action);
            throw;
        }
    }

    public void Human(string operation, string summary) => Add("Human", "Human", operation, summary, "sent", 0);
    public void Control(bool enabled) => Add("Human", "Human", enabled ? "control.take" : "control.release",
        enabled ? "Took control" : "Released control", "state", 0);

    private string Add(string actor, string name, string operation, string summary, string outcome, long duration,
        byte[]? image = null, string? imageName = null, string? activityId = null, string? action = null)
    {
        string id = activityId ?? Guid.NewGuid().ToString("N");
        if (Volatile.Read(ref _disposed) != 0) return id;
        TimelineEntry entry;
        lock (_gate)
        {
            entry = new TimelineEntry(++_sequence, id, DateTimeOffset.UtcNow,
                actor, name, Clean(operation, 80), Clean(summary, 240), outcome, duration, imageName, action,
                CaptureStatus: imageName is null ? null : "existing", CapturedAt: imageName is null ? null : DateTimeOffset.UtcNow,
                ScreenshotDirectory: imageName is null || ImageDirectory == DirectoryPath ? null : ImageDirectory);
            Remember(entry);
            if (!_writes.Writer.TryWrite(new Pending(entry, image))) Interlocked.Increment(ref _notQueued);
        }
        Notify();
        if (outcome != "running")
            foreach (var handler in Completed?.GetInvocationList() ?? []) try { ((Action<TimelineEntry>)handler)(entry); } catch { }
        return id;
    }

    private void Remember(TimelineEntry entry)
    {
        if (entry.Source?.StartsWith("import:", StringComparison.Ordinal) == true)
        {
            if (!_imported.ContainsKey(entry.Id)) _importOrder.Enqueue(entry.Id);
            _imported[entry.Id] = entry;
            while (_imported.Count > 5000) _imported.Remove(_importOrder.Dequeue());
            return;
        }
        if (_recent.Any(previous => previous.Id == entry.Id))
        {
            var keep = _recent.Where(previous => previous.Id != entry.Id).ToArray();
            _recent.Clear();
            foreach (var previous in keep.Append(entry).OrderBy(e => e.Sequence)) _recent.Enqueue(previous);
            return;
        }
        _recent.Enqueue(entry);
        while (_recent.Count > _capacity) _recent.Dequeue();
    }

    internal bool CaptureState(string id, string state, byte[]? image = null, DateTimeOffset? capturedAt = null, string? existingScreenshot = null,
        string? existingScreenshotDirectory = null)
    {
        if (Volatile.Read(ref _disposed) != 0) return false;
        bool queued;
        lock (_gate)
        {
            var entry = _recent.FirstOrDefault(e => e.Id == id);
            if (entry is null || entry.Screenshot is not null) return false;
            if (existingScreenshot is not null && ScreenshotPath(entry with { Screenshot = existingScreenshot, ScreenshotDirectory = existingScreenshotDirectory }) is null) return false;
            var update = entry with { CaptureStatus = state, CapturedAt = capturedAt,
                Screenshot = image is null ? existingScreenshot : $"shot-auto-{Guid.NewGuid():N}.jpg",
                ScreenshotDirectory = image is null ? existingScreenshotDirectory : ImageDirectory == DirectoryPath ? null : ImageDirectory };
            queued = _writes.Writer.TryWrite(new Pending(update, image));
            if (!queued) { Interlocked.Increment(ref _notQueued); update = entry with { CaptureStatus = "journal queue full" }; }
            Remember(update);
        }
        Notify();
        return queued;
    }

    public void ArchiveEvent(string operation, int entries) => Add("Client", "Viewer", operation, $"{operation}: {entries} entries", "ok", 0);
    public void Checkpoint() => Add("Client", "Viewer", "checkpoint", "Manual visual checkpoint", "state", 0);
    internal void NotifyStatus() => Notify();

    internal void UseImageDirectory(string? path)
    {
        string folder = path is null ? DirectoryPath : ActivityCaptureSettings.NormalizeDirectory(path);
        Directory.CreateDirectory(folder);
        lock (_gate) { ImageDirectory = folder; _imageDirectories.Add(folder); }
    }

    internal string[] ImageDirectories()
    {
        lock (_gate) return _imageDirectories.ToArray();
    }

    internal async Task<(string PreviousJournal, long Through)> RotateAsync()
    {
        string next = Path.Combine(DirectoryPath, $"timeline-{DateTime.UtcNow:yyyyMMdd-HHmmss}-{Guid.NewGuid():N}.jsonl");
        var barrier = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        string previous; long through;
        lock (_gate)
        {
            previous = JournalPath; through = _sequence;
            if (!_writes.Writer.TryWrite(new Pending(Barrier: barrier, RotateTo: next))) throw new IOException("Journal queue is busy; try cleanup again later.");
        }
        if (!await barrier.Task.ConfigureAwait(false)) throw new IOException("Could not rotate the activity journal.");
        return (previous, through);
    }

    internal void ForgetThrough(long sequence)
    {
        lock (_gate)
        {
            var remaining = _recent.Where(e => e.Sequence > sequence).ToArray();
            _recent.Clear(); foreach (var entry in remaining) _recent.Enqueue(entry);
            foreach (string id in _imported.Where(e => e.Value.Sequence <= sequence).Select(e => e.Key).ToArray()) _imported.Remove(id);
            var imported = _importOrder.Where(_imported.ContainsKey).ToArray();
            _importOrder.Clear(); foreach (string id in imported) _importOrder.Enqueue(id);
        }
        Notify();
    }

    internal IReadOnlyList<TimelineEntry> ExportEntries()
    {
        var rows = new Dictionary<string, TimelineEntry>(StringComparer.Ordinal);
        if (Directory.Exists(DirectoryPath))
            foreach (string file in Directory.EnumerateFiles(DirectoryPath, "timeline-*.jsonl").OrderBy(File.GetLastWriteTimeUtc))
            {
                if (new FileInfo(file).Length > 64 * 1024 * 1024) throw new InvalidDataException("A journal exceeds the export limit; export the filtered view instead.");
                using var input = new StreamReader(new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite));
                while (input.ReadLine() is { } line)
                {
                    if (line.Length > 16384) throw new InvalidDataException("A journal row exceeds the export limit.");
                    try { if (JsonSerializer.Deserialize<TimelineEntry>(line, Json) is { Id: not null } entry) rows[entry.Id] = entry; }
                    catch (JsonException) { /* Preserve intact rows after a truncated append. */ }
                    if (rows.Count > ActivityArchive.MaxEntries) throw new InvalidDataException("Too many rows; export the filtered view instead.");
                }
            }
        foreach (var entry in Snapshot()) rows[entry.Id] = entry;
        return rows.Values.OrderBy(e => e.At).ThenBy(e => e.Sequence).ToArray();
    }

    internal async Task<int> ImportEntriesAsync(IReadOnlyList<TimelineEntry> entries)
    {
        await _importGate.WaitAsync().ConfigureAwait(false);
        try
        {
            if (!await FlushAsync().ConfigureAwait(false)) throw new IOException("The local journal is not writable.");
            var known = ExportEntries().Select(e => e.Id).ToHashSet(StringComparer.Ordinal);
            var fresh = entries.Where(e => known.Add(e.Id)).ToArray();
            if (fresh.Length == 0) return 0;
            string stage = Path.Combine(DirectoryPath, $"activity-{Guid.NewGuid():N}.importing");
            string committed = Path.Combine(DirectoryPath, $"timeline-import-{Guid.NewGuid():N}.jsonl");
            var imported = new List<TimelineEntry>();
            using (var writer = new StreamWriter(new FileStream(stage, FileMode.CreateNew, FileAccess.Write, FileShare.None), new UTF8Encoding(false)))
                foreach (var entry in fresh)
                {
                    TimelineEntry local;
                    lock (_gate) local = entry with { Sequence = ++_sequence };
                    imported.Add(local);
                    await writer.WriteLineAsync(JsonSerializer.Serialize(local, Json)).ConfigureAwait(false);
                }
            // Both generated, nonrecursive file paths are inside this timeline directory; no overwrite.
            File.Move(stage, committed);
            lock (_gate) foreach (var entry in imported) Remember(entry);
            Notify();
            return imported.Count;
        }
        finally { _importGate.Release(); }
    }

    public async Task<bool> FlushAsync()
    {
        var barrier = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        await _writes.Writer.WriteAsync(new Pending(Barrier: barrier)).ConfigureAwait(false);
        return await barrier.Task.ConfigureAwait(false);
    }

    private async Task WriteLoopAsync()
    {
        StreamWriter? writer = null;
        try
        {
            Directory.CreateDirectory(DirectoryPath);
            writer = new StreamWriter(new FileStream(JournalPath, FileMode.CreateNew, FileAccess.Write, FileShare.Read), new UTF8Encoding(false));
        }
        catch (Exception ex) { Fail(ex); }
        try
        {
            await foreach (var item in _writes.Reader.ReadAllAsync().ConfigureAwait(false))
            {
                try
                {
                    if (item.RotateTo is { } next)
                    {
                        var replacement = new StreamWriter(new FileStream(next, FileMode.CreateNew, FileAccess.Write, FileShare.Read), new UTF8Encoding(false));
                        if (writer is not null) await writer.DisposeAsync().ConfigureAwait(false);
                        writer = replacement;
                        lock (_gate) JournalPath = next;
                    }
                    if (item.Entry is { } entry && writer is not null)
                    {
                        if (item.Image is { } bytes && entry.Screenshot is { } file)
                        {
                            string folder = entry.ScreenshotDirectory ?? DirectoryPath;
                            Directory.CreateDirectory(folder);
                            using var output = new FileStream(Path.Combine(folder, file), FileMode.CreateNew, FileAccess.Write, FileShare.Read);
                            await output.WriteAsync(bytes).ConfigureAwait(false);
                        }
                        await writer.WriteLineAsync(JsonSerializer.Serialize(entry, Json)).ConfigureAwait(false);
                        await writer.FlushAsync().ConfigureAwait(false);
                        Notify();
                    }
                }
                catch (Exception ex) { Fail(ex); }
                item.Barrier?.TrySetResult(WriteError is null && NotQueued == 0);
            }
        }
        finally { if (writer is not null) await writer.DisposeAsync().ConfigureAwait(false); }
    }

    private void ReadHistory()
    {
        try
        {
            if (!Directory.Exists(DirectoryPath)) return;
            var files = Directory.EnumerateFiles(DirectoryPath, "timeline-*.jsonl").OrderByDescending(File.GetLastWriteTimeUtc).Take(8).Reverse();
            foreach (string file in files)
            {
                if (new FileInfo(file).Length > 32 * 1024 * 1024) continue;
                using var input = new StreamReader(new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite));
                while (input.ReadLine() is { } line)
                {
                    try
                    {
                        if (JsonSerializer.Deserialize<TimelineEntry>(line, Json) is not { } row || row.Id is null || row.Summary is null) continue;
                        Remember(row);
                        if (row.ScreenshotDirectory is { } imageFolder && Path.IsPathFullyQualified(imageFolder)) _imageDirectories.Add(ActivityCaptureSettings.NormalizeDirectory(imageFolder));
                        _sequence = Math.Max(_sequence, row.Sequence);
                    }
                    catch (JsonException) { /* A truncated final line must not erase the rest of the history. */ }
                }
            }
            var restored = _recent.Select(row => row.Outcome == "running" ? row with { Outcome = "interrupted; effects unknown" } : row).ToArray();
            _recent.Clear();
            foreach (var row in restored) _recent.Enqueue(row);
        }
        catch (Exception ex) { Fail(ex); }
    }

    public string? ScreenshotPath(TimelineEntry entry)
    {
        if (entry.Screenshot is not { } name || Path.GetFileName(name) != name || name.IndexOfAny(['/', '\\', ':']) >= 0) return null;
        try
        {
            string folder = entry.ScreenshotDirectory is { } custom ? ActivityCaptureSettings.NormalizeDirectory(custom) : DirectoryPath;
            string path = Path.Combine(folder, name);
            return File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0 ? path : null;
        }
        catch (Exception ex) when (ex is IOException or ArgumentException or UnauthorizedAccessException) { return null; }
    }

    private static (byte[]? Image, string? Name) ExistingScreenshot(string operation, JsonObject response)
    {
        var result = response["result"] as JsonObject;
        var shot = operation == "screenshot" ? result : result?["screenshot"] as JsonObject;
        if (shot is null || Text(shot, "data") is not { Length: > 0 and < 28_000_000 } data) return (null, null);
        try
        {
            byte[] bytes = Convert.FromBase64String(data);
            bool png = bytes.Length >= 8 && bytes.AsSpan(0, 8).SequenceEqual(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 });
            bool jpeg = bytes.Length >= 3 && bytes[0] == 255 && bytes[1] == 216 && bytes[2] == 255;
            return png || jpeg ? (bytes, $"shot-{Guid.NewGuid():N}." + (png ? "png" : "jpg")) : (null, null);
        }
        catch (FormatException) { return (null, null); }
    }

    internal static string Describe(JsonObject request)
    {
        string op = Text(request, "op") ?? "operation";
        if (op is "input.type" or "input.text") return $"Typed {Text(request, "text")?.Length ?? 0} characters";
        if (op is "input.key" or "input.keydown" or "input.keyup") return op;
        string summary = op;
        if (request["x"] is JsonValue x && x.TryGetValue<int>(out int px) && request["y"] is JsonValue y && y.TryGetValue<int>(out int py)) summary += $" at {px},{py}";
        if (op == "lease" && Text(request, "action") is "acquire" or "release") summary += " " + Text(request, "action");
        return summary;
    }

    private static string? Text(JsonObject obj, string key) => obj[key] is JsonValue value && value.TryGetValue<string>(out var text) ? text : null;
    private static string Clean(string value, int limit) => new(value.Where(c => !char.IsControl(c)).Take(limit).ToArray());
    private void Fail(Exception ex) { Volatile.Write(ref _writeError, ex.GetType().Name + ": " + ex.Message); Notify(); }
    private void Notify()
    {
        foreach (var handler in Changed?.GetInvocationList() ?? []) try { ((Action)handler)(); } catch { }
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
        _writes.Writer.TryComplete();
        try { _writer.Wait(TimeSpan.FromSeconds(2)); } catch { }
    }
}
