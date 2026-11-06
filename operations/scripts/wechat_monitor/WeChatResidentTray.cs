using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// Native GUI subsystem entry. All child processes explicitly use CREATE_NO_WINDOW.
static class ResidentHost
{
    internal static string Root, Python, State, Logs, MutexName;
    internal const string WorkerTask = "AgentWindowsOperation-WeChatResident";
    internal const string GuardTask = "AgentWindowsOperation-WeChatResident-Watchdog";
    internal static Dictionary<string, object> Settings;

    internal static Dictionary<string, object> Json(string value)
    {
        return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(value);
    }
    internal static string Encode(object value) { return new JavaScriptSerializer().Serialize(value); }
    internal static bool Flag(Dictionary<string, object> row, string key)
    {
        return row != null && row.ContainsKey(key) && row[key] is bool && (bool)row[key];
    }
    internal static long Number(Dictionary<string, object> row, string key)
    {
        return row != null && row.ContainsKey(key) ? Convert.ToInt64(row[key]) : 0;
    }
    internal static string Value(Dictionary<string, object> row, string key)
    {
        return row != null && row.ContainsKey(key) ? Convert.ToString(row[key]) : "";
    }
    internal static long Now { get { return DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(); } }
    internal static Dictionary<string, object> Tail(string path)
    {
        if (!File.Exists(path)) return new Dictionary<string, object>();
        using (var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        {
            file.Seek(Math.Max(0, file.Length - 65536), SeekOrigin.Begin);
            using (var reader = new StreamReader(file, Encoding.UTF8))
            {
                var rows = reader.ReadToEnd().Split('\n');
                for (int i = rows.Length - 1; i >= 0; --i)
                {
                    try { if (rows[i].Trim().Length > 0) return Json(rows[i]); }
                    catch (ArgumentException) { }
                    catch (InvalidOperationException) { }
                }
            }
        }
        return new Dictionary<string, object>();
    }
    internal static void Append(string path, object row)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        // Cross-process append is serialized; no configuration or history is overwritten.
        using (var mutex = new Mutex(false, MutexName + ".journal"))
        {
            bool acquired = false;
            try
            {
                try { acquired = mutex.WaitOne(5000); } catch (AbandonedMutexException) { acquired = true; }
                if (!acquired) throw new IOException("TRAY_JOURNAL_BUSY");
                using (var file = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.Read))
                using (var writer = new StreamWriter(file, new UTF8Encoding(false)))
                { writer.WriteLine(Encode(row)); writer.Flush(); file.Flush(true); }
            }
            finally { if (acquired) mutex.ReleaseMutex(); }
        }
    }
    internal static void Event(string kind, object detail)
    {
        Append(Path.Combine(Logs, "events.jsonl"), new { at_ms = Now, kind = kind,
            pid = Process.GetCurrentProcess().Id, detail = detail });
    }
    internal static bool Enabled { get { return Flag(Tail(Path.Combine(State, "control.jsonl")), "enabled"); } }
    internal static void SetEnabled(bool value, bool exit)
    {
        Append(Path.Combine(State, "control.jsonl"), new { at_ms = Now, enabled = value,
            exitTray = exit, reason = "explicit tray control" });
    }
    internal static bool WorkerPresent()
    {
        string path = Path.Combine(State, "owner", "dispatch.lock");
        if (!File.Exists(path)) return false;
        using (var file = new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite))
        {
            try { file.Lock(0, 1); }
            catch (IOException) { return true; }
            file.Unlock(0, 1);
            return false;
        }
    }
    internal static bool TrayPresent()
    {
        using (var mutex = new Mutex(false, MutexName))
        {
            bool acquired = false;
            try
            {
                try { acquired = mutex.WaitOne(0); } catch (AbandonedMutexException) { acquired = true; }
                return !acquired;
            }
            finally { if (acquired) mutex.ReleaseMutex(); }
        }
    }
    internal static ProcessStartInfo Hidden(string path, string arguments)
    {
        return new ProcessStartInfo(path, arguments) { WorkingDirectory = Root, UseShellExecute = false,
            CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardOutput = true, RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
    }
    internal static string Run(string path, string arguments, int timeout)
    {
        using (var process = Process.Start(Hidden(path, arguments)))
        {
            var output = process.StandardOutput.ReadToEndAsync();
            var errors = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(timeout)) throw new TimeoutException("TRAY_CHILD_TIMEOUT");
            if (process.ExitCode != 0) throw new InvalidOperationException("TRAY_CHILD_FAILED_" + process.ExitCode);
            return output.GetAwaiter().GetResult();
        }
    }
    internal static Dictionary<string, object> Anode()
    {
        var config = Json(File.ReadAllText(Path.Combine(Root, "config", "anode-local-mcp.json"), Encoding.UTF8));
        string exe = Path.GetFullPath(Path.Combine(Root, Value(config, "executable")));
        if (!exe.StartsWith(Path.Combine(Root, "work") + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("ANODE_PATH_OUTSIDE_WORK");
        using (var sha = SHA256.Create())
        using (var file = File.OpenRead(exe))
        {
            string hash = BitConverter.ToString(sha.ComputeHash(file)).Replace("-", "").ToLowerInvariant();
            if (hash != Value(config, "sha256")) throw new InvalidOperationException("ANODE_HASH_CHANGED");
        }
        string channel = Value(config, "channel");
        if (!System.Text.RegularExpressions.Regex.IsMatch(channel, "^[a-z0-9][a-z0-9-]{0,31}$"))
            throw new InvalidOperationException("ANODE_CHANNEL_INVALID");
        return Json(Run(exe, "--channel " + channel + " status --json", 20000));
    }
    internal static dynamic Scheduler()
    {
        dynamic service = Activator.CreateInstance(Type.GetTypeFromProgID("Schedule.Service"));
        service.Connect();
        return service;
    }
    internal static void GuardEnabled(bool enabled)
    {
        dynamic service = Scheduler();
        service.GetFolder("\\").GetTask(GuardTask).Enabled = enabled;
    }
    internal static void Ensure()
    {
        if (!Enabled || TrayPresent()) return;
        var state = Anode();
        if (Value(state, "state") != "ready") { Event("waiting_for_existing_anode", null); return; }
        int parent = (int)Number(state, "parentSession");
        if (parent < 1) throw new InvalidOperationException("ANODE_PARENT_INVALID");
        dynamic service = Scheduler();
        service.GetFolder("\\").GetTask(WorkerTask).RunEx(null, 4, parent, null);
    }
    internal static Process StartWorker()
    {
        var state = Anode();
        if (Value(state, "state") != "ready" || Number(state, "parentSession") != Process.GetCurrentProcess().SessionId)
            throw new InvalidOperationException("WAITING_FOR_EXISTING_ANODE_PARENT");
        string run = Path.Combine(Root, "artifacts", "wechat-resident", "supervisor", "tray-" + Guid.NewGuid().ToString("N").Substring(0, 12));
        Directory.CreateDirectory(run);
        var output = new StreamWriter(new FileStream(Path.Combine(run, "stdout.log"), FileMode.CreateNew), new UTF8Encoding(false));
        var error = new StreamWriter(new FileStream(Path.Combine(run, "stderr.log"), FileMode.CreateNew), new UTF8Encoding(false));
        var child = new Process { StartInfo = Hidden(Python, "-X utf8 -B \"" + Path.Combine(Root, "scripts", "wechat_monitor", "resident.py") + "\" serve"), EnableRaisingEvents = true };
        child.OutputDataReceived += delegate(object sender, DataReceivedEventArgs args) { lock (output) { if (args.Data != null) { output.WriteLine(args.Data); output.Flush(); } } };
        child.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs args) { lock (error) { if (args.Data != null) { error.WriteLine(args.Data); error.Flush(); } } };
        child.Start();
        child.BeginOutputReadLine(); child.BeginErrorReadLine();
        Task.Run(delegate { child.WaitForExit(); lock (output) { output.Dispose(); } lock (error) { error.Dispose(); } });
        Event("worker_started", new { workerPid = child.Id, session = child.SessionId, output = run });
        return child;
    }
    internal static string Details()
    {
        var health = Tail(Path.Combine(State, "health.jsonl"));
        var config = Json(File.ReadAllText(Path.Combine(Root, "config", "wechat-monitor.json"), Encoding.UTF8));
        return "微信群关键词提醒\r\n\r\n" +
            "监控：" + (Enabled ? "已启用" : "已暂停") + "\r\n" +
            "进程：" + (WorkerPresent() ? "运行中" : "未运行") + "\r\n" +
            "最近状态：" + Value(health, "state") + "\r\n" +
            "检查间隔：" + Value(config, "pollSeconds") + " 秒；首次命中立即提醒\r\n" +
            "新增 " + Number(health, "newMessages") + " 条，命中 " + Number(health, "keywordHits") + " 条\r\n" +
            "待提醒 " + Number(health, "pendingHits") + " 条，结果不确定 " + Number(health, "notificationsUncertain") + " 次\r\n\r\n" +
            "群：星创票务 收分群、✈️方舟航空✈️壹\r\n关键词：收／UA\r\n\r\n" +
            "只读监控；通知显示在本机，不自动发送微信。\r\n" +
            "报告：" + Path.Combine(Root, "artifacts", "wechat-resident", "reports");
    }
    internal static object Inspect()
    {
        return new { enabled = Enabled, workerRunning = WorkerPresent(), trayRunning = TrayPresent(),
            health = Tail(Path.Combine(State, "health.jsonl")), tray = Tail(Path.Combine(Logs, "state.jsonl")),
            currentSession = Process.GetCurrentProcess().SessionId, guiSubsystem = true };
    }
    internal static void SaveResult(string path, object value)
    {
        using (var file = new FileStream(path, FileMode.CreateNew))
        using (var writer = new StreamWriter(file, new UTF8Encoding(false))) writer.Write(Encode(value));
    }
    [STAThread]
    static int Main(string[] args)
    {
        string action = "--tray", resultPath = null;
        try
        {
            for (int i = 0; i < args.Length; ++i)
            {
                if (args[i] == "--root") Root = Path.GetFullPath(args[++i]).TrimEnd(Path.DirectorySeparatorChar);
                else if (args[i] == "--result") resultPath = Path.GetFullPath(args[++i]);
                else if (Array.IndexOf(new[] { "--tray", "--ensure", "--resume", "--pause", "--exit", "--inspect", "--test-notification" }, args[i]) >= 0) action = args[i];
                else throw new ArgumentException("UNKNOWN_TRAY_OPTION");
            }
            if (Root == null || Root.Contains("\"")) throw new ArgumentException("PROJECT_ROOT_REQUIRED");
            Settings = Json(File.ReadAllText(Path.Combine(Root, "config", "wechat-resident-tray.json"), Encoding.UTF8));
            Python = Value(Settings, "pythonExecutable");
            if (!File.Exists(Python)) throw new FileNotFoundException("PYTHON_UNAVAILABLE");
            State = Path.Combine(Root, "artifacts", "wechat-resident", "state");
            Logs = Path.Combine(Root, "artifacts", "wechat-resident", "tray");
            using (var sha = SHA256.Create()) MutexName = "Global\\AgentWindowsOperation.WeChatTray." + BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Root.ToLowerInvariant()))).Replace("-", "").Substring(0, 24);
            if (action == "--ensure") Ensure();
            else if (action == "--resume") { SetEnabled(true, false); GuardEnabled(true); Ensure(); }
            else if (action == "--pause" || action == "--exit") { SetEnabled(false, action == "--exit"); GuardEnabled(false); }
            else if (action == "--test-notification")
            {
                var test = Json(Run(Python, "-X utf8 -B \"" + Path.Combine(Root, "scripts", "wechat_monitor", "resident.py") + "\" test-notification", 45000));
                if (resultPath != null) SaveResult(resultPath, test);
                return Value(test, "state") == "shown" ? 0 : 2;
            }
            else if (action == "--inspect") { if (resultPath == null) throw new ArgumentException("RESULT_PATH_REQUIRED"); SaveResult(resultPath, Inspect()); }
            else
            {
                using (var mutex = new Mutex(false, MutexName))
                {
                    bool owner = false;
                    try
                    {
                        try { owner = mutex.WaitOne(0); } catch (AbandonedMutexException) { owner = true; }
                        if (!owner) return 0;
                        var state = Anode();
                        if (Value(state, "state") != "ready" || Number(state, "parentSession") != Process.GetCurrentProcess().SessionId)
                            throw new InvalidOperationException("TRAY_REQUIRES_VERIFIED_PARENT_SESSION");
                        Application.EnableVisualStyles();
                        Application.SetCompatibleTextRenderingDefault(false);
                        Application.Run(new MonitorTray());
                    }
                    finally { if (owner) mutex.ReleaseMutex(); }
                }
            }
            return 0;
        }
        catch (Exception error)
        {
            if (Logs != null && MutexName != null) Event("controller_error", new { action = action, error = error.GetType().Name, message = error.Message });
            if (resultPath != null && !File.Exists(resultPath)) SaveResult(resultPath, new { error = error.GetType().Name, message = error.Message });
            return 1;
        }
    }
}

sealed class MonitorTray : ApplicationContext
{
    readonly NotifyIcon icon;
    readonly System.Windows.Forms.Timer timer;
    readonly ToolStripMenuItem stateItem, toggle;
    readonly long born = ResidentHost.Now;
    Process worker;
    bool busy;
    long lastStart, lastRecord;
    string lastState = "", failure = "";
    Form details;
    TextBox detailsText;

    internal MonitorTray()
    {
        var menu = new ContextMenuStrip();
        stateItem = new ToolStripMenuItem("正在检查监控状态") { Enabled = false };
        toggle = new ToolStripMenuItem("暂停监控", null, delegate { ChangeEnabled(!ResidentHost.Enabled); });
        menu.Items.Add(stateItem); menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("查看状态", null, delegate { ShowDetails(); });
        menu.Items.Add(toggle);
        menu.Items.Add("打开报告目录", null, delegate { OpenFolder("reports"); });
        menu.Items.Add("打开日志目录", null, delegate { OpenFolder("runs"); });
        menu.Items.Add("测试本机提醒", null, delegate { TestNotice(); });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("退出并停止监控", null, delegate { ChangeEnabled(false); ExitThread(); });
        icon = new NotifyIcon { Icon = SystemIcons.Information, Text = "微信群关键词提醒", ContextMenuStrip = menu, Visible = true };
        icon.DoubleClick += delegate { ShowDetails(); };
        timer = new System.Windows.Forms.Timer { Interval = 1000 };
        timer.Tick += delegate { Tick(); };
        timer.Start();
        ResidentHost.Event("tray_started", new { session = Process.GetCurrentProcess().SessionId, consoleWindow = false });
        Tick();
    }
    void ChangeEnabled(bool value)
    {
        ResidentHost.SetEnabled(value, false);
        try { ResidentHost.GuardEnabled(value); failure = ""; }
        catch (Exception error) { failure = error.GetType().Name; ResidentHost.Event("guard_control_error", new { error = failure }); }
        Tick();
    }
    void OpenFolder(string folder)
    {
        string path = Path.Combine(ResidentHost.Root, "artifacts", "wechat-resident", folder);
        Directory.CreateDirectory(path);
        Process.Start(new ProcessStartInfo("explorer.exe", "\"" + path + "\"") { UseShellExecute = true });
    }
    void ShowDetails()
    {
        if (details == null || details.IsDisposed)
        {
            details = new Form { Text = "微信群关键词提醒 · 状态", Width = 620, Height = 395, StartPosition = FormStartPosition.CenterScreen };
            detailsText = new TextBox { Dock = DockStyle.Fill, Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical,
                Font = new Font("Microsoft YaHei UI", 10), BackColor = SystemColors.Window, BorderStyle = BorderStyle.None };
            details.Controls.Add(detailsText);
        }
        detailsText.Text = ResidentHost.Details();
        details.Show(); details.Activate();
    }
    async void TestNotice()
    {
        if (busy) return;
        busy = true;
        try
        {
            var result = await Task.Run(delegate { return ResidentHost.Run(ResidentHost.Python,
                "-X utf8 -B \"" + Path.Combine(ResidentHost.Root, "scripts", "wechat_monitor", "resident.py") + "\" test-notification", 45000); });
            ResidentHost.Event("notification_test", ResidentHost.Json(result));
        }
        catch (Exception error) { failure = error.GetType().Name; ResidentHost.Event("notification_test_error", new { error = failure }); }
        finally { busy = false; }
    }
    async void Tick()
    {
        try
        {
            var control = ResidentHost.Tail(Path.Combine(ResidentHost.State, "control.jsonl"));
            if (ResidentHost.Flag(control, "exitTray") && ResidentHost.Number(control, "at_ms") >= born) { ExitThread(); return; }
            bool enabled = ResidentHost.Flag(control, "enabled"), running = ResidentHost.WorkerPresent();
            var health = ResidentHost.Tail(Path.Combine(ResidentHost.State, "health.jsonl"));
            bool healthy = running && ResidentHost.Value(health, "state") == "healthy" && ResidentHost.Now - ResidentHost.Number(health, "at_ms") < 120000;
            string state = !enabled ? "已暂停" : healthy ? "正在监控" : running ? "正在恢复连接" : "等待恢复";
            if (failure.Length > 0 && enabled && !healthy) state += " · " + failure;
            stateItem.Text = state;
            toggle.Text = enabled ? "暂停监控" : "恢复监控";
            icon.Text = ("微信群关键词提醒 · " + state).Substring(0, Math.Min(63, ("微信群关键词提醒 · " + state).Length));
            icon.Icon = !enabled ? SystemIcons.Application : healthy ? SystemIcons.Information : SystemIcons.Warning;
            if (details != null && !details.IsDisposed && details.Visible) detailsText.Text = ResidentHost.Details();
            if (state != lastState || ResidentHost.Now - lastRecord >= 60000)
            {
                lastState = state; lastRecord = ResidentHost.Now;
                ResidentHost.Append(Path.Combine(ResidentHost.Logs, "state.jsonl"), new { at_ms = ResidentHost.Now,
                    pid = Process.GetCurrentProcess().Id, session = Process.GetCurrentProcess().SessionId,
                    iconVisible = icon.Visible, state = state, enabled = enabled, workerRunning = running, healthy = healthy,
                    menuItems = new[] { "查看状态", toggle.Text, "打开报告目录", "打开日志目录", "测试本机提醒", "退出并停止监控" } });
            }
            if (enabled && !running && !busy && ResidentHost.Now - lastStart >= 10000)
            {
                lastStart = ResidentHost.Now; busy = true;
                try { worker = await Task.Run(delegate { return ResidentHost.StartWorker(); }); failure = ""; }
                catch (Exception error) { failure = error.GetType().Name; ResidentHost.Event("worker_start_error", new { error = failure, message = error.Message }); }
                finally { busy = false; }
            }
        }
        catch (Exception error) { failure = error.GetType().Name; ResidentHost.Event("tray_tick_error", new { error = failure }); }
    }
    protected override void ExitThreadCore()
    {
        timer.Stop(); timer.Dispose(); icon.Visible = false; icon.Dispose();
        if (details != null) details.Dispose();
        ResidentHost.Event("tray_exited", new { monitoringEnabled = ResidentHost.Enabled });
        base.ExitThreadCore();
    }
}
