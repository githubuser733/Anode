param([switch]$Preflight, [switch]$ShowErrors)
$ErrorActionPreference = 'Stop'
try {
    $root = Split-Path -Parent $PSScriptRoot
    $config = Get-Content -LiteralPath (Join-Path $root 'config/alpha-shared.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $anodeConfig = Get-Content -LiteralPath (Join-Path $root 'config/anode-local-mcp.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $anodeExe = [IO.Path]::GetFullPath((Join-Path $root $anodeConfig.executable))
    $exe = [IO.Path]::GetFullPath($config.executable)
    $profile = [IO.Path]::GetFullPath($config.sharedUserData)
    $expectedProfile = [IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'Alpha'))
    foreach ($file in @($exe, $config.asar)) {
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing verified Alpha file: $file" }
    }
    if (!(Test-Path -LiteralPath $profile -PathType Container) -or $profile -ine $expectedProfile) {
        throw 'The current Windows user does not resolve to the verified shared Alpha data directory.'
    }
    if ((Get-FileHash -LiteralPath $config.asar -Algorithm SHA256).Hash -ine $config.asarSha256) {
        throw 'Alpha changed since verification. Recheck its data path and single-instance behavior before updating config/alpha-shared.json.'
    }
    if (@($config.arguments).Count -ne 0) { throw 'This verified entry requires the original empty argument list.' }
    $session = (Get-Process -Id $PID).SessionId
    $seatHosts = @(Get-CimInstance Win32_Process -Filter "Name='anode.exe'" | Where-Object {
        $_.SessionId -eq $session -and $_.ExecutablePath -ieq $anodeExe -and $_.CommandLine -match '(^|\s)__seat-host(\s|$)'
    })
    $instances = @(Get-CimInstance Win32_Process -Filter "Name='Alpha.exe'")
    $outside = @($instances | Where-Object SessionId -ne $session)
    $inside = @($instances | Where-Object SessionId -eq $session)
    $allowed = $seatHosts.Count -eq 1 -and $outside.Count -eq 0
    if ($Preflight) {
        [ordered]@{status='ALPHA_PREFLIGHT_OK';session=$session;allowed=$allowed;seatHostCount=$seatHosts.Count;
            outsideSessionProcesses=@($outside | Select-Object ProcessId,SessionId);insideSessionProcessCount=$inside.Count;
            executable=$exe;arguments=@();workingDirectory=$config.workingDirectory;sharedUserData=$profile;launchPerformed=$false} | ConvertTo-Json -Depth 5
        return
    }
    if ($seatHosts.Count -ne 1) { throw 'Open Alpha-Shared-In-Anode.lnk INSIDE the existing Anode child desktop. This entry does not start or replace a desktop session.' }
    if ($outside.Count -ne 0) {
        $sessions = ($outside | Select-Object -ExpandProperty SessionId -Unique) -join ', '
        throw "Alpha is already running in Windows session(s) $sessions. Save your work and exit Alpha normally there before opening it in Anode. Shared-data simultaneous use is not verified."
    }
    $info = [Diagnostics.ProcessStartInfo]::new($exe)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WorkingDirectory = $config.workingDirectory
    $app = [Diagnostics.Process]::Start($info)
    $exited = $app.WaitForExit(1200)
    $deadline = (Get-Date).AddSeconds(20)
    do {
        $windows = @(Get-Process -Name Alpha -ErrorAction SilentlyContinue | Where-Object {
            $_.SessionId -eq $session -and $_.Path -ieq $exe -and $_.MainWindowHandle -ne 0
        })
        if ($windows.Count -gt 0) { break }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    $receipt = [ordered]@{status=$(if ($windows.Count) {'ALPHA_CHILD_WINDOW_STARTED'} else {'ALPHA_CHILD_WINDOW_NOT_CONFIRMED'});
        at=(Get-Date).ToString('o');session=$session;startedPid=$app.Id;existingProcessesBefore=$inside.Count;
        launchMode=$(if ($inside.Count) {'reuse same-session single instance'} else {'start in child session'});
        executable=$exe;arguments=@();workingDirectory=$config.workingDirectory;sharedUserData=$profile;
        exitedEarly=$exited;windows=@($windows | Select-Object Id,SessionId,MainWindowTitle,MainWindowHandle)}
    if ($exited) { $receipt.exitCode = $app.ExitCode }
    $receiptDirectory = Join-Path $root $config.receiptDirectory
    [IO.Directory]::CreateDirectory($receiptDirectory) | Out-Null
    $receiptPath = Join-Path $receiptDirectory ('alpha-'+[Guid]::NewGuid().ToString('N')+'.json')
    $receipt | ConvertTo-Json -Depth 5 | Out-File -LiteralPath $receiptPath -Encoding UTF8 -NoClobber
    if ($windows.Count -eq 0) { throw "Alpha did not expose a child-session window. Inspect $receiptPath before retrying; no process was killed." }
    $receipt | ConvertTo-Json -Depth 5
} catch {
    if ($ShowErrors) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Alpha in Anode') | Out-Null
    }
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
