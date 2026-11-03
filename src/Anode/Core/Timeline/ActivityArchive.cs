using System.Drawing;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Anode.Core.Timeline;

/// <summary>Portable, data-only activity packages. Imported operations are never dispatched.</summary>
internal static class ActivityArchive
{
    internal const int MaxEntries = 20000;
    private const long MaxManifest = 32 * 1024 * 1024, MaxImage = 16 * 1024 * 1024, MaxImages = 256L * 1024 * 1024;
    private static readonly JsonSerializerOptions Json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase, MaxDepth = 24 };
    internal sealed record ImageInfo(long Bytes, string Sha256);
    internal sealed record Document(int SchemaVersion, string BundleId, DateTimeOffset ExportedAt, TimelineEntry[] Entries, Dictionary<string, ImageInfo> Images);
    internal sealed record Result(int Entries, int Images, int Skipped = 0);

    internal static async Task<Result> ExportAsync(OperationTimeline timeline, string path, IReadOnlyList<TimelineEntry>? selected = null)
    {
        if (!Path.IsPathFullyQualified(path)) throw new ArgumentException("Choose an absolute export path.");
        if (!await timeline.FlushAsync().ConfigureAwait(false)) throw new IOException("The local journal could not be flushed.");
        var rows = (selected ?? timeline.ExportEntries()).ToArray();
        if (rows.Length > MaxEntries) throw new InvalidDataException("Export is limited to 20,000 activities; filter the view first.");
        var images = new Dictionary<string, ImageInfo>(StringComparer.Ordinal);
        var imageSources = new Dictionary<string, string>(StringComparer.Ordinal);
        long total = 0;
        for (int index = 0; index < rows.Length; index++)
            if (rows[index].Screenshot is { } name)
            {
                var row = rows[index];
                string source = timeline.ScreenshotPath(row) ?? throw new IOException("An activity image is missing; the export was not started.");
                CheckName(name);
                if ((File.GetAttributes(source) & FileAttributes.ReparsePoint) != 0) throw new InvalidDataException("Image links cannot be exported.");
                byte[] data = await File.ReadAllBytesAsync(source).ConfigureAwait(false);
                CheckImage(data);
                string hash = Hash(data);
                string portable = "image-" + hash + Path.GetExtension(name).ToLowerInvariant();
                if (!images.ContainsKey(portable))
                {
                    total += data.Length;
                    if (total > MaxImages) throw new InvalidDataException("Images exceed 256 MiB; filter the view first.");
                    images.Add(portable, new(data.Length, hash)); imageSources.Add(portable, source);
                }
                rows[index] = row with { Screenshot = portable, ScreenshotDirectory = null };
            }
        var document = new Document(1, Guid.NewGuid().ToString("N"), DateTimeOffset.UtcNow, rows, images);
        byte[] manifest = JsonSerializer.SerializeToUtf8Bytes(document, Json);
        if (manifest.LongLength > MaxManifest) throw new InvalidDataException("The activity manifest is too large.");
        // CreateNew deliberately refuses overwriting an existing user archive.
        using var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        using var zip = new ZipArchive(output, ZipArchiveMode.Create);
        using (var stream = zip.CreateEntry("activity.json", CompressionLevel.Optimal).Open()) await stream.WriteAsync(manifest).ConfigureAwait(false);
        foreach (var item in images)
        {
            string source = imageSources[item.Key];
            byte[] data = await File.ReadAllBytesAsync(source).ConfigureAwait(false);
            if (Hash(data) != item.Value.Sha256) throw new IOException("An image changed during export; this package is incomplete.");
            using var stream = zip.CreateEntry("images/" + item.Key, CompressionLevel.NoCompression).Open();
            await stream.WriteAsync(data).ConfigureAwait(false);
        }
        return new(rows.Length, images.Count);
    }

    internal static async Task<Result> ImportAsync(OperationTimeline timeline, string path)
    {
        using var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        using var zip = new ZipArchive(input, ZipArchiveMode.Read);
        if (zip.Entries.Count is < 1 or > MaxEntries + 1) throw new InvalidDataException("Too many package members.");
        var members = new Dictionary<string, ZipArchiveEntry>(StringComparer.Ordinal);
        foreach (var entry in zip.Entries)
            if (!members.TryAdd(entry.FullName, entry)) throw new InvalidDataException("Duplicate package member.");
        if (!members.TryGetValue("activity.json", out var manifest)) throw new InvalidDataException("This is not an Anode activity package.");
        byte[] bytes = await ReadBoundedAsync(manifest, MaxManifest).ConfigureAwait(false);
        var document = JsonSerializer.Deserialize<Document>(bytes, Json) ?? throw new InvalidDataException("Missing activity manifest.");
        if (document.SchemaVersion != 1 || document.BundleId is null || document.BundleId.Length != 32 || !document.BundleId.All(char.IsAsciiHexDigit)
            || document.Entries is null || document.Images is null || document.Entries.Length > MaxEntries || document.Images.Count > MaxEntries)
            throw new InvalidDataException("Unsupported or oversized activity manifest.");
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (var row in document.Entries)
        {
            if (row is null || !Valid(row.Id, 80) || !ids.Add(row.Id) || !Valid(row.Actor, 24) || !Valid(row.ActorName, 64)
                || !Valid(row.Operation, 80) || !Valid(row.Summary, 240, empty: true) || !Valid(row.Outcome, 80)
                || row.Action is { } action && !Valid(action, 32) || row.ElapsedMs is < 0 or > 604800000 || row.At.Year < 1970)
                throw new InvalidDataException("Invalid or duplicate activity row.");
            if (row.Screenshot is { } name) { CheckName(name); if (!document.Images.ContainsKey(name)) throw new InvalidDataException("Missing image descriptor."); }
        }
        long total = 0;
        foreach (var image in document.Images)
        {
            CheckName(image.Key);
            if (image.Value is null || image.Value.Bytes is < 1 or > MaxImage || image.Value.Sha256 is not { Length: 64 }
                || !image.Value.Sha256.All(char.IsAsciiHexDigit) || !members.TryGetValue("images/" + image.Key, out var entry))
                throw new InvalidDataException("Invalid image descriptor.");
            total += image.Value.Bytes;
            if (total > MaxImages) throw new InvalidDataException("Package images exceed 256 MiB.");
            byte[] data = await ReadBoundedAsync(entry, MaxImage).ConfigureAwait(false);
            if (data.Length != image.Value.Bytes || !Hash(data).Equals(image.Value.Sha256, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("Image checksum mismatch.");
            CheckImage(data);
        }
        if (members.Count != document.Images.Count + 1) throw new InvalidDataException("Unexpected package member or unsafe path.");
        // Validation above completes before anything is written to the local timeline.
        var known = timeline.ExportEntries().Select(e => e.Id).ToHashSet(StringComparer.Ordinal);
        string LocalId(TimelineEntry row) => "import-" + Hash(Encoding.UTF8.GetBytes(document.BundleId + ":" + row.Id));
        var pending = document.Entries.Where(e => !known.Contains(LocalId(e))).ToArray();
        var needed = pending.Where(e => e.Screenshot is not null).Select(e => e.Screenshot!).Distinct().ToArray();
        var renamed = new Dictionary<string, string>(StringComparer.Ordinal);
        string imageDirectory = timeline.ImageDirectory;
        Directory.CreateDirectory(imageDirectory);
        foreach (string name in needed)
        {
            string local = "shot-import-" + Guid.NewGuid().ToString("N") + Path.GetExtension(name).ToLowerInvariant();
            byte[] data = await ReadBoundedAsync(members["images/" + name], MaxImage).ConfigureAwait(false);
            using var stream = new FileStream(Path.Combine(imageDirectory, local), FileMode.CreateNew, FileAccess.Write, FileShare.Read);
            await stream.WriteAsync(data).ConfigureAwait(false);
            renamed[name] = local;
        }
        var rows = pending.Select(row => row with { Id = LocalId(row), Source = "import:" + document.BundleId,
            Screenshot = row.Screenshot is { } image ? renamed[image] : null,
            ScreenshotDirectory = row.Screenshot is not null && imageDirectory != timeline.DirectoryPath ? imageDirectory : null,
            Outcome = row.Outcome == "running" ? "interrupted; effects unknown" : row.Outcome,
            CaptureStatus = row.Screenshot is null ? null : "imported" }).ToArray();
        int added = await timeline.ImportEntriesAsync(rows).ConfigureAwait(false);
        return new(added, needed.Length, document.Entries.Length - added);
    }

    internal static void CheckImage(byte[] bytes)
    {
        if (bytes.LongLength is < 8 or > MaxImage) throw new InvalidDataException("Invalid image size.");
        using var stream = new MemoryStream(bytes, false);
        try
        {
            using var image = Image.FromStream(stream);
            if (image.Width > 8192 || image.Height > 8192 || (long)image.Width * image.Height > 8388608
                || image.RawFormat.Guid != System.Drawing.Imaging.ImageFormat.Png.Guid && image.RawFormat.Guid != System.Drawing.Imaging.ImageFormat.Jpeg.Guid)
                throw new InvalidDataException("Only PNG/JPEG images up to 8 megapixels are supported.");
        }
        catch (Exception ex) when (ex is ArgumentException or OutOfMemoryException) { throw new InvalidDataException("Invalid PNG/JPEG image.", ex); }
    }

    private static void CheckName(string name)
    {
        if (name.Length is < 5 or > 100 || name.StartsWith('.') || !name.All(c => char.IsAsciiLetterOrDigit(c) || c is '-' or '_' or '.')
            || !new[] { ".png", ".jpg", ".jpeg" }.Contains(Path.GetExtension(name).ToLowerInvariant())) throw new InvalidDataException("Unsafe image name.");
    }
    private static bool Valid(string? value, int limit, bool empty = false) => value is not null && value.Length <= limit && (empty || value.Length > 0) && !value.Any(char.IsControl);
    private static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    private static async Task<byte[]> ReadBoundedAsync(ZipArchiveEntry entry, long maximum)
    {
        if (entry.Length < 0 || entry.Length > maximum) throw new InvalidDataException("Package member exceeds its size limit.");
        using var stream = entry.Open();
        using var output = new MemoryStream();
        byte[] buffer = new byte[65536];
        int read;
        while ((read = await stream.ReadAsync(buffer).ConfigureAwait(false)) > 0)
        {
            if (output.Length + read > maximum) throw new InvalidDataException("Expanded package member exceeds its size limit.");
            await output.WriteAsync(buffer.AsMemory(0, read)).ConfigureAwait(false);
        }
        if (output.Length != entry.Length) throw new InvalidDataException("Package member is truncated.");
        return output.ToArray();
    }
}
