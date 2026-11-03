using System.Diagnostics;
using System.Text;
using System.Text.Json.Nodes;

namespace Anode.Cli;

/// <summary>Known message publication times inside an owned child-session window, never a real chat.</summary>
internal static class ChatMonitorFixture
{
    internal static int Run(string[] args)
    {
        try
        {
            var options = new Args(args);
            string path = options.Value("out") ?? throw new ArgumentException("out is required.");
            if (!Path.IsPathFullyQualified(path)) throw new ArgumentException("out must be absolute.");
            Seat.SeatHost.VerifyCurrentSession();
            using var output = new StreamWriter(new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read), new UTF8Encoding(false)) { AutoFlush = true };
            using var form = new Form { Text = "Anode keyword monitor fixture", Size = new Size(700, 450),
                StartPosition = FormStartPosition.Manual, Location = new Point(1050, 70), ShowInTaskbar = false };
            var list = new ListBox { AccessibleName = "消息", Dock = DockStyle.Fill, HorizontalScrollbar = true };
            list.Items.Add("fixture baseline"); form.Controls.Add(list);
            var watch = Stopwatch.StartNew();
            var lifetime = Stopwatch.StartNew();
            bool started = !options.Flag("wait-trigger");
            int stage = 0, ordinal = 0;
            void Add(string text)
            {
                list.Items.Add(text); list.TopIndex = Math.Max(0, list.Items.Count - 1);
                output.WriteLine(new JsonObject { ["ordinal"] = ++ordinal, ["text"] = text,
                    ["publishedAtUtc"] = DateTime.UtcNow.ToString("O"), ["elapsedMs"] = watch.ElapsedMilliseconds }.ToJsonString());
            }
            using var timer = new System.Windows.Forms.Timer { Interval = 50 };
            timer.Tick += (_, _) =>
            {
                if (lifetime.ElapsedMilliseconds >= 120000) { form.Close(); return; }
                if (!started)
                {
                    if (!File.Exists(path + ".begin")) return;
                    started = true; watch.Restart();
                }
                long elapsed = watch.ElapsedMilliseconds;
                if (stage == 0 && elapsed >= 10000) { Add("fixture normal 1"); stage++; }
                if (stage == 1 && elapsed >= 14000) { Add("收 fixture 2"); stage++; }
                if (stage == 2 && elapsed >= 18000) { Add("出 USDT fixture 3"); stage++; }
                if (stage == 3 && elapsed >= 22000) { for (int i = 0; i < 3; i++) Add("积分 fixture repeated"); stage++; }
                if (stage == 4 && elapsed >= 26000) { for (int i = 0; i < 20; i++) Add($"转 fixture burst {i:00}"); stage++; }
                if (stage == 5 && elapsed >= 32000) { Add(new string('长', 5000) + " USDT fixture long tail"); stage++; }
                if (stage == 6 && elapsed >= 36000) { Add("fixture normal last"); stage++; }
                if (elapsed >= 48000) form.Close();
            };
            timer.Start();
            Application.Run(form);
            return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex.Message); return 1; }
    }
}
