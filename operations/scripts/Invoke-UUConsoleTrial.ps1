#requires -Version 5.1
<#
Version-pinned, reversible runtime trial for UU 4.45.0.4318.
Inspect is read-only. Apply pauses the verified service supervision control,
launches the unchanged installed workers in the physical console, and waits
up to five minutes for a keep-runtime.txt marker. Otherwise it restores UU.
No installed executable, application config, service startup type or Anode
process is changed. Remote behavior and recovery paths require live testing.
#>
[CmdletBinding()]
param(
    [ValidateSet('Inspect', 'Apply', 'Restore')][string]$Mode = 'Inspect',
    [ValidatePattern('^[0-9a-f]{32}$')][string]$RunId = ([Guid]::NewGuid().ToString('N'))
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$evidenceRoot = Join-Path (Join-Path $projectRoot 'artifacts') 'uu-session-investigation-20261103'
$runPath = Join-Path $evidenceRoot ('console-trial-' + $RunId)
$serviceName = 'GameViewerService'
$installRoot = 'C:\Program Files\Netease\GameViewer'
$bin = Join-Path $installRoot 'bin'
$serviceExe = Join-Path $installRoot 'GameViewerService.exe'
$expectedServiceHash = '708fcc3a953aa7952cdf6c59a793314618636aaffdc8b2b3e0979c2b6a0e97e6'
$psExec = Join-Path $bin 'PsExec64.exe'
$workerNames = @('GameViewerServer.exe', 'GameViewerHealthd.exe')
# Verified in this exact executable's switch table and control handler:
# 130 clears the supervision flag; 131 sets it. These are not public UU APIs.
$disableSupervision = 130
$enableSupervision = 131
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class UUConsoleTrialNative {
    [DllImport("kernel32.dll")]
    public static extern uint WTSGetActiveConsoleSessionId();
}
'@

function Get-AnodeSnapshot {
    @(Get-CimInstance Win32_Process -Filter "Name='anode.exe'" |
        Where-Object { $_.CommandLine -match '\s(up|__seat-host)(\s|$)' } |
        Sort-Object ProcessId | ForEach-Object {
            [pscustomobject]@{ processId = [int]$_.ProcessId; sessionId = [int]$_.SessionId; createdUtc = $_.CreationDate.ToUniversalTime().ToString('o') }
        })
}
function Get-Workers {
    @(Get-CimInstance Win32_Process -Filter "Name='GameViewerServer.exe' OR Name='GameViewerHealthd.exe'")
}
function Get-WorkerRecord($Process) {
    [pscustomobject]@{
        name = $Process.Name
        processId = [int]$Process.ProcessId
        sessionId = [int]$Process.SessionId
        createdUtc = $Process.CreationDate.ToUniversalTime().ToString('o')
        executablePath = $Process.ExecutablePath
    }
}
function Write-NewJson([string]$Path, $Value) {
    $Value | ConvertTo-Json -Depth 10 | Out-File -LiteralPath $Path -Encoding UTF8 -NoClobber
    $null = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}
function Write-Step([string]$Message) {
    [IO.File]::AppendAllText((Join-Path $runPath 'journal.log'), ([DateTime]::UtcNow.ToString('o') + ' ' + $Message + [Environment]::NewLine))
}
function Restore-Runtime($OwnedWorkers) {
    $restoreErrors = @()
    foreach ($owned in @($OwnedWorkers)) {
        try {
            $current = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$owned.processId)
            if ($null -eq $current) { continue }
            if ($current.ExecutablePath -ne $owned.executablePath -or
                $current.CreationDate.ToUniversalTime().ToString('o') -ne $owned.createdUtc) {
                throw ('Worker PID identity changed; not terminating PID ' + $owned.processId)
            }
            Stop-Process -Id $owned.processId -Force -ErrorAction Stop
            Write-Step ('Stopped trial-owned ' + $owned.name + ' PID ' + $owned.processId)
        }
        catch { $restoreErrors += $_.Exception.ToString() }
    }
    try {
        $service = Get-Service -Name $serviceName
        if ($service.Status -ne [ServiceProcess.ServiceControllerStatus]::Running) { Start-Service -Name $serviceName }
        $service.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(30))
        $service.ExecuteCommand($enableSupervision)
        Write-Step 'UU automatic worker supervision enabled again (control 131).'
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        do {
            $restored = @(Get-Workers)
            if (@($restored | Where-Object Name -eq 'GameViewerServer.exe').Count -eq 1 -and
                @($restored | Where-Object Name -eq 'GameViewerHealthd.exe').Count -eq 1) { break }
            Start-Sleep -Milliseconds 500
        } while ([DateTime]::UtcNow -lt $deadline)
        if (@($restored | Where-Object Name -eq 'GameViewerServer.exe').Count -ne 1 -or
            @($restored | Where-Object Name -eq 'GameViewerHealthd.exe').Count -ne 1) {
            throw 'UU service is running but both original worker types did not return within 30 seconds.'
        }
    }
    catch { $restoreErrors += $_.Exception.ToString() }
    return [pscustomobject]@{ succeeded = ($restoreErrors.Count -eq 0); errors = $restoreErrors; workers = @((Get-Workers) | ForEach-Object { Get-WorkerRecord $_ }) }
}

if ((Get-FileHash -LiteralPath $serviceExe -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedServiceHash) {
    throw 'UU service binary differs from the inspected version; no controls sent.'
}
$serviceInfo = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
if ($serviceInfo.PathName -ne ('"' + $serviceExe + '" --service')) { throw 'Unexpected UU service image path.' }
$consoleSession = [UUConsoleTrialNative]::WTSGetActiveConsoleSessionId()
if ($consoleSession -eq [uint32]::MaxValue -or $consoleSession -eq 0) { throw 'No usable physical console session.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$workersBefore = @(Get-Workers)
$anodeBefore = @(Get-AnodeSnapshot)
$signature = Get-AuthenticodeSignature -LiteralPath $psExec
if ($signature.Status -ne [Management.Automation.SignatureStatus]::Valid -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
    throw 'Bundled PsExec does not have the verified Microsoft signature.'
}
if ($Mode -eq 'Inspect') {
    [ordered]@{
        preflight = 'PASS - read-only'
        serviceHashMatchesInspectedCode = $true
        isAdministrator = $isAdmin
        physicalConsoleSessionId = $consoleSession
        workers = @($workersBefore | ForEach-Object { Get-WorkerRecord $_ })
        bindingPass = (@($workersBefore | Where-Object { $_.Name -eq 'GameViewerServer.exe' -and $_.SessionId -eq $consoleSession }).Count -eq 1)
        inspectionScope = 'Read-only; execution outcomes are in per-run result, restore, and user-verdict files.'
    } | ConvertTo-Json -Depth 6
    exit 0
}
if (-not $isAdmin) { throw 'Apply and Restore require an elevated Windows PowerShell process.' }
if ($Mode -eq 'Restore') {
    $readyPath = Join-Path $runPath 'ready.json'
    $ready = Get-Content -LiteralPath $readyPath -Raw | ConvertFrom-Json
    $restored = Restore-Runtime $ready.ownedWorkers
    Write-NewJson (Join-Path $runPath ('restore-' + [Guid]::NewGuid().ToString('N') + '.json')) $restored
    $restored | ConvertTo-Json -Depth 6
    if (-not $restored.succeeded) { exit 1 }
    exit 0
}
if ($serviceInfo.State -ne 'Running' -or $anodeBefore.Count -lt 2) { throw 'UU service and both Anode processes must already be running.' }
foreach ($name in $workerNames) {
    $existing = @($workersBefore | Where-Object Name -eq $name)
    $expectedPath = Join-Path $bin $name
    if ($existing.Count -ne 1 -or $existing[0].ExecutablePath -ne $expectedPath) { throw ('Unexpected worker identity: ' + $name) }
    $commandLine = $existing[0].CommandLine.Trim()
    $allowedCommands = @($expectedPath, ('"' + $expectedPath + '"'))
    if ($name -eq 'GameViewerHealthd.exe') {
        # Elevated readback verified this exact executable is launched without
        # arguments using a command-line path that omits its .exe suffix.
        $extensionlessPath = Join-Path $bin 'GameViewerHealthd'
        $allowedCommands += @($extensionlessPath, ('"' + $extensionlessPath + '"'))
    }
    if ($allowedCommands -notcontains $commandLine) {
        throw ('Worker has unverified arguments; no changes applied: ' + $name)
    }
}
if (Test-Path -LiteralPath $runPath) { throw 'Trial directory exists; refusing to overwrite evidence.' }
New-Item -ItemType Directory -Path $runPath | Out-Null
Write-NewJson (Join-Path $runPath 'before.json') ([ordered]@{
    startedAtUtc = [DateTime]::UtcNow.ToString('o'); consoleSessionId = $consoleSession
    workers = @($workersBefore | ForEach-Object { Get-WorkerRecord $_ }); anode = $anodeBefore
    serviceProcessId = [int]$serviceInfo.ProcessId; serviceHash = $expectedServiceHash
})
$ownedWorkers = @()
$supervisionTouched = $false
$keep = $false
$errors = @()
$restoreResult = $null
try {
    $supervisionTouched = $true
    (Get-Service -Name $serviceName).ExecuteCommand($disableSupervision)
    Write-Step 'Sent verified UU supervision pause control 130.'
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        $remaining = @(Get-Workers)
        if ($remaining.Count -eq 0) { break }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($remaining.Count -gt 0) { throw 'Original UU workers did not stop; aborting trial.' }
    Write-Step 'Original server and health worker have stopped.'
    foreach ($name in $workerNames) {
        $workerPath = Join-Path $bin $name
        $outLog = Join-Path $runPath ($name + '.stdout.txt')
        $errLog = Join-Path $runPath ($name + '.stderr.txt')
        # The Microsoft tool is already bundled with UU. Keep the existing
        # SYSTEM account, changing only its target Windows desktop session.
        $launchArgs = '-accepteula -nobanner -s -i ' + $consoleSession + ' -d -w "' + $bin + '" "' + $workerPath + '"'
        $launcher = Start-Process -FilePath $psExec -ArgumentList $launchArgs -WorkingDirectory $bin -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
        if (-not $launcher.WaitForExit(30000)) {
            Stop-Process -InputObject $launcher -Force
            throw ('PsExec timed out launching ' + $name)
        }
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        do {
            $started = @(Get-Workers | Where-Object { $_.Name -eq $name -and $_.SessionId -eq $consoleSession -and $_.ExecutablePath -eq $workerPath })
            if ($started.Count -eq 1) { break }
            Start-Sleep -Milliseconds 500
        } while ([DateTime]::UtcNow -lt $deadline)
        if ($started.Count -ne 1) { throw ('Expected exactly one console worker after launch: ' + $name + '; launcher exit=' + $launcher.ExitCode) }
        $ownedWorkers += Get-WorkerRecord $started[0]
        Write-NewJson (Join-Path $runPath ($name + '.process.json')) $ownedWorkers[-1]
        Write-Step ('Started unchanged ' + $name + ' as SYSTEM in session ' + $consoleSession + ', PID ' + $started[0].ProcessId)
    }
    if (@(Get-Workers | Where-Object SessionId -ne $consoleSession).Count -gt 0) { throw 'UU has started a worker outside the physical console.' }
    $anodeAfter = @(Get-AnodeSnapshot)
    if ((ConvertTo-Json -InputObject $anodeBefore -Compress) -ne (ConvertTo-Json -InputObject $anodeAfter -Compress)) { throw 'Anode process/session snapshot changed; aborting trial.' }
    Write-NewJson (Join-Path $runPath 'ready.json') ([ordered]@{
        readyAtUtc = [DateTime]::UtcNow.ToString('o'); consoleSessionId = $consoleSession
        ownedWorkers = $ownedWorkers; anodeUnchanged = $true; anode = $anodeAfter
        bindingPass = $true; remoteDesktopSelection = 'UNTESTED - awaiting user reconnect'
        autoRestoreAfterSeconds = 300
    })
    Write-Step 'READY for user reconnect; waiting at most five minutes for a verdict.'
    $deadline = [DateTime]::UtcNow.AddSeconds(300)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath (Join-Path $runPath 'restore-now.txt')) { Write-Step 'Restore requested by experiment controller.'; break }
        if (Test-Path -LiteralPath (Join-Path $runPath 'keep-runtime.txt')) { $keep = $true; break }
        $current = @(Get-Workers)
        if ($current.Count -ne 2 -or @($current | Where-Object SessionId -ne $consoleSession).Count -gt 0) {
            throw 'Console worker binding changed while waiting; restoring normal UU supervision.'
        }
        Start-Sleep -Seconds 1
    }
}
catch {
    $errors += $_.Exception.ToString()
    Write-Step ('Trial failed: ' + $_.Exception.ToString())
}
finally {
    if ($supervisionTouched -and -not $keep) {
        $restoreResult = Restore-Runtime $ownedWorkers
        if (-not $restoreResult.succeeded) { $errors += $restoreResult.errors }
    }
    Write-NewJson (Join-Path $runPath 'result.json') ([ordered]@{
        finishedAtUtc = [DateTime]::UtcNow.ToString('o'); keptConsoleRuntime = $keep
        errors = $errors; restoration = $restoreResult; ownedWorkers = $ownedWorkers
        remoteDesktopSelection = 'See user-verdict.json; process binding alone is not proof of repair'
        limitations = 'Runtime workaround is version-dependent; service restart, app update, lock screen, and Anode session recreation require separate validation.'
    })
    Write-Step ('Finished; keptConsoleRuntime=' + $keep + '; errors=' + $errors.Count)
}
if ($errors.Count -gt 0) { exit 1 }
exit 0
