using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json.Nodes;
using System.Windows.Automation;
using Anode.Core.Bridge;
using Anode.Core.Desktop;

namespace Anode.Cli;

/// <summary>A read-only list reference; each read fetches fresh child properties in one cache request.</summary>
internal sealed class CachedChatReader
{
    private readonly WindowTarget _target;
    private readonly string _title, _listName;
    private readonly AutomationElement _list;
    private readonly CacheRequest _cache;

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr window, StringBuilder text, int capacity);

    internal CachedChatReader(WindowTarget target, string listName)
    {
        _target = target;
        _title = Title();
        _listName = listName;
        _list = AutomationElement.FromHandle(WindowAccess.Verify(target)).FindFirst(TreeScope.Descendants,
            new AndCondition(new PropertyCondition(AutomationElement.NameProperty, listName),
                new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.List)))
            ?? throw new InvalidOperationException("The selected message list was not found.");
        _cache = new CacheRequest { TreeScope = TreeScope.Element | TreeScope.Children,
            AutomationElementMode = AutomationElementMode.None };
        foreach (var property in new[] { AutomationElement.NameProperty, AutomationElement.ControlTypeProperty,
            AutomationElement.IsPasswordProperty, AutomationElement.ProcessIdProperty, AutomationElement.RuntimeIdProperty })
            _cache.Add(property);
    }

    private string Title()
    {
        var text = new StringBuilder(1024);
        GetWindowText(WindowAccess.Verify(_target), text, text.Capacity);
        return text.ToString();
    }

    internal JsonObject Read(int maximum, int chars, bool tail = true)
    {
        if (maximum is < 1 or > 500 || chars is < 1 or > 20000) throw new ArgumentException("Invalid text limits.");
        var watch = Stopwatch.StartNew();
        if (Title() != _title) throw new InvalidOperationException("The selected window title changed.");
        var cached = _list.GetUpdatedCache(_cache);
        void Verify(AutomationElement element)
        {
            int pid = element.Cached.ProcessId;
            if (pid == _target.Pid) return;
            using var process = Process.GetProcessById(pid);
            if (process.SessionId != (int)Core.Session.ChildSession.CurrentSessionId())
                throw new InvalidOperationException("Refusing a control outside the child session.");
        }
        Verify(cached);
        if (cached.Cached.IsPassword || cached.Cached.Name != _listName || cached.Cached.ControlType != ControlType.List)
            throw new InvalidOperationException("The selected message list changed identity.");
        var children = cached.CachedChildren.Cast<AutomationElement>().ToArray();
        if (children.Length > 5000) throw new InvalidOperationException("The exposed list exceeds the bulk-read limit.");
        int budget = AccessibilityText.NameBudget;
        var nodes = new JsonArray();
        var selection = tail ? children.TakeLast(maximum) : children.Take(maximum);
        foreach (var item in selection)
        {
            Verify(item);
            var data = item.Cached;
            if (data.IsPassword) continue;
            var text = AccessibilityText.Known(data.Name, Math.Min(chars, budget), budget < chars ? "totalTextBudget" : "maxChars");
            var entry = new JsonObject { ["index"] = nodes.Count, ["role"] = AccessibilityReader.Role(data.ControlType) };
            var identity = item.GetCachedPropertyValue(AutomationElement.RuntimeIdProperty) as int[];
            entry["elementKey"] = identity is { Length: > 0 }
                ? AccessibilityText.Fingerprint(string.Join(".", identity.Select(i => i.ToString(CultureInfo.InvariantCulture)))) : null;
            text.Put(entry, "text", "name");
            nodes.Add(entry);
            budget -= text.Text.Length;
        }
        if (Title() != _title) throw new InvalidOperationException("The selected window title changed during reading.");
        bool omitted = children.Length > maximum;
        return new JsonObject { ["listName"] = _listName, ["selectorMode"] = "cachedChildren", ["nodes"] = nodes,
            ["exposedChildren"] = children.Length, ["truncated"] = omitted, ["tailRead"] = tail,
            ["windowComplete"] = true, ["olderItemsOmitted"] = tail && omitted,
            ["truncationReason"] = omitted ? "maxElements" : null, ["elapsedMs"] = watch.ElapsedMilliseconds,
            ["readOnly"] = true, ["screenshotRequested"] = false, ["clipboardRead"] = false };
    }

    internal JsonObject ReadRecent(int maximum, int chars)
    {
        if (maximum is < 1 or > 500 || chars is < 1 or > 20000) throw new ArgumentException("Invalid text limits.");
        var watch = Stopwatch.StartNew();
        if (Title() != _title) throw new InvalidOperationException("The selected window title changed.");
        var request = new CacheRequest { TreeScope = TreeScope.Element };
        foreach (var property in new[] { AutomationElement.NameProperty, AutomationElement.ControlTypeProperty,
            AutomationElement.IsPasswordProperty, AutomationElement.ProcessIdProperty }) request.Add(property);
        var nodes = new JsonArray();
        int budget = AccessibilityText.NameBudget, visited = 0;
        var node = TreeWalker.ControlViewWalker.GetLastChild(_list, request);
        while (node is not null && nodes.Count < maximum && visited++ < 500 && watch.ElapsedMilliseconds < 8000)
        {
            var state = node.Cached;
            if (state.ProcessId != _target.Pid)
            {
                using var process = Process.GetProcessById(state.ProcessId);
                if (process.SessionId != (int)Core.Session.ChildSession.CurrentSessionId())
                    throw new InvalidOperationException("Refusing a control outside the child session.");
            }
            if (!state.IsPassword)
            {
                var entry = new JsonObject { ["role"] = AccessibilityReader.Role(state.ControlType) };
                var text = AccessibilityText.Known(state.Name, Math.Min(chars, budget), budget < chars ? "totalTextBudget" : "maxChars");
                text.Put(entry, "text", "name");
                budget -= text.Text.Length;
                nodes.Add(entry);
            }
            node = TreeWalker.ControlViewWalker.GetPreviousSibling(node, request);
        }
        if (Title() != _title) throw new InvalidOperationException("The selected window title changed during reading.");
        nodes = new JsonArray(nodes.Reverse().Select(n => n!.DeepClone()).ToArray());
        bool complete = node is null || nodes.Count >= maximum && watch.ElapsedMilliseconds < 8000;
        return new JsonObject { ["listName"] = _listName, ["selectorMode"] = "residentTail", ["nodes"] = nodes,
            ["windowComplete"] = complete, ["olderItemsOmitted"] = node is not null, ["truncated"] = node is not null,
            ["tailRead"] = true, ["elapsedMs"] = watch.ElapsedMilliseconds, ["readOnly"] = true };
    }
}
