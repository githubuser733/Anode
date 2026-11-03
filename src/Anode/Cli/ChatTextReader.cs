using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using System.Windows.Automation;
using Anode.Core.Bridge;
using Anode.Core.Desktop;

namespace Anode.Cli;

/// <summary>Read-only scoped name extraction, launched by seat_exec in an already verified child session.</summary>
internal static class ChatTextReader
{
    internal static int Run(string[] args)
    {
        using var output = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true };
        try
        {
            var options = new Args(args);
            int Number(string key, int fallback, int maximum)
            {
                string? value = options.Value(key);
                if (value is null) return fallback;
                if (!int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out int parsed) || parsed < 1 || parsed > maximum)
                    throw new ArgumentException(key + " is outside its supported range.");
                return parsed;
            }
            int pid = Number("pid", 0, int.MaxValue);
            string title = options.Value("window-title") ?? throw new ArgumentException("window-title is required.");
            string list = options.Value("list-name") ?? throw new ArgumentException("list-name is required.");
            int maximum = Number("max-elements", 100, 500), chars = Number("max-chars", 10000, 20000);
            int budget = Number("budget-ms", 4000, 20000);
            int watchMs = Number("watch-ms", 0, 1200000);
            int intervalMs = Number("interval-ms", 250, 10000);
            string? streamPath = options.Value("stream");
            if (watchMs > 0 && (streamPath is null || !Path.IsPathFullyQualified(streamPath)))
                throw new ArgumentException("A resident read requires a new absolute --stream path.");
            if (pid < 1 || title.Length is 0 or > 256 || list.Length is 0 or > 256) throw new ArgumentException("A process, window title and list name are required.");
            Seat.SeatHost.VerifyCurrentSession();
            Core.Display.SeatDisplay.UsePhysicalPixels();
            var matches = WindowAccess.List().OfType<JsonObject>().Where(w => w.Int("pid") == pid && w.Str("title") == title).ToArray();
            if (matches.Length != 1) throw new InvalidOperationException("The selected child-session window is absent or ambiguous.");
            var target = WindowTarget.FromJson(matches[0].Obj("target")!);
            if (watchMs > 0)
                return Task.Run(() => Watch(target, title, list, maximum, chars, watchMs, intervalMs, streamPath!, options.Flag("bulk")))
                    .GetAwaiter().GetResult();
            var result = Task.Run(() => options.Flag("bulk")
                ? new CachedChatReader(target, list).Read(maximum, chars, options.Flag("tail"))
                : Read(target, list, maximum, chars, budget, options.Flag("tail"))).GetAwaiter().GetResult();
            result["windowTitle"] = title;
            result["processId"] = pid;
            result["session"] = (int)Core.Session.ChildSession.CurrentSessionId();
            output.WriteLine(result.ToJsonString());
            return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex.Message); return 1; }
    }

    private static int Watch(WindowTarget target, string title, string list, int maximum, int chars,
        int duration, int interval, string path, bool bulk)
    {
        using var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read);
        using var output = new StreamWriter(file, new UTF8Encoding(false)) { AutoFlush = true };
        using var requestsFile = new FileStream(path + ".requests", FileMode.CreateNew, FileAccess.ReadWrite, FileShare.ReadWrite);
        using var requests = new StreamReader(requestsFile, Encoding.ASCII);
        var reader = new CachedChatReader(target, list);
        var timer = Stopwatch.StartNew();
        long sequence = 0, lastVerified = 0;
        string? fingerprint = null;
        try
        {
            while (timer.ElapsedMilliseconds < duration && file.Position < 64 * 1024 * 1024 && !File.Exists(path + ".stop"))
            {
                long start = timer.ElapsedMilliseconds;
                if (start - lastVerified >= 5000) { Seat.SeatHost.VerifyCurrentSession(); lastVerified = start; }
                bool catchup = false;
                while (requests.ReadLine() is { } command) if (command == "catchup") catchup = true;
                var snapshot = catchup ? reader.Read(500, chars) : bulk ? reader.Read(maximum, chars) : reader.ReadRecent(maximum, chars);
                snapshot["catchup"] = catchup;
                snapshot["sequence"] = ++sequence;
                snapshot["observedAtUtc"] = DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture);
                snapshot["readStartedMs"] = start;
                snapshot["session"] = (int)Core.Session.ChildSession.CurrentSessionId();
                snapshot["processId"] = target.Pid;
                snapshot["windowTitle"] = title;
                string current = AccessibilityText.Fingerprint(snapshot["nodes"]!.ToJsonString());
                bool changed = catchup || current != fingerprint;
                snapshot["kind"] = changed ? "snapshot" : "heartbeat";
                if (!changed) snapshot.Remove("nodes");
                fingerprint = current;
                output.WriteLine(snapshot.ToJsonString());
                int remaining = interval - (int)(timer.ElapsedMilliseconds - start);
                if (remaining > 0) Thread.Sleep(remaining);
            }
            output.WriteLine(new JsonObject { ["kind"] = "stopped", ["sequence"] = sequence,
                ["elapsedMs"] = timer.ElapsedMilliseconds, ["stopRequested"] = File.Exists(path + ".stop") }.ToJsonString());
            return 0;
        }
        catch (Exception ex)
        {
            output.WriteLine(new JsonObject { ["kind"] = "error", ["sequence"] = sequence,
                ["observedAtUtc"] = DateTime.UtcNow.ToString("O"), ["error"] = ex.Message }.ToJsonString());
            Console.Error.WriteLine(ex.Message);
            return 1;
        }
    }

    internal static JsonObject Read(WindowTarget target, string listName, int maximum, int chars, int budgetMs = 4000, bool tail = false)
    {
        if (maximum is < 1 or > 500 || chars is < 1 or > 20000 || budgetMs is < 1 or > 20000) throw new ArgumentException("Invalid text limits.");
        var watch = Stopwatch.StartNew();
        long processCheckMs = 0;
        void Verify(int pid) { if (pid == target.Pid) return; var timer = Stopwatch.StartNew(); VerifySession(pid); processCheckMs += timer.ElapsedMilliseconds; }
        var cache = new CacheRequest { TreeScope = TreeScope.Element };
        foreach (var property in new[] { AutomationElement.NameProperty, AutomationElement.ControlTypeProperty,
            AutomationElement.IsPasswordProperty, AutomationElement.ProcessIdProperty }) cache.Add(property);
        var walker = TreeWalker.ControlViewWalker;
        var queue = new Queue<AutomationElement>();
        var root = AutomationElement.FromHandle(WindowAccess.Verify(target));
        int visited = 0;
        var list = root.FindFirst(TreeScope.Descendants, new AndCondition(
            new PropertyCondition(AutomationElement.NameProperty, listName),
            new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.List)))?.GetUpdatedCache(cache);
        string selectorMode = list is null ? "controlView" : "nativeSearch";
        if (list is null) queue.Enqueue(root.GetUpdatedCache(cache));
        while (list is null && queue.Count > 0 && visited < 500 && watch.ElapsedMilliseconds < budgetMs)
        {
            var item = queue.Dequeue(); visited++;
            var state = item.Cached;
            Verify(state.ProcessId);
            if (!state.IsPassword && state.Name == listName && state.ControlType == ControlType.List) { list = item; break; }
            var child = walker.GetFirstChild(item, cache);
            while (child is not null && visited + queue.Count < 500 && watch.ElapsedMilliseconds < budgetMs)
            {
                queue.Enqueue(child);
                child = walker.GetNextSibling(child, cache);
            }
        }
        if (list is null) throw new InvalidOperationException($"The selected message list was not found within the bounded search (visited={visited}).");
        Verify(list.Cached.ProcessId);
        var nodes = new JsonArray();
        int budget = AccessibilityText.NameBudget;
        var node = tail ? walker.GetLastChild(list, cache) : walker.GetFirstChild(list, cache);
        while (node is not null && nodes.Count < maximum && watch.ElapsedMilliseconds < budgetMs)
        {
            var data = node.Cached;
            Verify(data.ProcessId);
            if (!data.IsPassword)
            {
                var text = AccessibilityText.Known(data.Name, Math.Min(chars, budget), budget < chars ? "totalTextBudget" : "maxChars");
                var entry = new JsonObject { ["index"] = nodes.Count, ["role"] = AccessibilityReader.Role(data.ControlType) };
                int[]? identity = node.GetRuntimeId();
                entry["elementKey"] = identity is { Length: > 0 }
                    ? AccessibilityText.Fingerprint(string.Join(".", identity.Select(i => i.ToString(CultureInfo.InvariantCulture)))) : null;
                text.Put(entry, "text", "name");
                nodes.Add(entry);
                budget -= text.Text.Length;
            }
            node = tail ? walker.GetPreviousSibling(node, cache) : walker.GetNextSibling(node, cache);
        }
        WindowAccess.Verify(target);
        bool windowComplete = node is null || tail && nodes.Count >= maximum && watch.ElapsedMilliseconds < budgetMs;
        if (tail)
        {
            nodes = new JsonArray(nodes.Reverse().Select(n => n!.DeepClone()).ToArray());
            for (int i = 0; i < nodes.Count; i++) nodes[i]!["index"] = i;
        }
        return new JsonObject
        {
            ["listName"] = listName, ["selectorMode"] = selectorMode, ["selectorVisited"] = visited, ["nodes"] = nodes,
            ["truncated"] = node is not null,
            ["tailRead"] = tail, ["windowComplete"] = windowComplete,
            ["olderItemsOmitted"] = tail && node is not null,
            ["truncationReason"] = node is null ? null : watch.ElapsedMilliseconds >= budgetMs ? "timeLimit" : "maxElements",
            ["budgetMs"] = budgetMs,
            ["elapsedMs"] = watch.ElapsedMilliseconds, ["processCheckMs"] = processCheckMs,
            ["readOnly"] = true, ["screenshotRequested"] = false, ["clipboardRead"] = false
        };
    }

    private static void VerifySession(int pid)
    {
        using var process = Process.GetProcessById(pid);
        if (process.SessionId != (int)Core.Session.ChildSession.CurrentSessionId()) throw new InvalidOperationException("Refusing a control outside the child session.");
    }
}
