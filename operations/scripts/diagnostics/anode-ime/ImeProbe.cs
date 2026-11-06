// Temporary, opt-in diagnostic. Logs state/lengths only, never text or key codes.
// IMM constants: microsoft/win32metadata WinSDK um/imm.h; WM values: win32con.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

sealed class ImeBox : TextBox
{
    [DllImport("imm32.dll")] static extern IntPtr ImmGetContext(IntPtr window);
    [DllImport("imm32.dll")] static extern bool ImmReleaseContext(IntPtr window, IntPtr context);
    [DllImport("imm32.dll", CharSet=CharSet.Unicode)] static extern int ImmGetCompositionStringW(IntPtr context, uint index, IntPtr buffer, uint length);
    public Action<string, int> Record;
    public bool Composing;
    public DateTime LastChange = DateTime.UtcNow;
    public int CompositionBytes()
    {
        IntPtr context = ImmGetContext(Handle);
        if (context == IntPtr.Zero) return -2;
        try { return ImmGetCompositionStringW(context, 0x0008, IntPtr.Zero, 0); }
        finally { ImmReleaseContext(Handle, context); }
    }
    protected override void WndProc(ref Message m)
    {
        string kind = null;
        int flags = 0;
        switch (m.Msg)
        {
            case 0x010D: Composing = true; kind = "composition_start"; break;
            case 0x010E: Composing = false; kind = "composition_end"; break;
            case 0x010F: kind = "composition_change"; flags = m.LParam.ToInt32(); break;
            case 0x0007: kind = "focus_gained"; break;
            case 0x0008: kind = "focus_lost"; break;
            case 0x0051: kind = "input_language_change"; break;
            case 0x0100: case 0x0104: kind = "key_down"; break;
            case 0x0201: kind = "mouse_down"; break;
            case 0x0282:
                int notice = m.WParam.ToInt32();
                if (notice == 4) kind = "candidate_close";
                if (notice == 5) kind = "candidate_open";
                break;
        }
        if (kind == "composition_change" || kind == "composition_start" || kind == "key_down") LastChange = DateTime.UtcNow;
        if (kind != null && Record != null) Record(kind, flags);
        base.WndProc(ref m);
    }
}

sealed class ProbeForm : Form
{
    readonly string directory;
    readonly StreamWriter log;
    readonly JavaScriptSerializer serializer = new JavaScriptSerializer();
    readonly Stopwatch clock = Stopwatch.StartNew();
    readonly ImeBox box = new ImeBox();
    readonly Label status = new Label();
    readonly Timer timer = new Timer();
    bool ready, started, finished;
    protected override bool ShowWithoutActivation { get { return true; } }

    public ProbeForm(string path)
    {
        directory = path;
        log = new StreamWriter(new FileStream(Path.Combine(path, "parent-events.jsonl"), FileMode.CreateNew, FileAccess.Write, FileShare.Read), new UTF8Encoding(false));
        log.AutoFlush = true;
        Text = "Anode 中文输入隔离测试";
        ClientSize = new Size(670, 230);
        StartPosition = FormStartPosition.CenterScreen;
        var instructions = new Label { Text = "点击下方输入框，用中文输入法键入 nihao，保留候选框。\n不要按空格、回车或切换窗口；停止输入 2 秒后自动测试。\n测试约 15 秒，只记录状态和长度。关闭窗口即可取消。", Location = new Point(18, 16), Size = new Size(630, 75) };
        box.Name = "ParentComposition";
        box.Location = new Point(18, 100);
        box.Width = 630;
        box.Font = new Font("Microsoft YaHei UI", 16);
        box.ImeMode = ImeMode.NoControl;
        box.Record = Write;
        status.Location = new Point(18, 158);
        status.Size = new Size(630, 55);
        status.Text = "等待你点击并保留一段未确认的拼音。";
        Controls.AddRange(new Control[] { instructions, box, status });
        timer.Interval = 100;
        timer.Tick += Tick;
        timer.Start();
        FormClosed += delegate { timer.Stop(); Write("closed", 0); log.Dispose(); };
        Write("created", Process.GetCurrentProcess().SessionId);
    }
    void Write(string kind, int flags)
    {
        log.WriteLine(serializer.Serialize(new { kind = kind, utc = DateTime.UtcNow.ToString("o"), ms = clock.ElapsedMilliseconds, flags = flags, composing = box.Composing, focused = box.Focused, compositionBytes = box.IsHandleCreated ? box.CompositionBytes() : -2 }));
    }
    void Tick(object sender, EventArgs e)
    {
        if (File.Exists(Path.Combine(directory, "close.flag"))) { Close(); return; }
        if (clock.Elapsed.TotalSeconds > 600) { Write("expired", 0); Close(); return; }
        if (!ready && box.Composing && box.Focused && box.CompositionBytes() > 0 && (DateTime.UtcNow - box.LastChange).TotalSeconds >= 2)
        {
            ready = true;
            status.Text = "检测到未确认拼音，正在准备子桌面输入，请保持不动。";
            Write("ready", 0);
        }
        if (!started && File.Exists(Path.Combine(directory, "start.flag")))
        {
            started = true;
            status.Text = "测试中，请保持不动，等待下方完成提示。";
            Write("probe_start", 0);
        }
        if (started && !finished)
        {
            Write("sample", 0);
            if (File.Exists(Path.Combine(directory, "finish.flag")))
            {
                finished = true;
                Write("probe_end", 0);
                status.Text = "测试采集完成，可以继续输入。结果已保存；可关闭此窗口。";
            }
        }
    }
    [STAThread]
    static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        if (args.Length == 1 && args[0] == "--selftest")
        {
            using (var test = new ImeBox()) { test.CreateControl(); Console.WriteLine("IMM_STATE_QUERY_OK bytes=" + test.CompositionBytes()); }
            return 0;
        }
        if (args.Length != 2 || Process.GetCurrentProcess().SessionId != int.Parse(args[1])) return 3;
        Application.Run(new ProbeForm(args[0]));
        return 0;
    }
}
