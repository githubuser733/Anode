using System.Diagnostics;
using System.Text;
using System.Text.Json.Nodes;
using Anode.Core.Bridge;
using Anode.Core.Util;

namespace Anode.Cli;

/// <summary>Opt-in parent-session tray notifications; never controls any application.</summary>
internal static class ChatNotifications
{
    internal static int Run(string[] args)
    {
        try
        {
            var options = new Args(args);
            string inbox = options.Value("inbox") ?? throw new ArgumentException("inbox is required.");
            string receipt = options.Value("receipts") ?? throw new ArgumentException("receipts is required.");
            int parent = options.Int("parent-session") ?? -1;
            int owner = options.Int("owner-pid") ?? -1;
            if (!Path.IsPathFullyQualified(inbox) || !Path.IsPathFullyQualified(receipt) || parent < 1 || owner < 1)
                throw new ArgumentException("Absolute paths, owner PID and parent session are required.");
            using var process = Process.GetCurrentProcess();
            if (process.SessionId != parent) throw new InvalidOperationException("Notification helper requires the parent session.");
            using var daemon = JsonPipeClient.TryConnectAsync(Env.ControlPipe, 5000).GetAwaiter().GetResult();
            var identity = daemon?.RequestAsync("seat.identity", timeoutMs: 5000).GetAwaiter().GetResult();
            if (identity?.Bool("ok") != true || identity.Obj("result")?.Int("parentSession") != parent
                || identity.Obj("result")?.Int("session") == parent)
                throw new InvalidOperationException("The daemon did not verify this notification session.");
            using var ownerProcess = Process.GetProcessById(owner);
            if (ownerProcess.SessionId != parent) throw new InvalidOperationException("Notification owner is outside the parent session.");
            using var input = new StreamReader(new FileStream(inbox, FileMode.Open, FileAccess.Read, FileShare.ReadWrite), Encoding.UTF8);
            using var output = new StreamWriter(new FileStream(receipt, FileMode.CreateNew, FileAccess.Write, FileShare.Read), new UTF8Encoding(false)) { AutoFlush = true };
            using var context = new ApplicationContext();
            using var icon = new NotifyIcon { Icon = SystemIcons.Information, Text = "Anode keyword monitor", Visible = true };
            using var timer = new System.Windows.Forms.Timer { Interval = 50 };
            var seen = new HashSet<string>(StringComparer.Ordinal);
            string pending = "", currentId = "";
            void Record(string id, string state, string? error = null) => output.WriteLine(new JsonObject
                { ["id"] = id, ["state"] = state, ["atUtc"] = DateTime.UtcNow.ToString("O"), ["error"] = error }.ToJsonString());
            icon.BalloonTipShown += (_, _) => Record(currentId, "shown");
            timer.Tick += (_, _) =>
            {
                if (File.Exists(inbox + ".stop") || ownerProcess.HasExited) { context.ExitThread(); return; }
                pending += input.ReadToEnd();
                int end;
                while ((end = pending.IndexOf('\n')) >= 0)
                {
                    string line = pending[..end]; pending = pending[(end + 1)..];
                    try
                    {
                        if (line.Length > 16384) throw new ArgumentException("Notification record is too large.");
                        var item = JsonNode.Parse(line)?.AsObject() ?? throw new ArgumentException("Invalid notification.");
                        string id = item.Str("id") ?? throw new ArgumentException("Notification id is required.");
                        if (!seen.Add(id)) continue;
                        currentId = id;
                        string Clean(string? text, int maximum) => new string((text ?? "").Where(c => !char.IsControl(c)).Take(maximum).ToArray());
                        icon.BalloonTipTitle = Clean(item.Str("title"), 60);
                        icon.BalloonTipText = Clean(item.Str("text"), 240);
                        if (icon.BalloonTipText.Length == 0) icon.BalloonTipText = "Keyword monitor event";
                        icon.BalloonTipIcon = ToolTipIcon.Info;
                        icon.ShowBalloonTip(5000);
                        Record(id, "requested");
                    }
                    catch (Exception ex) { Record(currentId, "failed", ex.Message); }
                }
                if (pending.Length > 16384) { Record("", "failed", "Incomplete oversized notification."); context.ExitThread(); }
            };
            timer.Start();
            Record("", "ready");
            Application.Run(context);
            icon.Visible = false;
            Record("", "stopped");
            return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex.Message); return 1; }
    }
}
