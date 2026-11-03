using System.Diagnostics;
using System.Text.Json.Nodes;
using System.Windows.Automation;
using Anode.Core.Bridge;

namespace Anode.Core.Desktop;

/// <summary>Runs only in a disposable MTA worker inside the verified child session.</summary>
internal static class AccessibilityReader
{
    private static readonly TreeWalker Walker = TreeWalker.ControlViewWalker;
    private static readonly (AutomationPattern Pattern, string[] Actions)[] Patterns =
    {
        (InvokePattern.Pattern, new[] { "invoke" }), (ValuePattern.Pattern, new[] { "set_value" }),
        (TogglePattern.Pattern, new[] { "toggle" }), (SelectionItemPattern.Pattern, new[] { "select" }),
        (ExpandCollapsePattern.Pattern, new[] { "expand", "collapse" }),
        (ScrollItemPattern.Pattern, new[] { "scroll_into_view" }),
        (ScrollPattern.Pattern, new[] { "scroll" }), (RangeValuePattern.Pattern, new[] { "set_range" })
    };

    public static JsonObject Observe(WindowTarget target, JsonObject request)
    {
        var root = AutomationElement.FromHandle(WindowAccess.Verify(target));
        int maximum = request.Int("maxElements") ?? 200;
        int depthLimit = request.Int("maxDepth") ?? 8;
        int textBudget = request.Int("maxTextChars") ?? 6000;
        int nameLimit = request.Int("maxNameChars") ?? 500;
        int fieldLimit = request.Int("maxElementTextChars") ?? 3000;
        int nameBudget = AccessibilityText.NameBudget;
        bool offscreen = request.Bool("includeOffscreen") ?? false;
        var elements = new JsonArray();
        var warnings = new JsonArray();
        var watch = Stopwatch.StartNew();
        // An app can list the same control twice in its tree, even below itself (Chrome appeared to while a <select> list
        // was open). Walking it again would repeat its whole subtree until the budget ran out, so a control with a runtime
        // ID already queued is skipped. Its index still counts, so the paths seat_element follows stay right.
        var seen = new HashSet<string>();
        int repeated = 0;
        var queue = new Queue<(AutomationElement Element, int[]? RuntimeId, int[] Path, string? Parent)>();
        int[]? rootId = RuntimeId(root);
        if (rootId is { Length: > 0 }) seen.Add(Key(rootId));
        queue.Enqueue((root, rootId, Array.Empty<int>(), null));
        bool truncated = false;
        var truncationReasons = new HashSet<string>();
        int visited = 0;
        while (queue.Count > 0 && visited < maximum && watch.ElapsedMilliseconds < 4000)
        {
            var current = queue.Dequeue();
            visited++;
            JsonObject? node = null;
            string? key = current.RuntimeId is { Length: > 0 } queued ? Key(queued) : null;
            try
            {
                // A runtime ID that couldn't be read when queued is read again here, and its failure reported.
                int[] runtimeId = current.RuntimeId ?? current.Element.GetRuntimeId() ?? Array.Empty<int>();
                if (current.RuntimeId is null && runtimeId.Length > 0 && !seen.Add(Key(runtimeId)))
                {
                    visited--;
                    repeated++;
                    continue;
                }
                if (runtimeId.Length > 0) key = Key(runtimeId);
                node = Describe(current.Element, runtimeId, current.Path, current.Parent, target.Pid, nameLimit, fieldLimit, ref nameBudget, ref textBudget);
                string? parent = current.Parent;
                if (offscreen || node.Bool("offscreen") != true || current.Path.Length == 0)
                {
                    node["id"] = "e" + elements.Count;
                    parent = node.Str("id");
                    elements.Add(node);
                }
                int index = 0;
                var siblings = new HashSet<string>();
                var child = Walker.GetFirstChild(current.Element);
                if (current.Path.Length >= depthLimit)
                {
                    if (child is not null) { truncated = true; truncationReasons.Add("maxDepth"); }
                    continue;
                }
                while (child is not null && queue.Count + visited < maximum && watch.ElapsedMilliseconds < 4000)
                {
                    int[]? id = RuntimeId(child);
                    string? childKey = id is { Length: > 0 } ? Key(id) : null;
                    // A list of children that comes back to one it already gave has no more to give; repeats take no
                    // budget, so only this stops it.
                    if (childKey is not null && !siblings.Add(childKey)) { child = null; break; }
                    if (childKey is not null && !seen.Add(childKey)) repeated++;
                    else queue.Enqueue((child, id, current.Path.Append(index).ToArray(), parent));
                    index++;
                    child = Walker.GetNextSibling(child);
                }
                if (child is not null)
                {
                    truncated = true;
                    truncationReasons.Add(watch.ElapsedMilliseconds >= 4000 ? "timeLimit" : "maxElements");
                }
            }
            catch (Exception ex) when (ex is ElementNotAvailableException or InvalidOperationException or System.Runtime.InteropServices.COMException)
            {
                // A control that couldn't be described lets a copy of it listed later be described instead.
                if (node is null && key is not null) seen.Remove(key);
                if (warnings.Count < 3) warnings.Add("A control changed or did not expose readable accessibility information.");
            }
        }
        truncated |= queue.Count > 0;
        if (queue.Count > 0) truncationReasons.Add(watch.ElapsedMilliseconds >= 4000 ? "timeLimit" : "maxElements");
        bool incomplete = elements.OfType<JsonObject>().Any(node => new[] { "nameInfo", "textInfo", "selectedTextInfo" }
            .Any(field => node.Obj(field)?.Bool("complete") == false));
        bool longText = elements.OfType<JsonObject>().Any(node => new[] { "nameInfo", "textInfo", "selectedTextInfo" }
            .Any(field => node.Obj(field)?.Int("lengthAtLeast") >= AccessibilityText.LongTextThreshold));
        var result = new JsonObject
        {
            ["window"] = WindowAccess.Describe(target), ["elements"] = elements,
            ["truncated"] = truncated, ["warnings"] = warnings,
            ["truncationReasons"] = new JsonArray(truncationReasons.Order().Select(value => (JsonNode)value).ToArray()),
            ["textIncomplete"] = incomplete, ["longText"] = longText,
            ["needsTextRecovery"] = incomplete || longText,
            ["observedAt"] = DateTime.UtcNow.ToString("o"), ["elapsedMs"] = watch.ElapsedMilliseconds
        };
        // Not a warning: nothing was hidden, and seat_wait's "missing" needs a tree without warnings.
        if (repeated > 0) result["skippedRepeats"] = repeated;
        return result;

        // Null when the control can't say yet; it is then read again, and its failure reported, when it is described.
        static int[]? RuntimeId(AutomationElement element)
        {
            try { return element.GetRuntimeId(); }
            catch (Exception ex) when (ex is ElementNotAvailableException or InvalidOperationException or System.Runtime.InteropServices.COMException)
            {
                return null;
            }
        }
        static string Key(int[] id) => string.Join(".", id);
    }

    private static JsonObject Describe(AutomationElement element, int[] runtimeId, int[] path, string? parent,
        int rootPid, int nameLimit, int fieldLimit, ref int nameBudget, ref int textBudget)
    {
        var cache = new CacheRequest { TreeScope = TreeScope.Element };
        foreach (var property in new[]
        {
            AutomationElement.NameProperty, AutomationElement.ControlTypeProperty, AutomationElement.AutomationIdProperty,
            AutomationElement.ClassNameProperty, AutomationElement.IsEnabledProperty, AutomationElement.IsOffscreenProperty,
            AutomationElement.IsKeyboardFocusableProperty, AutomationElement.HasKeyboardFocusProperty,
            AutomationElement.IsPasswordProperty, AutomationElement.BoundingRectangleProperty, AutomationElement.ProcessIdProperty
        }) cache.Add(property);
        var data = element.GetUpdatedCache(cache).Cached;
        // The root's PID/session/start time is verified before and after traversal. Reopening that same
        // process for every message dominated live reads. Embedded cross-process controls still get checked.
        if (data.ProcessId != rootPid) VerifyElementSession(data.ProcessId);
        bool password = data.IsPassword;
        var supported = element.GetSupportedPatterns();
        var actions = new List<string>();
        if (!password)
            foreach (var pattern in Patterns)
                if (supported.Contains(pattern.Pattern)) actions.AddRange(pattern.Actions);
        if (data.IsKeyboardFocusable && !password) actions.Add("focus");
        if (!password) actions.Add("read_text");
        var bounds = data.BoundingRectangle;
        var node = new JsonObject
        {
            ["parent"] = parent, ["depth"] = path.Length,
            ["role"] = Role(data.ControlType),
            ["automationId"] = Clip(data.AutomationId, 256),
            ["className"] = Clip(data.ClassName, 128), ["enabled"] = data.IsEnabled,
            ["offscreen"] = data.IsOffscreen, ["focused"] = data.HasKeyboardFocus, ["password"] = password,
            ["bounds"] = bounds.IsEmpty ? null : WindowAccess.RectangleJson(new Rectangle(
                (int)bounds.X, (int)bounds.Y, Math.Max(0, (int)bounds.Width), Math.Max(0, (int)bounds.Height))),
            ["actions"] = new JsonArray(actions.Select(value => (JsonNode)value).ToArray()),
            // Private references are retained by the host and removed from public results.
            ["path"] = new JsonArray(path.Select(value => (JsonNode)value).ToArray()),
            ["runtimeId"] = new JsonArray(runtimeId.Select(value => (JsonNode)value).ToArray()),
            ["processId"] = data.ProcessId, ["nameFingerprint"] = AccessibilityText.Fingerprint(data.Name)
        };
        var name = AccessibilityText.Known(data.Name, Math.Min(nameLimit, nameBudget), nameBudget < nameLimit ? "nameBudget" : "maxNameChars");
        name.Put(node, "name", "name");
        nameBudget -= name.Text.Length;
        if (!password)
        {
            int limit = Math.Min(textBudget, fieldLimit);
            string reason = textBudget < fieldLimit ? "maxTextChars" : "maxElementTextChars";
            AccessibilityText? text = null;
            string source = "value";
            if (element.TryGetCurrentPattern(ValuePattern.Pattern, out var value))
            {
                var pattern = (ValuePattern)value;
                node["readOnly"] = pattern.Current.IsReadOnly;
                text = limit > 0 ? AccessibilityText.Known(pattern.Current.Value, limit, reason) : AccessibilityText.NotRead(reason);
                if (pattern.Current.IsReadOnly) actions.Remove("set_value");
            }
            else if (element.TryGetCurrentPattern(TextPattern.Pattern, out var document))
            {
                var pattern = (TextPattern)document;
                source = "document";
                text = AccessibilityText.Probe(count => pattern.DocumentRange?.GetText(count), limit, reason);
                text.Put(node, "text", source);
                textBudget -= text.Text.Length;
                int selectedLimit = Math.Min(textBudget, Math.Min(fieldLimit, 1000));
                if (selectedLimit > 0)
                {
                    var selectedRange = pattern.GetSelection()?.FirstOrDefault();
                    if (selectedRange is not null)
                    {
                        var selected = AccessibilityText.Probe(selectedRange.GetText, selectedLimit,
                            textBudget < Math.Min(fieldLimit, 1000) ? "maxTextChars" : "selectedTextLimit");
                        selected.Put(node, "selectedText", "selection");
                        textBudget -= selected.Text.Length;
                    }
                }
                text = null; // Document text was already charged before selection text.
            }
            if (text is not null)
            {
                text.Put(node, "text", source);
                textBudget -= text.Text.Length;
            }
        }
        if (!password && element.TryGetCurrentPattern(TogglePattern.Pattern, out var toggle))
            node["toggleState"] = ((TogglePattern)toggle).Current.ToggleState.ToString();
        if (!password && element.TryGetCurrentPattern(SelectionItemPattern.Pattern, out var selection))
            node["selected"] = ((SelectionItemPattern)selection).Current.IsSelected;
        if (!password && element.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out var expand))
            node["expandState"] = ((ExpandCollapsePattern)expand).Current.ExpandCollapseState.ToString();
        if (!password && element.TryGetCurrentPattern(RangeValuePattern.Pattern, out var range))
        {
            var state = ((RangeValuePattern)range).Current;
            node["range"] = new JsonObject
            {
                ["value"] = Finite(state.Value), ["minimum"] = Finite(state.Minimum), ["maximum"] = Finite(state.Maximum),
                ["smallChange"] = Finite(state.SmallChange), ["largeChange"] = Finite(state.LargeChange), ["readOnly"] = state.IsReadOnly
            };
            if (state.IsReadOnly) actions.Remove("set_range");
        }
        if (!password && element.TryGetCurrentPattern(ScrollPattern.Pattern, out var scrolling))
        {
            var state = ((ScrollPattern)scrolling).Current;
            node["scroll"] = new JsonObject
            {
                ["horizontal"] = state.HorizontallyScrollable, ["vertical"] = state.VerticallyScrollable,
                ["horizontalPercent"] = Finite(state.HorizontalScrollPercent), ["verticalPercent"] = Finite(state.VerticalScrollPercent)
            };
        }
        node["actions"] = new JsonArray(actions.Select(value => (JsonNode)value).ToArray());
        return node;
    }

    public static JsonObject Act(WindowTarget target, JsonObject reference, JsonObject request)
    {
        var root = AutomationElement.FromHandle(WindowAccess.Verify(target));
        var element = root;
        foreach (int index in reference["path"]!.AsArray().Select(value => value!.GetValue<int>()))
        {
            element = Walker.GetFirstChild(element);
            for (int i = 0; i < index && element is not null; i++) element = Walker.GetNextSibling(element);
            if (element is null) throw Stale();
        }
        int[] runtimeId = reference["runtimeId"]!.AsArray().Select(value => value!.GetValue<int>()).ToArray();
        if (!(element.GetRuntimeId() ?? Array.Empty<int>()).SequenceEqual(runtimeId)) throw Stale();
        var state = element.Current;
        VerifyElementSession(state.ProcessId);
        bool sameName = reference.Str("nameFingerprint") is { } fingerprint
            ? AccessibilityText.Fingerprint(state.Name) == fingerprint : Clip(state.Name, 500) == reference.Str("name");
        if (state.ProcessId != reference.Int("processId") || !sameName
            || Role(state.ControlType) != reference.Str("role")
            || Clip(state.AutomationId, 256) != reference.Str("automationId")) throw Stale();
        if (state.IsPassword) throw new InvalidOperationException("Password controls require the user's direct interaction.");
        WindowAccess.Verify(target);
        string action = request.Str("action") ?? throw new ArgumentException("An element action is required.");
        if (!state.IsEnabled && action != "read_text") throw new InvalidOperationException("The control is disabled. Inspect again.");
        if (reference["actions"] is not JsonArray actions || !actions.Any(value => value?.GetValue<string>() == action))
            throw new InvalidOperationException("That action was not offered by the observed control. Inspect again.");
        T Pattern<T>(AutomationPattern id) where T : class => element.TryGetCurrentPattern(id, out var value)
            ? (T)value : throw Stale();
        switch (action)
        {
            case "read_text": return ReadText(element, request);
            case "invoke": Pattern<InvokePattern>(InvokePattern.Pattern).Invoke(); break;
            case "focus": element.SetFocus(); break;
            case "set_value": return SetValue(Pattern<ValuePattern>(ValuePattern.Pattern), request.Str("value")!, state.ControlType == ControlType.ComboBox);
            case "toggle": Pattern<TogglePattern>(TogglePattern.Pattern).Toggle(); break;
            case "select": Pattern<SelectionItemPattern>(SelectionItemPattern.Pattern).Select(); break;
            case "expand": Pattern<ExpandCollapsePattern>(ExpandCollapsePattern.Pattern).Expand(); break;
            case "collapse": Pattern<ExpandCollapsePattern>(ExpandCollapsePattern.Pattern).Collapse(); break;
            case "scroll_into_view": Pattern<ScrollItemPattern>(ScrollItemPattern.Pattern).ScrollIntoView(); break;
            case "scroll":
                var amount = request.Str("amount") == "large" ? ScrollAmount.LargeIncrement : ScrollAmount.SmallIncrement;
                if (request.Str("direction") is "up" or "left") amount = request.Str("amount") == "large" ? ScrollAmount.LargeDecrement : ScrollAmount.SmallDecrement;
                Pattern<ScrollPattern>(ScrollPattern.Pattern).Scroll(
                    request.Str("direction") is "left" or "right" ? amount : ScrollAmount.NoAmount,
                    request.Str("direction") is "up" or "down" ? amount : ScrollAmount.NoAmount);
                break;
            case "set_range": Pattern<RangeValuePattern>(RangeValuePattern.Pattern).SetValue(request["number"]!.GetValue<double>()); break;
            default: throw new ArgumentException("Unknown element action.");
        }
        return Performed(action);
    }

    private static JsonObject ReadText(AutomationElement element, JsonObject request)
    {
        int limit = request.Int("maxChars") ?? 20000;
        string source = request.Str("textSource") ?? "auto";
        AccessibilityText text;
        if (source is "auto" or "value" && element.TryGetCurrentPattern(ValuePattern.Pattern, out var value))
        {
            source = "value";
            text = AccessibilityText.Known(((ValuePattern)value).Current.Value, limit, "maxChars");
        }
        else if (source is "auto" or "document" && element.TryGetCurrentPattern(TextPattern.Pattern, out var document))
        {
            source = "document";
            text = AccessibilityText.Probe(count => ((TextPattern)document).DocumentRange?.GetText(count), limit, "maxChars");
        }
        else if (source is "auto" or "name")
        {
            source = "name";
            text = AccessibilityText.Known(element.Current.Name, limit, "maxChars");
        }
        else throw new InvalidOperationException("The control does not expose the requested text source. Inspect again; use a screenshot to locate an expand/copy action if needed.");
        var result = Performed("read_text");
        text.Put(result, "text", source);
        result["textIncomplete"] = !text.Complete;
        result["note"] = "Read accessible text without focusing, selecting or copying. Observation consumed. Completeness refers to the app's UIA text, not hidden chat history.";
        return result;
    }

    private static JsonObject Performed(string action) =>
        new() { ["performed"] = action, ["note"] = "Observation consumed. Inspect again before the next action." };

    /// <summary>How long set_value watches for the control to take its new value. Chrome shows a change a moment later.</summary>
    internal const int SetValueSettleMs = 1500;

    /// <summary>
    /// Sets a value and reads it back. Some controls accept the call and ignore it, as Chrome's drop-down lists
    /// (&lt;select&gt;) do, and "performed" would then let an agent submit a form with the old choice. The failure never
    /// quotes the control's text: the pipe server logs errors, and a field can hold anything.
    /// </summary>
    private static JsonObject SetValue(ValuePattern pattern, string value, bool list)
    {
        string before = pattern.Current.Value ?? "";
        pattern.SetValue(value);
        string now;
        var watch = Stopwatch.StartNew();
        try
        {
            now = pattern.Current.Value ?? "";
            while (now == before && now != value && watch.ElapsedMilliseconds < SetValueSettleMs)
            {
                Thread.Sleep(50);
                now = pattern.Current.Value ?? "";
            }
        }
        catch (Exception ex) when (ex is ElementNotAvailableException or InvalidOperationException or TimeoutException
            or System.Runtime.InteropServices.COMException)
        {
            // The value was already sent, so this must not read as "nothing happened".
            var unread = Performed("set_value");
            unread["note"] = "The value was sent, but the control could not be read back (it may have gone away). Inspect again before the next action.";
            return unread;
        }
        if (now == value) return Performed("set_value");
        if (now != before)
        {
            // An app may reformat what it was given, for example a number field that adds its separators.
            var changed = Performed("set_value");
            changed["value"] = Clip(now, 500);
            changed["note"] = "The control changed the value it was given. Observation consumed. Inspect again before the next action.";
            return changed;
        }
        throw new InvalidOperationException(
            $"The control accepted set_value but still showed its old value {SetValueSettleMs} ms later. It ignored the change, or "
            + "took it and kept its old text (a tag field that adds a tag, a value it rewrites, a change still pending). "
            + (list ? "Drop-down lists such as Chrome's ignore set_value: expand the list and select the option, or click it. " : "")
            + "Inspect again before you retry.");
    }

    /// <summary>The role of a control whose type .NET's UI Automation client can't name.</summary>
    internal const string UnknownRole = "Unknown";

    /// <summary>
    /// The control type's programmatic name without its prefix, such as Button. .NET's client names only the types up to
    /// Separator and returns no type at all for later ones, SemanticZoom and AppBar, which File Explorer's command bar is.
    /// </summary>
    internal static string Role(ControlType? type) => type?.ProgrammaticName.Replace("ControlType.", "") ?? UnknownRole;

    private static InvalidOperationException Stale() => new("The control changed since observation. Inspect again; the action was not replayed.");
    private static void VerifyElementSession(int pid)
    {
        using var process = Process.GetProcessById(pid);
        if (process.SessionId != (int)Core.Session.ChildSession.CurrentSessionId())
            throw new InvalidOperationException("Refusing an accessibility element outside the seat session.");
    }
    private static string Clip(string? value, int maximum) => string.IsNullOrEmpty(value) || maximum <= 0 ? "" : value[..Math.Min(value.Length, maximum)];
    private static JsonNode? Finite(double value) => double.IsFinite(value) ? JsonValue.Create(value) : null;
}
