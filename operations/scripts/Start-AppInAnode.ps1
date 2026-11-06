param(
    [Parameter(Mandatory=$true)][ValidateSet('grokbot','trae','opencode')][string]$App,
    [switch]$Preflight,
    [switch]$ShowErrors
)
$ErrorActionPreference = 'Stop'
try {
    $root = Split-Path -Parent $PSScriptRoot
    $config = Get-Content -LiteralPath (Join-Path $root 'config\multiopen-apps.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $spec = $config.apps.$App
    $session = (Get-Process -Id $PID).SessionId
    $hosts = @(Get-CimInstance Win32_Process -Filter "Name='anode.exe'" | Where-Object {
        $_.SessionId -eq $session -and $_.CommandLine -match '(?i)(^|\s)__seat-host(\s|$)'
    })
    if (!(Test-Path -LiteralPath $spec.executable -PathType Leaf)) { throw 'Installed application not found' }
    $version = (Get-Item -LiteralPath $spec.executable).VersionInfo.ProductVersion
    if ($version -ne $spec.verifiedProductVersion) { throw "Application version changed ($version); revalidate multiopen configuration before launch." }
    if ($Preflight) {
        @{app=$App;session=$session;allowed=($hosts.Count -eq 1);version=$version;launchPerformed=$false} | ConvertTo-Json -Compress
        return
    }
    if ($hosts.Count -ne 1) { throw 'Open this shortcut INSIDE the Anode child session, like the WorkBuddy shortcut.' }
    # Restore only a window owned by a previous launch in this same child session.
    $logs = Join-Path $root 'artifacts\multiopen-launchers-20261031'
    if (!(Test-Path -LiteralPath $logs)) { New-Item -ItemType Directory -Path $logs | Out-Null }
    if (-not ('AnodeAppWindow' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class AnodeAppWindow {
    public delegate bool EnumWindowsProc(IntPtr window, IntPtr parameter);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr window, int command);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr window, StringBuilder text, int max);
    public static IntPtr FindWindow(int processId, string title) {
        IntPtr found = IntPtr.Zero;
        EnumWindows(delegate(IntPtr window, IntPtr parameter) {
            uint owner; GetWindowThreadProcessId(window, out owner);
            if (owner != processId || !IsWindowVisible(window)) return true;
            var text = new StringBuilder(1024); GetWindowText(window, text, text.Capacity);
            if (text.ToString() != title) return true;
            found = window; return false;
        }, IntPtr.Zero);
        return found;
    }
}
'@
    }
    if ($App -ne 'grokbot') {
        & python.exe (Join-Path $PSScriptRoot 'Prepare-MultiopenProfile.py') $App
        if ($LASTEXITCODE -ne 0) { throw 'Profile preparation failed; existing files were not replaced without verified backups.' }
        $runtime = Join-Path $root $spec.runtime
        foreach ($entry in $spec.sharedDirectories.PSObject.Properties) {
            $link = Join-Path $runtime $entry.Name
            if (!(Test-Path -LiteralPath $entry.Value -PathType Container)) { throw "Shared directory missing: $($entry.Value)" }
            if (Test-Path -LiteralPath $link) {
                $item = Get-Item -LiteralPath $link
                if ($item.LinkType -ne 'Junction' -or $item.Target -ine $entry.Value) { throw "Unexpected shared directory: $link" }
            } else {
                $parent = Split-Path -Parent $link
                if (!(Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
                New-Item -ItemType Junction -Path $link -Target $entry.Value | Out-Null
            }
        }
    }
    if ($App -eq 'grokbot') {
        $grok = Get-Content -LiteralPath (Join-Path $root 'config\grokbot-multiopen.json') -Raw | ConvertFrom-Json
        $receiptFolder = Join-Path (Join-Path $root $grok.runtimeDirectory) 'anode'
        $receiptFilter = 'launch-*.json'
    } else {
        $receiptFolder = $logs
        $receiptFilter = 'launch-'+$App+'-*.json'
    }
    $existing = $null
    $existingHandle = [IntPtr]::Zero
    foreach ($file in @(Get-ChildItem -LiteralPath $receiptFolder -File -Filter $receiptFilter -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)) {
        $previous = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
        if ($App -ne 'grokbot' -and $previous.runtime -ine (Join-Path $root $spec.runtime)) { continue }
        $candidate = Get-Process -Id $previous.pid -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.SessionId -eq $session -and $candidate.Path -ieq $spec.executable -and $candidate.MainWindowHandle -ne [IntPtr]::Zero) {
            if ($candidate.StartTime -gt ([DateTimeOffset]::Parse($previous.time).LocalDateTime.AddSeconds(5))) { continue }
            $candidateHandle = $candidate.MainWindowHandle
            if ($App -eq 'trae' -and $spec.preferredWindowTitle) {
                $candidateHandle = [AnodeAppWindow]::FindWindow($candidate.Id, $spec.preferredWindowTitle)
                if ($candidateHandle -eq [IntPtr]::Zero) { continue }
            }
            $existing = $candidate
            $existingHandle = $candidateHandle
            break
        }
    }
    if ($existing) {
        $handle = $existingHandle
        if ([AnodeAppWindow]::IsIconic($handle)) {
            [void][AnodeAppWindow]::ShowWindowAsync($handle, 9)
            $deadline = (Get-Date).AddSeconds(2)
            while ([AnodeAppWindow]::IsIconic($handle) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 50 }
        }
        [void][AnodeAppWindow]::SetForegroundWindow($handle)
        $receipt = [ordered]@{app=$App;pid=$existing.Id;session=$session;alreadyRunning=$true;launchPerformed=$false;restored=(![AnodeAppWindow]::IsIconic($handle));foreground=([AnodeAppWindow]::GetForegroundWindow() -eq $handle);time=(Get-Date).ToString('o')}
        if ($App -eq 'trae') { $receipt.windowTitle = $spec.preferredWindowTitle; $receipt.agentWindowVerified = $true }
        $receipt | ConvertTo-Json | Out-File -LiteralPath (Join-Path $logs ('restore-'+$App+'-'+[Guid]::NewGuid().ToString('N')+'.json')) -Encoding UTF8 -NoClobber
        $receipt | ConvertTo-Json -Compress
        if (!$receipt.restored) { throw 'Existing window did not restore; inspect the restore receipt.' }
        return
    }
    if ($App -eq 'grokbot') {
        & (Join-Path $PSScriptRoot 'Start-GrokBotInChild.ps1') -Engine anode -ExpectedSession $session
        return
    }
    $info = [Diagnostics.ProcessStartInfo]::new($spec.executable)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WorkingDirectory = Split-Path -Parent $spec.executable
    if ($App -eq 'trae') {
        $profile = Join-Path $runtime 'profile'
        $info.Arguments = '--user-data-dir "' + $profile + '" --extensions-dir "' + $spec.extensions + '"'
        foreach ($key in @('VSCODE_IPC_HOOK','VSCODE_IPC_HOOK_CLI','VSCODE_PORTABLE','VSCODE_APPDATA','ELECTRON_RUN_AS_NODE')) { $info.EnvironmentVariables.Remove($key) }
    } else {
        $info.EnvironmentVariables['OPENCODE_DESKTOP_TEST_ROOT'] = $runtime
        foreach ($key in @('OPENCODE_TEST_ONBOARDING','OPENCODE_DB','ELECTRON_RUN_AS_NODE')) { $info.EnvironmentVariables.Remove($key) }
    }
    $process = [Diagnostics.Process]::Start($info)
    $exited = $process.WaitForExit(1800)
    $receipt = [ordered]@{app=$App;pid=$process.Id;session=$session;runtime=$runtime;exitedEarly=$exited;time=(Get-Date).ToString('o')}
    if ($exited) { $receipt.exitCode=$process.ExitCode }
    if ($App -eq 'trae') {
        $deadline = (Get-Date).AddSeconds(25)
        $agentHandle = [IntPtr]::Zero
        while (!$process.HasExited -and (Get-Date) -lt $deadline) {
            $agentHandle = [AnodeAppWindow]::FindWindow($process.Id, $spec.preferredWindowTitle)
            if ($agentHandle -ne [IntPtr]::Zero) { break }
            Start-Sleep -Milliseconds 150
        }
        $receipt.windowTitle = $spec.preferredWindowTitle
        $receipt.agentWindowVerified = ($agentHandle -ne [IntPtr]::Zero)
    }
    $receipt | ConvertTo-Json | Out-File -LiteralPath (Join-Path $logs ('launch-'+$App+'-'+[Guid]::NewGuid().ToString('N')+'.json')) -Encoding UTF8 -NoClobber
    $receipt | ConvertTo-Json -Compress
    if ($App -eq 'trae' -and !$receipt.agentWindowVerified) { throw 'TRAE did not expose an Agent window before the startup deadline; inspect the receipt and application window.' }
    if ($exited -and $process.ExitCode -ne 0) { throw 'Application exited with an error; inspect the launch receipt.' }
} catch {
    if ($ShowErrors) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Application in Anode') | Out-Null
    }
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
