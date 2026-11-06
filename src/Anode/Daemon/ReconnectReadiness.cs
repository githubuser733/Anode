using System.Diagnostics;
using Anode.Core.Session;

namespace Anode.Daemon;

internal static class ReconnectReadiness
{
    // The ActiveX Disconnected event precedes Windows session teardown. On the
    // affected Windows build, reconnecting then authenticates but fails with 258.
    // Three seconds passed the live repeated-reconnect comparison; no retry loop.
    internal static readonly TimeSpan SettleTime = TimeSpan.FromSeconds(3);

    internal static async Task WaitAsync(Func<Task<int>> viewerState, Func<bool> serverDetached, CancellationToken cancel)
    {
        var elapsed = Stopwatch.StartNew();
        TimeSpan? disconnectedSince = null;
        while (elapsed.Elapsed < TimeSpan.FromSeconds(10))
        {
            cancel.ThrowIfCancellationRequested();
            if (await viewerState() == 0)
            {
                disconnectedSince ??= elapsed.Elapsed;
                if (elapsed.Elapsed - disconnectedSince.Value >= SettleTime && serverDetached()) return;
            }
            else disconnectedSince = null;
            await Task.Delay(50, cancel);
        }
        throw new TimeoutException("Windows has not finished disconnecting the viewer. The existing seat was preserved; wait before pressing Reconnect again.");
    }

    internal static bool ServerDetached(uint? expectedSession)
    {
        if (expectedSession is null) return true;
        if (ChildSession.TryGetId() != expectedSession)
            throw new InvalidOperationException("The child session changed while disconnecting; reconnect was cancelled.");
        return ChildSession.IsDisconnected(expectedSession.Value);
    }
}
