using System.Runtime.InteropServices;
using Anode.Core.Util;

namespace Anode.Daemon;

/// <summary>Observes only this process's viewer threads, including ActiveX's native input thread.</summary>
internal sealed class ViewerThreadMessages : IDisposable
{
    private readonly Control _viewer;
    private readonly Action<Message, Keys> _receive;
    private readonly HookProc _callback;
    private readonly Dictionary<uint, IntPtr> _hooks = new();
    private readonly System.Windows.Forms.Timer _refresh = new() { Interval = 250 };
    private IntPtr _handle;
    private int _disposed, _pending;
    private long _observed, _dropped;
    public long Observed => Interlocked.Read(ref _observed);
    public long Dropped => Interlocked.Read(ref _dropped);
    public int ThreadCount => _hooks.Count;

    public ViewerThreadMessages(Control viewer, Action<Message, Keys> receive)
    {
        _viewer = viewer;
        _receive = receive;
        _callback = Observe;
        _refresh.Tick += (_, _) => Refresh();
        _refresh.Start();
        Refresh();
    }

    internal void Refresh()
    {
        if (_disposed != 0 || !_viewer.IsHandleCreated) return;
        _handle = _viewer.Handle;
        var threads = new HashSet<uint>();
        void Add(IntPtr hwnd)
        {
            uint thread = GetWindowThreadProcessId(hwnd, out uint process);
            if (thread != 0 && process == Environment.ProcessId) threads.Add(thread);
        }
        Add(_handle);
        EnumChildWindows(_handle, (hwnd, _) => { Add(hwnd); return true; }, IntPtr.Zero);
        foreach (uint stale in _hooks.Keys.Where(t => !threads.Contains(t)).ToArray())
        {
            UnhookWindowsHookEx(_hooks[stale]);
            _hooks.Remove(stale);
        }
        foreach (uint thread in threads.Where(t => !_hooks.ContainsKey(t)))
        {
            // A nonzero, verified same-process thread is mandatory: never install a desktop-wide hook.
            IntPtr hook = SetWindowsHookExW(3, _callback, IntPtr.Zero, thread);
            if (hook == IntPtr.Zero)
                Log.Warn($"human input observer: could not observe viewer thread {thread}, error {Marshal.GetLastWin32Error()}");
            else
            {
                _hooks.Add(thread, hook);
                Log.Info($"human input observer: attached viewer thread {thread}");
            }
        }
    }

    private IntPtr Observe(int code, IntPtr removed, IntPtr data)
    {
        try
        {
            if (code >= 0 && removed == new IntPtr(1) && Volatile.Read(ref _disposed) == 0)
            {
                var native = Marshal.PtrToStructure<NativeMessage>(data);
                IntPtr viewer = _handle;
                if (HumanInputRecorder.IsInputMessage((int)native.Id) && viewer != IntPtr.Zero
                    && (native.Window == viewer || IsChild(viewer, native.Window)))
                {
                    Interlocked.Increment(ref _observed);
                    Keys modifiers = (GetKeyState(0x11) < 0 ? Keys.Control : Keys.None)
                        | (GetKeyState(0x12) < 0 ? Keys.Alt : Keys.None)
                        | (GetKeyState(0x10) < 0 ? Keys.Shift : Keys.None);
                    // Printable values never cross the native observer boundary; only their count matters.
                    IntPtr value = native.WParam;
                    if (native.Id == 0x0102 && value.ToInt64() >= 32) value = new IntPtr('*');
                    if (native.Id is 0x0100 or 0x0104 && !HumanInputRecorder.IsNamedKey((Keys)value.ToInt64(), modifiers)
                        && (Keys)value.ToInt64() is not (Keys.ShiftKey or Keys.ControlKey or Keys.Menu or Keys.LWin or Keys.RWin)) value = IntPtr.Zero;
                    var message = Message.Create(native.Window, (int)native.Id, value, native.LParam);
                    if (Interlocked.Increment(ref _pending) <= 1024)
                    {
                        try
                        {
                            _viewer.BeginInvoke(new Action(() =>
                            {
                                Interlocked.Decrement(ref _pending);
                                if (_disposed == 0) _receive(message, modifiers);
                            }));
                        }
                        catch { Interlocked.Decrement(ref _pending); Interlocked.Increment(ref _dropped); }
                    }
                    else { Interlocked.Decrement(ref _pending); Interlocked.Increment(ref _dropped); }
                }
            }
        }
        catch { Interlocked.Increment(ref _dropped); }
        // Do not alter, swallow, inject or replay the native message, including on observer failure.
        return CallNextHookEx(IntPtr.Zero, code, removed, data);
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
        _refresh.Stop();
        _refresh.Dispose();
        foreach (IntPtr hook in _hooks.Values) UnhookWindowsHookEx(hook);
        _hooks.Clear();
        GC.KeepAlive(_callback);
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct NativeMessage
    {
        public IntPtr Window;
        public uint Id;
        public IntPtr WParam, LParam;
        public uint Time;
        public Point Point;
        public uint Private;
    }
    private delegate IntPtr HookProc(int code, IntPtr wparam, IntPtr lparam);
    private delegate bool EnumProc(IntPtr hwnd, IntPtr lparam);
    [DllImport("user32.dll", SetLastError = true)] private static extern IntPtr SetWindowsHookExW(int kind, HookProc callback, IntPtr module, uint thread);
    [DllImport("user32.dll")] private static extern IntPtr CallNextHookEx(IntPtr hook, int code, IntPtr wparam, IntPtr lparam);
    [DllImport("user32.dll")] private static extern bool UnhookWindowsHookEx(IntPtr hook);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint process);
    [DllImport("user32.dll")] private static extern bool EnumChildWindows(IntPtr hwnd, EnumProc callback, IntPtr data);
    [DllImport("user32.dll")] private static extern bool IsChild(IntPtr parent, IntPtr child);
    [DllImport("user32.dll")] private static extern short GetKeyState(int key);
}
