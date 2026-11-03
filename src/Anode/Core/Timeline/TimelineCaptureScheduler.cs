using System.Threading.Channels;
using System.Diagnostics;
using System.Security.Cryptography;

namespace Anode.Core.Timeline;

internal enum TimelineCaptureMode { Off, KeyActions, AllActions }
internal sealed record TimelineFrame(byte[] Bytes, DateTimeOffset At);

/// <summary>One queued image, post-action debounce and a disk budget; never blocks an operation.</summary>
internal sealed class TimelineCaptureScheduler : IDisposable
{
    private sealed record QueuedCapture(TimelineEntry Entry, int Generation);
    private readonly OperationTimeline _timeline;
    private readonly Func<CancellationToken, Task<TimelineFrame?>> _capture;
    private readonly Channel<QueuedCapture> _pending = Channel.CreateBounded<QueuedCapture>(1);
    private readonly SemaphoreSlim _captureGate = new(1, 1);
    private readonly CancellationTokenSource _stop = new();
    private readonly Task _worker;
    private readonly TimeSpan _interval, _debounce;
    private long _budget;
    private long _bytes, _captured, _failed, _coalesced, _reused;
    private string? _lastHash, _lastImage, _lastImageDirectory;
    private int _mode = (int)TimelineCaptureMode.KeyActions;
    private int _budgetBlocked;
    private int _paused, _generation;
    private ActivityCaptureSettings _options;
    private readonly object _settingsGate = new();
    public string? SettingsError { get; private set; }
    public TimelineCaptureMode Mode
    {
        get => (TimelineCaptureMode)Volatile.Read(ref _mode);
        set
        {
            if (!Enum.IsDefined(value)) throw new ArgumentOutOfRangeException(nameof(value));
            if (Mode == value) return;
            try { ApplyOptions(_options with { Mode = value.ToString() }); }
            catch { SettingsError = "截图设置未保存，仍使用原设置"; _timeline.NotifyStatus(); }
        }
    }
    public long Bytes => Interlocked.Read(ref _bytes);
    public long Captured => Interlocked.Read(ref _captured);
    public long Failed => Interlocked.Read(ref _failed);
    public long Coalesced => Interlocked.Read(ref _coalesced);
    public long Reused => Interlocked.Read(ref _reused);
    public long BudgetBytes => Interlocked.Read(ref _budget);
    public bool Paused => Volatile.Read(ref _paused) != 0;
    public string ScreenshotDirectory => _timeline.ImageDirectory;
    public bool BudgetReached => Bytes >= BudgetBytes || Volatile.Read(ref _budgetBlocked) != 0;

    public TimelineCaptureScheduler(OperationTimeline timeline, Func<CancellationToken, Task<TimelineFrame?>> capture,
        TimeSpan? interval = null, TimeSpan? debounce = null, long budget = 256L * 1024 * 1024)
    {
        _timeline = timeline; _capture = capture;
        _interval = interval ?? TimeSpan.FromSeconds(3); _debounce = debounce ?? TimeSpan.FromMilliseconds(300); _budget = budget;
        _options = ActivityCaptureSettings.Load(timeline.DirectoryPath, budget, out string? error);
        SettingsError = error;
        _mode = (int)Enum.Parse<TimelineCaptureMode>(_options.Mode);
        _budget = _options.BudgetBytes;
        try { timeline.UseImageDirectory(_options.ScreenshotDirectory); RecountBytes(); }
        catch { _mode = (int)TimelineCaptureMode.Off; _options = _options with { Mode = "Off" }; SettingsError = "截图目录不可用，自动截图已关闭"; }
        timeline.Completed += Queue;
        _worker = Task.Run(WorkAsync);
    }

    public void Configure(long budgetBytes, string? directory) => ApplyOptions(_options with { BudgetBytes = budgetBytes, ScreenshotDirectory = directory });

    private void ApplyOptions(ActivityCaptureSettings value)
    {
        lock (_settingsGate)
        {
            value = value.Validate();
            string folder = value.ScreenshotDirectory ?? _timeline.DirectoryPath;
            Directory.CreateDirectory(folder);
            if (Paused || !_captureGate.Wait(TimeSpan.FromSeconds(5))) throw new IOException("Screenshot capture is busy; try settings again shortly.");
            try
            {
                // Settle an accepted image before recounting disk usage. Desktop commands never take this gate.
                Interlocked.Increment(ref _generation);
                if (!_timeline.FlushAsync().GetAwaiter().GetResult()) throw new IOException("The activity journal is unavailable.");
                value = value with { KnownDirectories = (_options.KnownDirectories ?? []).Concat(_timeline.ImageDirectories()).Append(folder).Distinct(StringComparer.OrdinalIgnoreCase).ToArray() };
                long bytes = CountBytes(value.KnownDirectories!);
                value.Save(_timeline.DirectoryPath);
                _timeline.UseImageDirectory(value.ScreenshotDirectory);
                _options = value;
                Volatile.Write(ref _mode, (int)Enum.Parse<TimelineCaptureMode>(value.Mode));
                Interlocked.Exchange(ref _budget, value.BudgetBytes);
                _lastHash = _lastImage = _lastImageDirectory = null;
                Interlocked.Exchange(ref _bytes, bytes); Interlocked.Exchange(ref _budgetBlocked, 0); SettingsError = null;
            }
            finally { _captureGate.Release(); }
        }
        _timeline.NotifyStatus();
    }

    internal void RecountBytes()
    {
        long bytes = CountBytes(_timeline.ImageDirectories().Concat(_options.KnownDirectories ?? []));
        Interlocked.Exchange(ref _bytes, bytes);
        Interlocked.Exchange(ref _budgetBlocked, 0);
    }

    private static long CountBytes(IEnumerable<string> folders) => folders.Distinct(StringComparer.OrdinalIgnoreCase).Where(Directory.Exists)
        .Sum(folder => Directory.EnumerateFiles(folder, "shot-auto-*").Sum(path => new FileInfo(path).Length));

    internal async Task PauseForCleanupAsync()
    {
        if (Interlocked.Exchange(ref _paused, 1) != 0) throw new InvalidOperationException("Activity cleanup is already in progress.");
        Interlocked.Increment(ref _generation);
        while (_pending.Reader.TryRead(out var pending)) _timeline.CaptureState(pending.Entry.Id, "paused for log cleanup");
        await _captureGate.WaitAsync().ConfigureAwait(false);
        _lastHash = _lastImage = _lastImageDirectory = null;
        _timeline.NotifyStatus();
    }

    internal void ResumeAfterCleanup()
    {
        try { RecountBytes(); }
        catch { SettingsError = "无法统计截图占用"; }
        Volatile.Write(ref _paused, 0); _captureGate.Release(); _timeline.NotifyStatus();
    }

    internal static bool IsKey(TimelineEntry entry) => entry.Operation == "checkpoint"
        || ActivityPresentation.Status(entry.Outcome) is "失败" or "未确认"
        || entry.Operation is "input.click" or "input.drag" or "input.text" or "input.type" or "input.key" or "desktop.element" or "desktop.window"
        || ActivityPresentation.Cue(entry).Level is "变更" or "高影响";

    private void Queue(TimelineEntry entry)
    {
        if (entry.Source is not null || entry.Screenshot is not null || entry.Operation.StartsWith("archive.")
            || entry.Operation is "input.move" or "input.down" or "input.up" or "input.keydown" or "input.keyup") return;
        if (Paused) { _timeline.CaptureState(entry.Id, "paused for log cleanup"); return; }
        if (entry.Operation != "checkpoint" && (Mode == TimelineCaptureMode.Off || Mode == TimelineCaptureMode.KeyActions && !IsKey(entry)))
        { _timeline.CaptureState(entry.Id, "not selected"); return; }
        _timeline.CaptureState(entry.Id, "queued");
        var queued = new QueuedCapture(entry, Volatile.Read(ref _generation));
        if (_pending.Writer.TryWrite(queued)) return;
        if (_pending.Reader.TryRead(out var old))
        { Interlocked.Increment(ref _coalesced); _timeline.CaptureState(old.Entry.Id, "coalesced into next action"); }
        if (!_pending.Writer.TryWrite(queued)) _timeline.CaptureState(entry.Id, "queue busy");
    }

    private async Task WorkAsync()
    {
        var clock = Stopwatch.StartNew();
        TimeSpan nextCapture = TimeSpan.Zero;
        try
        {
            while (await _pending.Reader.WaitToReadAsync(_stop.Token).ConfigureAwait(false))
            {
                var wait = nextCapture - clock.Elapsed;
                await Task.Delay(wait > _debounce ? wait : _debounce, _stop.Token).ConfigureAwait(false);
                if (!_pending.Reader.TryRead(out var queued)) continue;
                var entry = queued.Entry;
                if (Paused || queued.Generation != Volatile.Read(ref _generation)) { _timeline.CaptureState(entry.Id, "capture settings changed"); continue; }
                if (entry.Operation != "checkpoint" && Mode == TimelineCaptureMode.Off) { _timeline.CaptureState(entry.Id, "disabled"); continue; }
                if (BudgetReached) { _timeline.CaptureState(entry.Id, "configured image budget reached"); continue; }
                if (_timeline.WriteError is not null) { _timeline.CaptureState(entry.Id, "journal unavailable"); continue; }
                nextCapture = clock.Elapsed + _interval;
                await _captureGate.WaitAsync(_stop.Token).ConfigureAwait(false);
                try
                {
                    if (Paused || queued.Generation != Volatile.Read(ref _generation)) { _timeline.CaptureState(entry.Id, "capture settings changed"); continue; }
                    var frame = await _capture(_stop.Token).ConfigureAwait(false);
                    if (frame is null) throw new IOException("Seat capture unavailable");
                    if (Paused || queued.Generation != Volatile.Read(ref _generation) || entry.Operation != "checkpoint" && Mode == TimelineCaptureMode.Off) { _timeline.CaptureState(entry.Id, "disabled or settings changed"); continue; }
                    string hash = Convert.ToHexString(SHA256.HashData(frame.Bytes));
                    if (hash == _lastHash && _lastImage is not null
                        && _timeline.CaptureState(entry.Id, "identical frame reused", capturedAt: frame.At, existingScreenshot: _lastImage, existingScreenshotDirectory: _lastImageDirectory))
                    { Interlocked.Increment(ref _reused); Interlocked.Increment(ref _captured); continue; }
                    if (frame.Bytes.LongLength + Bytes > BudgetBytes) { Interlocked.Exchange(ref _budgetBlocked, 1); _timeline.CaptureState(entry.Id, "configured image budget reached"); continue; }
                    if (_timeline.CaptureState(entry.Id, "captured after action", frame.Bytes, frame.At))
                    {
                        var saved = _timeline.Snapshot().FirstOrDefault(e => e.Id == entry.Id);
                        _lastHash = hash; _lastImage = saved?.Screenshot; _lastImageDirectory = saved?.ScreenshotDirectory;
                        Interlocked.Add(ref _bytes, frame.Bytes.LongLength); Interlocked.Increment(ref _captured);
                    }
                    else Interlocked.Increment(ref _failed);
                }
                catch (OperationCanceledException) when (_stop.IsCancellationRequested) { throw; }
                catch { Interlocked.Increment(ref _failed); _timeline.CaptureState(entry.Id, "unavailable; operation result unchanged"); }
                finally { _captureGate.Release(); }
            }
        }
        catch (OperationCanceledException) when (_stop.IsCancellationRequested) { }
    }

    public void Dispose()
    {
        _timeline.Completed -= Queue; _stop.Cancel(); _pending.Writer.TryComplete();
        try { _worker.Wait(TimeSpan.FromSeconds(4)); } catch { }
    }
}
