using System.Collections.Concurrent;
using Anode.Core.Bridge;

namespace Anode.Cli;

/// <summary>Actual native-open requests over private pipes; never starts a daemon or a seat.</summary>
internal static class NativeOpenChecks
{
    internal static int Run()
    {
        ConsoleBridge.Attach();
        try
        {
            Console.WriteLine(CliChecks.HelpRouting());
            Console.WriteLine(CliChecks.Arguments());
            Console.WriteLine(CliChecks.HelpText());
            Cli.CheckArguments("open", ["--width", "1920", "--height", "1080", "--remember-login", "--clipboard", "--state-dir", @"C:\Anode state"]);
            bool rejected = false;
            try { Cli.CheckArguments("open", ["--hidden"]); }
            catch (Exception) { rejected = true; }
            Require(rejected, "Native open unexpectedly accepted --hidden.");
            Task.Run(CheckRequestsAsync).GetAwaiter().GetResult();
            Console.WriteLine("NATIVE_OPEN_CHECKS_OK privatePipeScenarios=4 inputSent=false liveSeatStarted=false");
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }

    private static void Require(bool condition, string error)
    {
        if (!condition) throw new InvalidOperationException(error);
    }

    private static async Task CheckRequestsAsync()
    {
        Require(!await Cli.ShowOpenedViewerAsync(null, false), "An absent daemon was treated as running.");
        foreach (bool resumeStoppedSeat in new[] { false, true })
        {
            string pipe = "anode-native-open-check-" + Guid.NewGuid().ToString("N");
            var requests = new ConcurrentQueue<string>();
            using var server = new JsonPipeServer(pipe, request =>
            {
                requests.Enqueue(request.Str("op") ?? "missing");
                return Task.FromResult(JsonLine.Ok());
            });
            server.Start();
            using var client = await JsonPipeClient.TryConnectAsync(pipe, 3000) ?? throw new InvalidOperationException("Private pipe missing.");
            Require(await Cli.ShowOpenedViewerAsync(client, resumeStoppedSeat), "An existing viewer was not opened.");
            string[] expected = resumeStoppedSeat ? ["seat.show", "seat.start"] : ["seat.show"];
            Require(requests.SequenceEqual(expected), "Native open sent unexpected requests, or resumed a seat without permission.");
        }
        {
            string pipe = "anode-native-open-error-" + Guid.NewGuid().ToString("N");
            int calls = 0;
            using var server = new JsonPipeServer(pipe, _ =>
            {
                Interlocked.Increment(ref calls);
                return Task.FromResult(JsonLine.Fail("fixture show denied"));
            });
            server.Start();
            using var client = await JsonPipeClient.TryConnectAsync(pipe, 3000) ?? throw new InvalidOperationException("Private error pipe missing.");
            bool rejected = false;
            try { await Cli.ShowOpenedViewerAsync(client, true); }
            catch (InvalidOperationException error) when (error.Message == "fixture show denied") { rejected = true; }
            Require(rejected && calls == 1, "A rejected show request was ignored, retried, or followed by seat startup.");
        }
    }
}
