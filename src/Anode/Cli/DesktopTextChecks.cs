using System.Text.Json.Nodes;
using System.Windows.Automation;
using System.Windows.Automation.Provider;
using Anode.Core.Bridge;
using Anode.Core.Desktop;
using Anode.Mcp;

namespace Anode.Cli;

internal static partial class DesktopChecks
{
    internal static string ScopedChatRead()
    {
        Hosted(new LoopWindow(messages: true), "Anode scoped chat check", target =>
        {
            var full = ChatTextReader.Read(target, "Messages", 100, 20000);
            var nodes = full["nodes"]!.AsArray();
            Require(nodes.Count == 2 && full.Bool("truncated") == false && nodes[0]!["text"]!.ToString().Length == 7505,
                "Scoped read failed to recover the full long message.");
            var capped = ChatTextReader.Read(target, "Messages", 1, 64);
            Require(capped.Bool("truncated") == true && capped.Str("truncationReason") == "maxElements"
                && capped["nodes"]![0]!["textInfo"]!["complete"]!.GetValue<bool>() == false,
                "Scoped read hid its node/field limits.");
            try { ChatTextReader.Read(target, "Messages", 1, 0); throw new Exception("Invalid text limit was accepted."); }
            catch (ArgumentException) { }
            try { ChatTextReader.Read(target, "Messages", 1, 64, 0); throw new Exception("Invalid time budget was accepted."); }
            catch (ArgumentException) { }
            return "ok";
        });
        Hosted(new LoopWindow(messages: true, secretMessage: true), "Anode scoped password check", target =>
        {
            var result = ChatTextReader.Read(target, "Messages", 100, 20000);
            Require(result["nodes"]!.AsArray().Count == 1 && !result.ToJsonString().Contains(new string('x', 100), StringComparison.Ordinal),
                "A protected message was exported by the scoped reader.");
            return "ok";
        });
        Hosted(new AppBarWindow(), "Anode missing list check", target =>
        {
            try { ChatTextReader.Read(target, "Missing", 100, 20000); throw new Exception("A missing list was reported as an empty history."); }
            catch (InvalidOperationException ex) when (ex.Message.StartsWith("The selected message list", StringComparison.Ordinal)) { }
            return "ok";
        });
        return "scoped message selection, complete long names, node/field limits, password omission and missing-list refusal passed";
    }
    internal static string ClipboardRecovery()
    {
        int reads = 0;
        string Read() { reads++; return "长消息\n" + new string('文', 8000); }
        void Refused(Func<ClipboardText.State> state, uint checkpoint)
        {
            int before = reads;
            try { ClipboardText.ReadVerified(22, checkpoint, 20000, state, Read); throw new Exception("An unverified clipboard was accepted."); }
            catch (InvalidOperationException) { }
            Require(reads == before, "Unverified clipboard content was accessed.");
        }
        Refused(() => new(100, 22), 100);
        Refused(() => new(0, 22), 99);
        Refused(() => new(100, 23), 99);
        Refused(() => new(100, 0), 99);
        var full = ClipboardText.ReadVerified(22, 99, 20000, () => new(100, 22), Read);
        Require(full.Bool("clipboardVerified") == true && full.Obj("textInfo")?.Bool("complete") == true && full.Str("text")!.Length == 8004,
            "A fresh owner-matched long clipboard was not recovered.");
        var clipped = ClipboardText.ReadVerified(22, 99, 20, () => new(100, 22), Read);
        Require(clipped.Str("text")?.Length == 20 && clipped.Bool("textIncomplete") == true, "Clipboard clipping was not reported.");
        int call = 0;
        try { ClipboardText.ReadVerified(22, 99, 20000, () => new(++call == 1 ? 100u : 101u, 22), Read); throw new Exception("A racing clipboard was accepted."); }
        catch (InvalidOperationException ex) when (ex.Message.Contains("changed during", StringComparison.Ordinal)) { }
        try { ClipboardText.ReadVerified(22, 99, 20000, () => new(100, 22), () => ""); throw new Exception("Empty clipboard text was accepted."); }
        catch (InvalidOperationException ex) when (ex.Message.Contains("no Unicode text", StringComparison.Ordinal)) { }
        Require(Tools.ValidateArguments("seat_window", new() { ["windowId"] = "w", ["action"] = "read_clipboard" }) is not null,
            "Clipboard read accepted no freshness checkpoint.");
        Require(Tools.ValidateArguments("seat_window", new() { ["windowId"] = "w", ["action"] = "read_clipboard", ["afterSequence"] = "4294967295" }) is null,
            "A valid UInt32 clipboard sequence was rejected.");
        Require(Tools.ValidateArguments("seat_window", new() { ["windowId"] = "w", ["action"] = "read_clipboard", ["afterSequence"] = "4294967296" }) is not null,
            "An overflowing clipboard sequence was accepted.");
        Require(Tools.ValidateArguments("seat_window", new() { ["windowId"] = "w", ["action"] = "focus", ["afterSequence"] = "1" }) is not null,
            "Clipboard-only options reached focus.");
        var summary = DesktopPresentation.Summary(new() { ["text"] = "line1\nline2\u001b[2J" });
        Require(summary.Contains("line1\nline2", StringComparison.Ordinal) && !summary.Contains('\u001b'), "Text readback lost line breaks or exposed terminal escapes.");
        _ = ClipboardText.Current(); // Native metadata only: no clipboard text is read or overwritten.
        return "clipboard freshness, source process, races, long text, limits and UInt32 validation passed; native metadata queried without reading clipboard contents";
    }
    private sealed class LongTextWindow : HiddenWindow, IValueProvider
    {
        internal string Label = new('名', 7500);
        internal string Content = new('文', 8000);
        internal bool Password;
        public override object? GetPatternProvider(int id) => id == ValuePattern.Pattern.Id ? this : null;
        public override object? GetPropertyValue(int id) => id == AutomationElement.ControlTypeProperty.Id ? ControlType.Edit.Id
            : id == AutomationElement.NameProperty.Id ? Label : id == AutomationElement.IsPasswordProperty.Id ? Password
            : id == AutomationElement.IsEnabledProperty.Id ? true : null;
        public bool IsReadOnly => true;
        public string Value => Content;
        public void SetValue(string value) => throw new InvalidOperationException("The fixture is read-only.");
    }

    internal static string TextCompleteness()
    {
        Require(AccessibilityText.Known("abc", 3, "limit").Complete, "An exact-length field was called incomplete.");
        var clipped = AccessibilityText.Known("ab😀cd", 3, "limit");
        Require(clipped.Text == "ab" && clipped.Length == 6 && !clipped.Complete, "A surrogate pair was split or its original length was lost.");
        int requested = 0;
        var probe = AccessibilityText.Probe(count => { requested = count; return "1234"; }, 3, "cap");
        Require(requested == 4 && probe.Text == "123" && probe.Length is null && probe.LengthAtLeast == 4 && !probe.Complete,
            "A bounded provider probe claimed to know the complete length.");
        Require(AccessibilityText.Probe(_ => "123", 3, "cap").Complete, "Exact-limit provider text was called truncated.");
        Require(!AccessibilityText.Probe(_ => throw new InvalidOperationException("A zero budget called the provider."), 0, "budget").Complete,
            "A zero budget claimed to have read the field.");
        Require(!AccessibilityText.Probe(_ => null, 3, "cap").Complete, "Unavailable provider text was called complete.");
        foreach (string field in new[] { "maxNameChars", "maxElementTextChars" })
        {
            Require(Tools.ValidateArguments("seat_observe", new() { ["windowId"] = "w", [field] = 20000 }) is null, "A supported text limit was rejected.");
            Require(Tools.ValidateArguments("seat_observe", new() { ["windowId"] = "w", [field] = 0 }) is not null, "A zero text limit was accepted.");
            Require(Tools.ValidateArguments("seat_observe", new() { ["windowId"] = "w", [field] = 20001 }) is not null, "An oversized text limit was accepted.");
        }
        Require(Tools.ValidateArguments("seat_element", new() { ["snapshotId"] = "s", ["elementId"] = "e0", ["action"] = "read_text", ["textSource"] = "document", ["maxChars"] = 20000 }) is null,
            "The targeted text reader's schema was rejected.");
        Require(Tools.ValidateArguments("seat_element", new() { ["snapshotId"] = "s", ["elementId"] = "e0", ["action"] = "invoke", ["maxChars"] = 5 }) is not null,
            "Text-only options reached a mutating action.");
        var textOnly = new JsonObject { ["includeScreenshot"] = false, ["screenshotOnTextIssue"] = true };
        Require(!DesktopTools.ShouldCapture(textOnly, new() { ["needsTextRecovery"] = false }), "Short complete text triggered a screenshot.");
        Require(DesktopTools.ShouldCapture(textOnly, new() { ["needsTextRecovery"] = true }), "Incomplete/long text did not trigger the optional screenshot.");
        Require(!DesktopTools.ShouldCapture(new() { ["includeScreenshot"] = false }, new() { ["needsTextRecovery"] = true }), "A screenshot was taken without being requested.");

        var window = new LongTextWindow();
        return Hosted(window, "Anode long text check", target =>
        {
            var observation = AccessibilityReader.Observe(target, new() { ["maxDepth"] = 0 });
            var root = Root(observation);
            Require(root.Str("name")?.Length == 500 && root.Obj("nameInfo")?.Int("length") == 7500 && root.Obj("nameInfo")?.Bool("complete") == false,
                "Default name clipping lost its original length/completeness.");
            Require(root.Str("text")?.Length == 3000 && root.Obj("textInfo")?.Int("length") == 8000 && observation.Bool("textIncomplete") == true,
                "Default value clipping was not exposed.");
            Require(observation.Bool("longText") == true && observation.Bool("truncated") == false,
                "Field clipping was confused with a partial tree or a leaf was called depth-truncated.");
            var reread = AccessibilityReader.Act(target, root, new() { ["action"] = "read_text" });
            Require(reread.Str("text") == window.Content && reread.Obj("textInfo")?.Bool("complete") == true, "Targeted reread did not recover the complete long value.");
            var name = AccessibilityReader.Act(target, root, new() { ["action"] = "read_text", ["textSource"] = "name" });
            Require(name.Str("text") == window.Label, "Explicit name reread returned a document/value instead.");
            var capped = AccessibilityReader.Act(target, root, new() { ["action"] = "read_text", ["maxChars"] = 100 });
            Require(capped.Str("text")?.Length == 100 && capped.Bool("textIncomplete") == true, "Targeted read ignored its cap.");
            var expanded = Root(AccessibilityReader.Observe(target, new() { ["maxDepth"] = 0, ["maxNameChars"] = 20000, ["maxElementTextChars"] = 20000, ["maxTextChars"] = 20000 }));
            Require(expanded.Str("name") == window.Label && expanded.Str("text") == window.Content, "Expanded name/value limits did not reach UIA.");
            var exhausted = Root(AccessibilityReader.Observe(target, new() { ["maxDepth"] = 0, ["maxTextChars"] = 0 }));
            Require(exhausted.Obj("textInfo")?.Bool("complete") == false && exhausted.Obj("textInfo")?.Str("reason") == "maxTextChars", "Exhausted total text budget was silent.");
            window.Label = new string('名', 7499) + "变";
            try { AccessibilityReader.Act(target, root, new() { ["action"] = "read_text" }); throw new InvalidOperationException("A stale long-name reference was accepted."); }
            catch (InvalidOperationException ex) when (ex.Message.Contains("changed since observation", StringComparison.Ordinal)) { }
            window.Password = true;
            var password = Root(AccessibilityReader.Observe(target, new() { ["maxDepth"] = 0 }));
            Require(password["text"] is null && !password["actions"]!.AsArray().Any(a => a?.ToString() == "read_text"), "A password exposed text or a read action.");
            var summary = DesktopPresentation.Summary(observation);
            Require(summary.Contains("name incomplete", StringComparison.Ordinal) && summary.Contains("Some text is incomplete", StringComparison.Ordinal), "The text summary hid clipping metadata.");
            return "text caps, exact lengths, surrogate pairs, budget exhaustion, hidden-window UIA, targeted reread, stale long names, password refusal and conditional screenshots passed";
        });
    }

    internal static async Task<string> TextPipelineAsync()
    {
        var target = new WindowTarget(10, 20, 30);
        int observations = 0, reads = 0;
        var desktop = new DesktopTools((request, _) =>
        {
            if (request.Str("op") == "windows") return Task.FromResult(new JsonObject { ["windows"] = new JsonArray(new JsonObject { ["target"] = target.ToJson() }) });
            if (request.Str("op") == "observe")
            {
                observations++;
                Require(request.Int("maxNameChars") == 9000 && request.Int("maxElementTextChars") == 12000, "Text limits were dropped before the worker.");
                return Task.FromResult(new JsonObject { ["elements"] = new JsonArray(new JsonObject { ["id"] = "e0", ["name"] = "message", ["nameFingerprint"] = "private", ["path"] = new JsonArray(), ["runtimeId"] = new JsonArray(1), ["processId"] = 20, ["actions"] = new JsonArray("read_text") }) });
            }
            Require(request.Str("op") == "element" && request.Str("action") == "read_text" && request.Obj("element")?.Str("nameFingerprint") == "private", "Private text identity was lost before the worker.");
            reads++;
            return Task.FromResult(new JsonObject { ["text"] = "complete message", ["textInfo"] = new JsonObject { ["complete"] = true } });
        });
        var windows = (await desktop.HandleAsync("desktop.windows", new())).Obj("result")!["windows"]!.AsArray();
        var observed = (await desktop.HandleAsync("desktop.observe", new() { ["windowId"] = windows[0]!["windowId"]!.ToString(), ["includeScreenshot"] = false, ["maxNameChars"] = 9000, ["maxElementTextChars"] = 12000 })).Obj("result")!;
        Require(observed["elements"]![0]!["nameFingerprint"] is null, "Private name fingerprint escaped into the public observation.");
        var args = new JsonObject { ["snapshotId"] = observed.Str("snapshotId"), ["elementId"] = "e0", ["action"] = "read_text" };
        var result = await desktop.HandleAsync("desktop.element", args);
        Require(result.Obj("result")?.Str("text") == "complete message", "Targeted text was dropped from the result.");
        try { await desktop.HandleAsync("desktop.element", args); throw new InvalidOperationException("A consumed text reference was reused."); }
        catch (InvalidOperationException ex) when (ex.Message.Contains("already used", StringComparison.Ordinal)) { }
        Require(observations == 1 && reads == 1, "A targeted text read was replayed.");
        return "public/private references, parameter forwarding and snapshot consumption passed";
    }
}

internal static class TextReadingChecks
{
    internal static int Run()
    {
        ConsoleBridge.Attach();
        try
        {
            Console.WriteLine(DesktopChecks.TextCompleteness());
            Console.WriteLine(DesktopChecks.TextPipelineAsync().GetAwaiter().GetResult());
            Console.WriteLine(DesktopChecks.ClipboardRecovery());
            Console.WriteLine(DesktopChecks.ScopedChatRead());
            Console.WriteLine(CliChecks.HelpRouting());
            Console.WriteLine(CliChecks.Arguments());
            Console.WriteLine(CliChecks.HelpText());
            Console.WriteLine(McpChecks.Validation().GetAwaiter().GetResult());
            Console.WriteLine(DesktopChecks.Dispatch().GetAwaiter().GetResult());
            Console.WriteLine(DesktopChecks.Presentation());
            Console.WriteLine(DesktopChecks.IgnoredValues());
            Console.WriteLine(DesktopChecks.References());
            Console.WriteLine(DesktopChecks.RepeatedElements());
            Console.WriteLine(DesktopChecks.UnknownControlTypes());
            Console.WriteLine("ANODE_TEXT_CHECKS_OK realInputSent=false liveSeatStarted=false filesDeleted=false");
            return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex.ToString()); return 1; }
    }
}
