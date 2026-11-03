using System.Runtime.InteropServices;
using Anode.Core.Timeline;
using Anode.Core.Util;

namespace Anode.Daemon;

/// <summary>Observes messages delivered to this viewer, without intercepting global input or recording text.</summary>
internal sealed class HumanInputRecorder : IMessageFilter, IDisposable
{
    private readonly Control _viewer;
    private readonly Func<bool> _allowed;
    private readonly Func<Size> _remoteSize;
    private readonly OperationTimeline _timeline;
    private readonly Func<Keys> _modifiers;
    private readonly ViewerThreadMessages? _native;
    private long _managedMessages, _acceptedMessages;
    private readonly System.Windows.Forms.Timer _typing = new() { Interval = 500 };
    private int _characters;
    private int _typingKeys;
    private (Point Start, string Button)? _press;

    internal HumanInputRecorder(Control viewer, Func<bool> allowed, Func<Size> remoteSize, OperationTimeline timeline, Func<Keys>? modifiers = null, bool nativeThreads = false)
    {
        _viewer = viewer;
        _allowed = allowed;
        _remoteSize = remoteSize;
        _timeline = timeline;
        _modifiers = modifiers ?? (() => Control.ModifierKeys);
        _typing.Tick += (_, _) => Flush();
        Application.AddMessageFilter(this);
        if (nativeThreads) _native = new ViewerThreadMessages(viewer, (message, keys) => ProcessMessage(ref message, keys));
    }

    public bool PreFilterMessage(ref Message message)
    {
        if (!IsInputMessage(message.Msg)) return false;
        _managedMessages++;
        // The native source observes these messages too. Count the managed path but never record twice.
        return _native is null ? ProcessMessage(ref message, _modifiers()) : false;
    }

    internal static bool IsInputMessage(int id) => id is 0x0100 or 0x0102 or 0x0104 or 0x0201 or 0x0202 or 0x0203 or 0x0204 or 0x0205 or 0x0206 or 0x0207 or 0x0208 or 0x0209 or 0x020A or 0x020E;
    internal static bool IsNamedKey(Keys key, Keys modifiers) => (modifiers & (Keys.Control | Keys.Alt)) != 0
        || key is Keys.Enter or Keys.Escape or Keys.Tab or Keys.Back or Keys.Delete or Keys.Left or Keys.Right or Keys.Up or Keys.Down
        or Keys.Home or Keys.End or Keys.PageUp or Keys.PageDown || key is >= Keys.F1 and <= Keys.F24;

    private bool ProcessMessage(ref Message message, Keys modifiers)
    {
        if (!_viewer.IsHandleCreated || (message.HWnd != _viewer.Handle && !IsChild(_viewer.Handle, message.HWnd)) || !_allowed()) return false;
        _acceptedMessages++;
        if (message.Msg == 0x0102)
        {
            if (message.WParam.ToInt64() >= 32) { _characters++; _typing.Stop(); _typing.Start(); }
            return false;
        }
        if (message.Msg is 0x0100 or 0x0104)
        {
            var key = (Keys)(message.WParam.ToInt64() & 0xFFFF);
            if (IsNamedKey(key, modifiers))
            {
                Flush();
                string name = ((modifiers & Keys.Control) != 0 ? "Ctrl+" : "") + ((modifiers & Keys.Alt) != 0 ? "Alt+" : "")
                    + ((modifiers & Keys.Shift) != 0 ? "Shift+" : "") + key;
                _timeline.Human("input.key", name);
            }
            else if (key is not (Keys.ShiftKey or Keys.ControlKey or Keys.Menu or Keys.LWin or Keys.RWin))
            {
                // Some ActiveX input paths never emit WM_CHAR. Count keystrokes without keeping their values.
                _typingKeys++;
                _typing.Stop();
                _typing.Start();
            }
            return false;
        }
        if (message.Msg is not (0x0201 or 0x0202 or 0x0203 or 0x0204 or 0x0205 or 0x0206 or 0x0207 or 0x0208 or 0x0209 or 0x020A or 0x020E)) return false;
        Flush();
        int x = (short)(message.LParam.ToInt64() & 0xFFFF), y = (short)((message.LParam.ToInt64() >> 16) & 0xFFFF);
        var point = new Point(x, y);
        bool wheel = message.Msg is 0x020A or 0x020E;
        if (wheel) point = _viewer.PointToClient(point);
        else MapWindowPoints(message.HWnd, _viewer.Handle, ref point, 1);
        var remote = RemotePoint(point, _viewer.ClientSize, _remoteSize());
        string where = remote is { } at ? $" at {at.X},{at.Y}" : "";
        if (wheel) _timeline.Human("input.scroll", "Scroll" + where);
        else if (message.Msg is 0x0201 or 0x0203 or 0x0204 or 0x0206 or 0x0207 or 0x0209)
        {
            string button = message.Msg is 0x0204 or 0x0206 ? "right" : message.Msg is 0x0207 or 0x0209 ? "middle" : "left";
            _press = (remote ?? point, button);
        }
        else if (_press is { } press)
        {
            _press = null;
            var end = remote ?? point;
            bool drag = Math.Abs(end.X - press.Start.X) > 4 || Math.Abs(end.Y - press.Start.Y) > 4;
            _timeline.Human(drag ? "input.drag" : "input.click", drag
                ? $"{press.Button} drag {press.Start.X},{press.Start.Y} to {end.X},{end.Y}"
                : $"{press.Button} click at {press.Start.X},{press.Start.Y}");
        }
        return false; // Observation must never swallow or replay the input.
    }

    internal static Point? RemotePoint(Point point, Size viewport, Size remote)
    {
        if (viewport.Width <= 0 || viewport.Height <= 0 || remote.Width <= 0 || remote.Height <= 0
            || point.X < 0 || point.Y < 0 || point.X >= viewport.Width || point.Y >= viewport.Height) return null;
        return new Point(Math.Clamp((int)((long)point.X * remote.Width / viewport.Width), 0, remote.Width - 1),
            Math.Clamp((int)((long)point.Y * remote.Height / viewport.Height), 0, remote.Height - 1));
    }

    internal void Flush()
    {
        _typing.Stop();
        int count = _characters;
        int keys = _typingKeys;
        _characters = 0;
        _typingKeys = 0;
        if (count > 0) _timeline.Human("input.text", $"Typed {count} characters");
        else if (keys > 0) _timeline.Human("input.text", $"Keyboard input: {keys} key events");
    }

    public void Dispose()
    {
        _native?.Dispose();
        Flush();
        Application.RemoveMessageFilter(this);
        _typing.Dispose();
    }

    internal void ReportCoverage() => Log.Info($"human input observer: threads={_native?.ThreadCount ?? 0}, native={_native?.Observed ?? 0}, managed={_managedMessages}, accepted={_acceptedMessages}, dropped={_native?.Dropped ?? 0}");

    internal static bool ViewerIsForeground(Control viewer) => viewer.IsHandleCreated && GetAncestor(viewer.Handle, 2) == GetForegroundWindow();

    [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool IsChild(IntPtr parent, IntPtr child);

    [DllImport("user32.dll")]
    private static extern int MapWindowPoints(IntPtr from, IntPtr to, ref Point point, uint count);
}
