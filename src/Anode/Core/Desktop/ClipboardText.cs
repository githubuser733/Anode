using System.Globalization;
using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using Anode.Core.Bridge;

namespace Anode.Core.Desktop;

/// <summary>Reads only after a confirmed copy from the selected seat window's process; never writes the clipboard.</summary>
internal static class ClipboardText
{
    internal readonly record struct State(uint Sequence, int OwnerPid);
    // Microsoft Learn: winuser.h GetClipboardSequenceNumber, GetClipboardOwner and GetWindowThreadProcessId.
    [DllImport("user32.dll")] private static extern uint GetClipboardSequenceNumber();
    [DllImport("user32.dll")] private static extern IntPtr GetClipboardOwner();
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);

    internal static State Current()
    {
        IntPtr owner = GetClipboardOwner();
        uint pid = 0;
        if (owner != IntPtr.Zero) GetWindowThreadProcessId(owner, out pid);
        return new(GetClipboardSequenceNumber(), checked((int)pid));
    }

    internal static JsonObject Handle(WindowTarget target, JsonObject request)
    {
        Seat.SeatHost.VerifyCurrentSession();
        WindowAccess.Verify(target);
        if (request.Str("action") == "clipboard_checkpoint")
        {
            var state = Current();
            if (state.Sequence == 0) throw new InvalidOperationException("Clipboard sequence is unavailable in the seat.");
            return new() { ["sequence"] = state.Sequence.ToString(CultureInfo.InvariantCulture), ["ownerPid"] = state.OwnerPid,
                ["note"] = "Checkpoint only; clipboard contents were not read. Locate the intended message and copy it, then read_clipboard with this sequence." };
        }
        uint after = uint.Parse(request.Str("afterSequence")!, NumberStyles.None, CultureInfo.InvariantCulture);
        JsonObject? result = null;
        Exception? failure = null;
        // WinForms Clipboard requires STA; UIA workers intentionally run MTA.
        var thread = new Thread(() =>
        {
            try { result = ReadVerified(target.Pid, after, request.Int("maxChars") ?? 20000, Current,
                () => System.Windows.Forms.Clipboard.GetText(TextDataFormat.UnicodeText)); }
            catch (Exception ex) { failure = ex; }
        }) { IsBackground = true, Name = "Anode clipboard read" };
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        if (!thread.Join(5000)) throw new TimeoutException("Clipboard read timed out. Do not assume the previous clipboard belongs to this copy.");
        if (failure is not null) throw new InvalidOperationException("Clipboard read was refused: " + failure.Message, failure);
        WindowAccess.Verify(target);
        return result!;
    }

    internal static JsonObject ReadVerified(int expectedPid, uint afterSequence, int maximum, Func<State> state, Func<string> read)
    {
        var before = state();
        if (before.Sequence == 0 || before.Sequence == afterSequence)
            throw new InvalidOperationException("Clipboard has not changed since the checkpoint. The copy was not verified; old contents were not read.");
        if (expectedPid <= 0 || before.OwnerPid != expectedPid)
            throw new InvalidOperationException("Clipboard owner does not match the selected window's process. Contents were not read; inspect the copy action or app helper process.");
        string value = read();
        var after = state();
        if (after != before) throw new InvalidOperationException("Clipboard changed during the read. Its contents were discarded; inspect before copying again.");
        if (value.Length == 0) throw new InvalidOperationException("The verified clipboard contains no Unicode text. Do not reuse earlier contents.");
        var result = new JsonObject { ["sequence"] = after.Sequence.ToString(CultureInfo.InvariantCulture), ["ownerPid"] = after.OwnerPid,
            ["clipboardVerified"] = true,
            ["note"] = "Clipboard changed after the checkpoint and its owner matches the selected process. Verify that the selected/copied message is the intended one." };
        var text = AccessibilityText.Known(value, maximum, "maxChars");
        text.Put(result, "text", "clipboard");
        result["textIncomplete"] = !text.Complete;
        return result;
    }
}
