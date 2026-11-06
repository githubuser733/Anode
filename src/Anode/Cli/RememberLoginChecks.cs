using System.Runtime.InteropServices;
using Anode.Daemon;

namespace Anode.Cli;

/// <summary>No connections, real credential access, user accounts or file cleanup.</summary>
internal static class RememberLoginChecks
{
    internal static int Run()
    {
        try
        {
            ConsoleBridge.Attach();
            Console.WriteLine(DiagnosticsChecks.RdpSettings());
            Console.WriteLine(NativeSettings());
            Console.WriteLine(ReconnectPolicy());
            Console.WriteLine(DaemonLifecycle().GetAwaiter().GetResult());
            Console.WriteLine(DiagnosticsChecks.ViewerSignIn().GetAwaiter().GetResult());
            Console.WriteLine(Arguments());
            Console.WriteLine(SavedLoginWorkflow());
            Console.WriteLine(ConnectionFailureIsNotBadPassword());
            // Earlier COM checks install a WinForms context; this no-UI check
            // must not synchronously block that context while awaiting a timer.
            Console.WriteLine(Task.Run(ReconnectReadinessCheck).GetAwaiter().GetResult());
            Console.WriteLine(NativeCredentialRoundTrip());
            Console.WriteLine("REMEMBER_LOGIN_LOCAL_CHECKS_OK realCredentialReads=0 realCredentialWrites=0 syntheticSessionCredentialWrites=1 connections=0");
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    private sealed class MemoryVault : ILoginVault, IDisposable
    {
        internal LoginSecret? Saved;
        internal int Reads, Writes;
        public LoginSecret? ReadLatest() { Reads++; return Saved?.Copy(); }
        public void Append(LoginSecret value) { Writes++; Saved?.Dispose(); Saved = value.Copy(); }
        public void Dispose() => Saved?.Dispose();
    }

    private static unsafe string NativeCredentialRoundTrip()
    {
        using var fixture = Fixture();
        var store = new WindowsLoginVault("Anode/Diagnostics/remember-login/" + Guid.NewGuid().ToString("N") + "/", persistence: 1);
        Require(store.ReadLatest() is null, "Unique synthetic test namespace was not empty.");
        store.Append(fixture);
        using var read = store.ReadLatest();
        Require(read is not null && read.UserName == fixture.UserName && read.ByteLength == fixture.ByteLength
            && new ReadOnlySpan<byte>((void*)read.Password, read.ByteLength).SequenceEqual(new ReadOnlySpan<byte>((void*)fixture.Password, fixture.ByteLength)),
            "Windows Credential Manager round-trip differs.");
        uint size = 0;
        CredPackAuthenticationBufferW(0, fixture.UserName, fixture.Password, IntPtr.Zero, ref size);
        Require(Marshal.GetLastWin32Error() == 122 && size > 0, "Windows packing size query failed.");
        IntPtr packed = Marshal.AllocHGlobal((int)size);
        try
        {
            Require(CredPackAuthenticationBufferW(0, fixture.UserName, fixture.Password, packed, ref size), "Windows packing failed.");
            using var unpacked = WindowsLoginPrompt.Unpack(packed, size);
            Require(unpacked.UserName == fixture.UserName && unpacked.ByteLength == fixture.ByteLength
                && new ReadOnlySpan<byte>((void*)unpacked.Password, unpacked.ByteLength).SequenceEqual(new ReadOnlySpan<byte>((void*)fixture.Password, fixture.ByteLength)),
                "Native credential unpacking differs.");
        }
        finally
        {
            System.Security.Cryptography.CryptographicOperations.ZeroMemory(new Span<byte>((void*)packed, (int)size));
            Marshal.FreeHGlobal(packed);
        }
        return "Windows Credential Manager + pack/unpack: synthetic round-trip OK; isolated session-only test entry retained until Windows logoff; no real credentials read.";
    }

    [DllImport("credui.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] private static extern bool CredPackAuthenticationBufferW(uint flags,
        string user, IntPtr password, IntPtr packed, ref uint size);

    private static LoginSecret Fixture()
    {
        // Synthetic fixture only; never used for a Windows connection.
        var p = Marshal.StringToBSTR("anode-test-fixture-not-an-account-password");
        try { return new LoginSecret("TESTDOMAIN\\test-user", p, Marshal.ReadInt32(p, -4)); }
        finally { Marshal.ZeroFreeBSTR(p); }
    }

    private static string SavedLoginWorkflow()
    {
        var type = Type.GetTypeFromCLSID(new Guid("A0C63C30-F08D-4AB4-907C-34905D770C7D"), true)!;
        object control = Activator.CreateInstance(type)!;
        using var vault = new MemoryVault();
        int prompts = 0;
        try
        {
            using (var viewer = new RdpViewer(new RememberedLogin(vault, _ => { prompts++; return new(Fixture(), true); })))
            {
                viewer.ConfigureLogin(control, false, true);
                Require(vault.Writes == 0, "Checkbox saved credentials before login succeeded.");
                var events = new RdpViewer.EventSink(viewer);
                events.OnConnected();
                Require(vault.Writes == 0, "Transport connection saved credentials before authentication.");
                events.OnLoginComplete();
                Require(vault.Writes == 1, "Successful remembered login did not persist credentials.");
                events.OnLoginComplete();
                Require(vault.Writes == 1, "Repeated login event saved twice.");
            }
            // A fresh viewer simulates cold startup rather than reusing the COM object's memory.
            using (var viewer = new RdpViewer(new RememberedLogin(vault, _ => { prompts++; throw new Exception("Unexpected prompt"); })))
            {
                viewer.ConfigureLogin(control, false, true);
                Require(prompts == 1 && (string?)Dispatch.Get(control, "Domain") == "TESTDOMAIN"
                    && (string?)Dispatch.Get(control, "UserName") == "test-user", "Cold login did not reuse saved identity.");
                var prompt = (IMsRdpCredentialPrompt)control;
                Require(!prompt.GetPromptForCredentials() && !prompt.GetAllowPromptingForCredentials(), "Cached login opened another RDP prompt.");
                new RdpViewer.EventSink(viewer).OnLogonError(0);
                bool refused = false;
                try { viewer.ConfigureLogin(control, false, true); } catch (InvalidOperationException) { refused = true; }
                Require(refused && prompts == 1, "Rejected password was automatically retried.");
            }
            using var oneTimeVault = new MemoryVault();
            using var oneTime = new RdpViewer(new RememberedLogin(oneTimeVault, _ => new(Fixture(), false)));
            oneTime.ConfigureLogin(control, true, true);
            new RdpViewer.EventSink(oneTime).OnLoginComplete();
            Require(oneTimeVault.Writes == 0, "Unchecked saving still wrote a credential.");
            using var cancelled = new RdpViewer(new RememberedLogin(oneTimeVault, _ => null));
            bool cancellation = false;
            try { cancelled.ConfigureLogin(control, true, true); } catch (OperationCanceledException) { cancellation = true; }
            Require(cancellation && oneTimeVault.Writes == 0, "Cancel saved a credential.");
            using var failed = new RdpViewer(new RememberedLogin(oneTimeVault, _ => new(Fixture(), true)));
            failed.ConfigureLogin(control, true, true);
            new RdpViewer.EventSink(failed).OnLogonError(0);
            new RdpViewer.EventSink(failed).OnLoginComplete();
            Require(oneTimeVault.Writes == 0, "Rejected password was saved by a later event.");
            failed.ConfigureLogin(control, true, true);
            new RdpViewer.EventSink(failed).OnLoginComplete();
            Require(oneTimeVault.Writes == 1, "Explicit Sign in could not repair a failed cached login.");
            return "Remembered login: real COM password setter; success-only save, cold reuse, cancellation, unchecked save, retry refusal; synthetic secrets only.";
        }
        finally { if (Marshal.IsComObject(control)) Marshal.FinalReleaseComObject(control); }
    }

    private static string NativeSettings()
    {
        var type = Type.GetTypeFromCLSID(new Guid("A0C63C30-F08D-4AB4-907C-34905D770C7D"), true)!;
        object control = Activator.CreateInstance(type)!;
        try
        {
            var prompt = (IMsRdpCredentialPrompt)control;
            foreach (bool remember in new[] { false, true })
            foreach (bool force in new[] { false, true })
            {
                RdpViewer.ConfigureCredentialPrompt(prompt, force, remember);
                Require(prompt.GetAllowCredentialSaving() == remember, "Native saving differs from explicit opt-in.");
                Require(prompt.GetPromptForCredentials() == force, "Saved-login mode forced an extra credential prompt.");
                Require(prompt.GetAllowPromptingForCredentials() == (force || remember), "Native prompt availability differs.");
                Require(prompt.GetPromptForCredsOnClient() == force, "Ordinary saved-login must not force the client credential dialog.");
            }
            RdpViewer.ConfigureCredentialPrompt(prompt, false);
            Require(!prompt.GetAllowCredentialSaving() && !prompt.GetAllowPromptingForCredentials(), "Default policy was not restored.");
            return "Native COM: four force/remember combinations round-trip; defaults restored; no connection.";
        }
        finally { if (Marshal.IsComObject(control)) Marshal.FinalReleaseComObject(control); }
    }

    private static string ConnectionFailureIsNotBadPassword()
    {
        var type = Type.GetTypeFromCLSID(new Guid("A0C63C30-F08D-4AB4-907C-34905D770C7D"), true)!;
        object control = Activator.CreateInstance(type)!;
        using var vault = new MemoryVault();
        using var fixture = Fixture();
        vault.Append(fixture);
        try
        {
            using var viewer = new RdpViewer(new RememberedLogin(vault, _ => throw new Exception("Unexpected password prompt")));
            viewer.ConfigureLogin(control, false, true);
            var events = new RdpViewer.EventSink(viewer);
            // Captured live sequence: Windows authenticated, then licensing/session
            // teardown failed (-1 followed by extended disconnect 258).
            events.OnLogonError(-1);
            events.OnDisconnected(3);
            Require(!viewer.RememberedLoginRejected, "Connection failure -1 was mislabeled as a rejected saved password.");
            viewer.ConfigureLogin(control, false, true);
            events.OnLoginComplete();
            Require(viewer.RememberedLoginStatus["state"]!.GetValue<string>() == "reused", "Explicit reconnect could not reuse a valid saved credential.");
            events.OnDisconnected(1);
            viewer.ConfigureLogin(control, false, true);
            events.OnLogonError(0);
            events.OnDisconnected(3);
            bool blocked = false;
            try { viewer.ConfigureLogin(control, false, true); } catch (InvalidOperationException) { blocked = true; }
            Require(blocked, "A genuinely rejected password became retryable after disconnection.");
            return "Failure classification: captured -1/disconnect allows explicit reuse; code 0 remains blocked; no connection or real secret.";
        }
        finally { if (Marshal.IsComObject(control)) Marshal.FinalReleaseComObject(control); }
    }

    private static async Task<string> ReconnectReadinessCheck()
    {
        var elapsed = System.Diagnostics.Stopwatch.StartNew();
        int serverChecks = 0;
        await ReconnectReadiness.WaitAsync(() => Task.FromResult(0), () => { serverChecks++; return true; }, CancellationToken.None);
        Require(elapsed.Elapsed >= ReconnectReadiness.SettleTime && serverChecks > 0,
            "Reconnect bypassed server-side disconnection or the observed teardown settling interval.");
        using var stopped = new CancellationTokenSource(50);
        bool cancelled = false;
        try { await ReconnectReadiness.WaitAsync(() => Task.FromResult(1), () => throw new Exception("Server checked before client disconnected"), stopped.Token); }
        catch (OperationCanceledException) { cancelled = true; }
        Require(cancelled, "Stop could not cancel a reconnect wait.");
        return "Reconnect readiness: three-second minimum, server state checked, Stop cancellation; no live session queried.";
    }

    private static string ReconnectPolicy()
    {
        foreach (bool remember in new[] { false, true })
        foreach (bool initial in new[] { false, true })
        foreach (bool password in new[] { false, true })
        foreach (bool explicitSignIn in new[] { false, true })
        {
            var options = new SeatOptions { RememberWindowsLogin = remember, PromptForCredentials = initial };
            Require(AnodeDaemon.ReconnectNeedsPrompt(explicitSignIn, options, password)
                == (explicitSignIn || (!remember && (initial || password))), "Reconnect credential policy differs.");
        }
        Require(!new SeatOptions().RememberWindowsLogin, "Remember-login must remain opt-in.");
        return "Reconnect: 16 combinations, explicit Sign in retained, default opt-in.";
    }

    private static async Task<string> DaemonLifecycle()
    {
        int hostStarts = 0, logoffs = 0;
        using var daemon = new AnodeDaemon(new SeatOptions { RememberWindowsLogin = true }, () => 42, () => null,
            () => { hostStarts++; return Task.CompletedTask; }, _ => { logoffs++; return 42; }, () => { });
        await daemon.ReconnectAsync(promptForCredentials: true);
        Require((await daemon.StatusAsync())["signInPrompt"]!.GetValue<bool>(), "Explicit Sign in must force a repair prompt.");
        daemon.OnViewerLoginComplete();
        Require(hostStarts == 1, "Host started before completed sign-in or failed to start.");
        await daemon.ReconnectAsync();
        var status = await daemon.StatusAsync();
        Require(!status["signInPrompt"]!.GetValue<bool>() && status["rememberWindowsLogin"]!.GetValue<bool>(),
            "Normal reconnect must prefer Windows reuse over a forced prompt.");
        daemon.OnViewerLogonError(0);
        Require((await daemon.StatusAsync())["state"]!.GetValue<string>() == "connecting", "Native retry treated as terminal failure.");
        Require(hostStarts == 1 && logoffs == 0, "Reconnect restarted host or logged off seat.");
        await daemon.StopSeatAsync("injected remember-login check, no live seat");
        daemon.OnViewerLoginComplete();
        Require(logoffs == 1 && hostStarts == 1, "Stop did not cancel delayed login.");
        using var firstPrompt = new AnodeDaemon(new SeatOptions { RememberWindowsLogin=true, PromptForCredentials=true },
            () => null, () => null, () => Task.CompletedTask, _ => null, () => { });
        await firstPrompt.StopSeatAsync("injected stopped seat");
        var restart=firstPrompt.StartSeatAsync();
        Require(!(await firstPrompt.StatusAsync())["signInPrompt"]!.GetValue<bool>(), "Later seat startup inherited first forced sign-in.");
        await firstPrompt.StopSeatAsync("end injected restart");
        await restart;
        return "Injected lifecycle: sign-in, cached reconnect, retry, host gate and Stop; no live seat.";
    }

    private static string Arguments()
    {
        Cli.CheckArguments("start", new[] { "--remember-login" });
        Cli.CheckArguments("up", new[] { "--sign-in", "--remember-login" });
        foreach (string command in new[] { "start", "up" })
        {
            bool refused = false;
            try { Cli.CheckArguments(command, new[] { "--remember-login", "--hidden" }); }
            catch (Exception error) when (error.Message.Contains("--remember-login may open Windows sign-in")) { refused = true; }
            Require(refused, "Hidden startup accepted interactive credential saving.");
        }
        return "CLI: visible opt-in accepted; hidden start/up refused without dispatch.";
    }
}
