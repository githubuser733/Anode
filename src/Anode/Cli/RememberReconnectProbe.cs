using System.Text.Json.Nodes;
using Anode.Core.Bridge;
using Anode.Core.Session;
using Anode.Core.Util;
using Anode.Daemon;

namespace Anode.Cli;

// Opt-in diagnostic: reconnect only an existing, already detached child. No logoff,
// host launch, new account, password prompt or desktop input. Stops at first failure.
internal static class RememberReconnectProbe
{
    internal static int Run(string[] args)
    {
        var options = new Args(args);
        uint session = uint.Parse(options.Value("session") ?? throw new ArgumentException("--session required"));
        int cycles = int.Parse(options.Value("cycles") ?? "2");
        int delay = int.Parse(options.Value("disconnect-wait-ms") ?? "0");
        bool settled = options.Flag("settled");
        string output = Path.GetFullPath(options.Value("output") ?? throw new ArgumentException("--output required"));
        if (cycles is < 1 or > 3 || delay is < 0 or > 5000 || File.Exists(output)) throw new ArgumentException("Unsafe probe range or existing output.");
        Env.SetStateDirectory(Path.GetDirectoryName(output)!);
        if (Env.Channel != "main" || ChildSession.TryGetId() != session || session == ChildSession.CurrentSessionId())
            throw new InvalidOperationException("Expected existing child session is absent.");
        using (var pipe = JsonPipeClient.TryConnectAsync(Env.ControlPipe, 2000).GetAwaiter().GetResult()
            ?? throw new InvalidOperationException("Existing daemon required."))
        {
            var status = pipe.RequestAsync("status").GetAwaiter().GetResult().Obj("result");
            if (status is null || status.Int("viewerConnection") != 0 || status.Bool("agentReady") == true || status.Int("session") != (int)session)
                throw new InvalidOperationException("Only a detached existing daemon can be probed.");
        }
        var rows = new JsonArray();
        int exitCode = 1;
        Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
        Application.EnableVisualStyles();
        using var form = new Form { ShowInTaskbar = false, Text = "Anode existing-seat reconnect diagnostic" };
        using var viewer = new RdpViewer(new RememberedLogin(prompt: _ => throw new InvalidOperationException("Probe refuses credential prompts.")));
        form.Controls.Add(viewer);
        _ = form.Handle;
        viewer.CreateHiddenHandle();
        form.BeginInvoke(new Action(async () =>
        {
            try
            {
                for (int cycle = 1; cycle <= cycles; cycle++)
                {
                    if (ChildSession.TryGetId() != session) throw new InvalidOperationException("Child identity changed; refusing connection.");
                    var row = new JsonObject { ["cycle"] = cycle, ["startUtc"] = DateTime.UtcNow.ToString("O"), ["session"] = session };
                    rows.Add(row);
                    var done = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
                    Action complete = () => done.TrySetResult("ready");
                    Action<int> disconnected = reason => done.TrySetResult(viewer.DescribeDisconnect(reason) ?? $"disconnect {reason}");
                    Action<int> logon = code => row["logonCode"] = code;
                    viewer.LoginComplete += complete; viewer.Disconnected += disconnected; viewer.LogonError += logon;
                    try
                    {
                        viewer.ConnectToChildSession(new SeatOptions { Width=1920, Height=1080, RememberWindowsLogin=true, ShareClipboard=true });
                        string outcome = await done.Task.WaitAsync(TimeSpan.FromSeconds(20));
                        row["outcome"] = outcome;
                        row["endUtc"] = DateTime.UtcNow.ToString("O");
                        row["credential"] = viewer.RememberedLoginStatus;
                        if (outcome != "ready") throw new InvalidOperationException("Probe stopped after connection failure.");
                        if (ChildSession.TryGetId() != session) throw new InvalidOperationException("Unexpected child identity.");
                        await Task.Delay(1500);
                    }
                    finally { viewer.LoginComplete -= complete; viewer.Disconnected -= disconnected; viewer.LogonError -= logon; }
                    viewer.Disconnect();
                    if (settled)
                        await ReconnectReadiness.WaitAsync(() => Task.FromResult(viewer.ConnectionState),
                            () => ReconnectReadiness.ServerDetached(session), CancellationToken.None);
                    else
                    {
                        var deadline = DateTime.UtcNow.AddSeconds(10);
                        while (viewer.ConnectionState != 0 && DateTime.UtcNow < deadline) await Task.Delay(50);
                        if (viewer.ConnectionState != 0) throw new TimeoutException("Probe viewer did not disconnect.");
                    }
                    if (delay > 0) await Task.Delay(delay);
                }
                exitCode = 0;
            }
            catch (Exception error) { rows.Add(new JsonObject { ["error"] = error.Message, ["type"] = error.GetType().Name }); }
            finally
            {
                viewer.Disconnect();
                var report = new JsonObject { ["result"] = exitCode == 0 ? "RECONNECT_PROBE_PASS" : "RECONNECT_PROBE_FAIL",
                    ["delayMs"] = delay, ["productionReadinessGate"] = settled, ["cycles"] = cycles, ["sessionBefore"] = session,
                    ["sessionAfter"] = ChildSession.TryGetId(), ["logoffCalls"] = 0, ["inputCalls"] = 0, ["events"] = rows };
                using var file = new FileStream(output, FileMode.CreateNew);
                using var writer = new StreamWriter(file);
                writer.Write(report.ToJsonString(new() { WriteIndented=true }));
                Application.ExitThread();
            }
        }));
        Application.Run();
        return exitCode;
    }
}
