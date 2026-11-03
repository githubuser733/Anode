using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;

namespace Anode.Core.Desktop;

/// <summary>Bounded UIA text with explicit completeness. Lengths count UTF-16 code units.</summary>
internal sealed record AccessibilityText(string Text, int? Length, int LengthAtLeast, bool Complete, string? Reason)
{
    internal const int NameBudget = 100_000;
    internal const int LongTextThreshold = 3000;

    internal static string Prefix(string value, int maximum)
    {
        int count = Math.Min(value.Length, Math.Max(0, maximum));
        if (count > 0 && count < value.Length && char.IsHighSurrogate(value[count - 1]) && char.IsLowSurrogate(value[count])) count--;
        return value[..count];
    }

    internal static AccessibilityText Known(string? value, int maximum, string reason)
    {
        value ??= "";
        string text = Prefix(value, maximum);
        return new(text, value.Length, value.Length, text.Length == value.Length, text.Length == value.Length ? null : reason);
    }

    // The extra character distinguishes a provider response exactly at the limit from a clipped response.
    internal static AccessibilityText Probe(Func<int, string?> read, int maximum, string reason)
    {
        if (maximum <= 0) return NotRead(reason);
        string? value = read(maximum + 1);
        if (value is null) return NotRead("providerUnavailable");
        string text = Prefix(value, maximum);
        bool complete = value.Length <= maximum;
        return new(text, complete ? value.Length : null, value.Length, complete, complete ? null : reason);
    }

    internal static AccessibilityText NotRead(string reason) => new("", null, 0, false, reason);

    internal JsonObject Info(string source) => new()
    {
        ["source"] = source, ["returnedLength"] = Text.Length, ["length"] = Length,
        ["lengthAtLeast"] = LengthAtLeast, ["complete"] = Complete, ["reason"] = Reason
    };

    internal void Put(JsonObject node, string field, string source)
    {
        node[field] = Text;
        node[field + "Info"] = Info(source);
    }

    internal static string Fingerprint(string? value) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value ?? "")));
}
