using System.Text.Json;

namespace Anode.Core.Timeline;

internal sealed record ActivityCaptureSettings(string Mode = "KeyActions", long BudgetBytes = 256L * 1024 * 1024,
    string? ScreenshotDirectory = null, string[]? KnownDirectories = null)
{
    internal const long DefaultBudget = 256L * 1024 * 1024;
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };

    internal ActivityCaptureSettings Validate()
    {
        if (!Enum.TryParse<TimelineCaptureMode>(Mode, out var mode) || !Enum.IsDefined(mode)
            || BudgetBytes is < 1 or > 64L * 1024 * 1024 * 1024) throw new InvalidDataException("Invalid screenshot settings.");
        string? folder = ScreenshotDirectory is null ? null : NormalizeDirectory(ScreenshotDirectory);
        var known = (KnownDirectories ?? []).Select(NormalizeDirectory).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        return this with { ScreenshotDirectory = folder, KnownDirectories = known };
    }

    internal static string NormalizeDirectory(string path)
    {
        if (!Path.IsPathFullyQualified(path) || path.Any(char.IsControl)) throw new InvalidDataException("Choose an absolute screenshot folder.");
        return Path.TrimEndingDirectorySeparator(Path.GetFullPath(path));
    }

    internal static ActivityCaptureSettings Load(string directory, long fallbackBudget, out string? error)
    {
        error = null;
        string path = Path.Combine(directory, "capture-options.json");
        if (!File.Exists(path)) return new(BudgetBytes: fallbackBudget);
        try { return (JsonSerializer.Deserialize<ActivityCaptureSettings>(File.ReadAllBytes(path), Json) ?? throw new InvalidDataException()).Validate(); }
        catch { error = "截图设置无效，自动截图已关闭"; return new("Off", fallbackBudget); }
    }

    internal void Save(string directory)
    {
        Directory.CreateDirectory(directory);
        string path = Path.Combine(directory, "capture-options.json");
        string stage = Path.Combine(directory, $"capture-options-{Guid.NewGuid():N}.new");
        using (var stream = new FileStream(stage, FileMode.CreateNew, FileAccess.Write)) JsonSerializer.Serialize(stream, this, Json);
        if (File.Exists(path)) File.Replace(stage, path, Path.Combine(directory, $"capture-options-backup-{Guid.NewGuid():N}.json"));
        else File.Move(stage, path);
    }
}
