#requires -Version 5.1
<# Daily entry. Ordinary callers use a fixed, pre-installed task; no daily UAC. #>
[CmdletBinding()]
param(
    [ValidateSet('Status','Enable','Restore')][string]$Action = 'Status',
    [ValidatePattern('^[0-9a-f]{32}$')][string]$OperationId = ([Guid]::NewGuid().ToString('N')),
    [string]$EvidenceRoot
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $EvidenceRoot) { $EvidenceRoot = Join-Path $projectRoot 'artifacts\uu-session-investigation-20261103' }
[void][IO.Directory]::CreateDirectory($EvidenceRoot)
Import-Module (Join-Path $PSScriptRoot 'UUConsoleTask.psm1') -Force
$receiptPath = Join-Path $evidenceRoot ('mode-' + $OperationId + '.json')
$trialScript = Join-Path $PSScriptRoot 'Invoke-UUConsoleTrial.ps1'
$runnerScript = Join-Path $PSScriptRoot 'Start-UUConsoleTrial.ps1'

function Inspect-Binding {
    $output = & $trialScript -Mode Inspect -EvidenceRoot $evidenceRoot
    if ($LASTEXITCODE -ne 0) { throw 'UU inspection failed.' }
    return (($output -join [Environment]::NewLine) | ConvertFrom-Json)
}
function Get-KeptProfile {
    foreach ($folder in @(Get-ChildItem -LiteralPath $evidenceRoot -Directory -Filter 'console-trial-*' | Sort-Object LastWriteTimeUtc -Descending)) {
        $runId = $folder.Name.Substring('console-trial-'.Length)
        if ($runId -notmatch '^[0-9a-f]{32}$') { continue }
        $resultPath = Join-Path $folder.FullName 'result.json'
        if (-not (Test-Path -LiteralPath $resultPath)) { continue }
        $result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
        if (-not $result.keptConsoleRuntime) { continue }
        $restored = $false
        foreach ($restoreFile in @(Get-ChildItem -LiteralPath $folder.FullName -File -Filter 'restore-*.json')) {
            if ((Get-Content -LiteralPath $restoreFile.FullName -Raw | ConvertFrom-Json).succeeded) { $restored = $true }
        }
        if ($restored) { continue }
        return [pscustomobject]@{
            runId = $runId
            ready = (Get-Content -LiteralPath (Join-Path $folder.FullName 'ready.json') -Raw | ConvertFrom-Json)
            before = (Get-Content -LiteralPath (Join-Path $folder.FullName 'before.json') -Raw | ConvertFrom-Json)
        }
    }
    return $null
}
function Save-Receipt($Value) {
    $Value | ConvertTo-Json -Depth 8 | Out-File -LiteralPath $receiptPath -Encoding UTF8 -NoClobber
    $readback = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    $readback | ConvertTo-Json -Depth 8
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
try {
    $inspection = Inspect-Binding
    if ($Action -eq 'Status') {
        $profile = Get-KeptProfile
        Save-Receipt ([ordered]@{ action=$Action; success=$true; observedAtUtc=[DateTime]::UtcNow.ToString('o'); binding=$inspection; keptTrialRunId=$(if ($profile) { $profile.runId } else { $null }) })
        exit 0
    }
    if ($Action -eq 'Enable' -and (Test-UUConsoleBinding $inspection)) {
        Save-Receipt ([ordered]@{ action=$Action; success=$true; noChangeNeeded=$true; callerIsAdministrator=$isAdmin; binding=$inspection; note='Both workers are already in the physical console. No service control, process restart or elevation was requested.' })
        exit 0
    }
    if (-not $inspection.serviceHashMatchesInspectedCode) { throw 'UU service version has not been inspected; no controls sent. Status remains available.' }
    if (-not $isAdmin) {
        $result=Invoke-UUConsoleRegisteredTask -Action $Action
        $result | Add-Member NoteProperty requestedVia 'FixedScheduledTask' -Force
        $result | Add-Member NoteProperty callerIsAdministrator $false -Force
        Save-Receipt $result
        exit 0
    }
    $profile = Get-KeptProfile
    if ($Action -eq 'Restore') {
        if ($profile) {
            & $trialScript -Mode Restore -RunId $profile.runId -EvidenceRoot $evidenceRoot | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'UU runtime restoration reported failure; inspect restore evidence.' }
        }
        Save-Receipt ([ordered]@{ action=$Action; success=$true; finishedAtUtc=[DateTime]::UtcNow.ToString('o'); restoredTrialRunId=$(if ($profile) { $profile.runId } else { $null }); binding=(Inspect-Binding) })
        exit 0
    }

    $sameWorkers = $null -ne $profile
    if ($profile) {
        foreach ($owned in $profile.ready.ownedWorkers) {
            if (@($inspection.workers | Where-Object { $_.processId -eq $owned.processId -and $_.sessionId -eq $profile.ready.consoleSessionId -and $_.createdUtc -eq $owned.createdUtc }).Count -ne 1) { $sameWorkers=$false }
        }
        $servicePid = (Get-CimInstance Win32_Service -Filter "Name='GameViewerService'").ProcessId
        if ($servicePid -ne $profile.before.serviceProcessId) { $sameWorkers=$false }
    }
    if ($sameWorkers -and $inspection.bindingPass -and $inspection.workers.Count -eq 2) {
        Save-Receipt ([ordered]@{ action=$Action; success=$true; noChangeNeeded=$true; trialRunId=$profile.runId; binding=$inspection })
        exit 0
    }
    if ($profile) {
        & $trialScript -Mode Restore -RunId $profile.runId -EvidenceRoot $evidenceRoot | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not restore the previous runtime before reapplying.' }
    }
    $runId = [Guid]::NewGuid().ToString('N')
    $runPath = Join-Path $evidenceRoot ('console-trial-' + $runId)
    $runnerArgs = '-NoProfile -ExecutionPolicy Bypass -File "' + $runnerScript + '" -Mode Apply -RunId ' + $runId + ' -EvidenceRoot "' + $evidenceRoot + '"'
    $runner = Start-Process powershell.exe -ArgumentList $runnerArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    do {
        if (Test-Path -LiteralPath (Join-Path $runPath 'ready.json')) { break }
        $runnerError = Join-Path $evidenceRoot ('runner-' + $runId + '-error.json')
        if (Test-Path -LiteralPath $runnerError) { throw (Get-Content -LiteralPath $runnerError -Raw | ConvertFrom-Json).exception }
        $runner.Refresh()
        if ($runner.HasExited) { throw ('Trial runner exited before ready; inspect ' + $runPath) }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    $readyPath = Join-Path $runPath 'ready.json'
    if (-not (Test-Path -LiteralPath $readyPath)) { throw 'Trial not ready within 90 seconds; its automatic restore remains armed.' }
    $ready = Get-Content -LiteralPath $readyPath -Raw | ConvertFrom-Json
    $after = Inspect-Binding
    if (-not $after.bindingPass -or -not $ready.anodeUnchanged -or $after.workers.Count -ne 2) {
        'Binding validation failed; restore requested.' | Out-File -LiteralPath (Join-Path $runPath 'restore-now.txt') -Encoding UTF8 -NoClobber
        throw 'Console binding verification failed; restoration requested.'
    }
    'Reapplied the runtime approach previously verified by the user; remote verification is still recommended.' |
        Out-File -LiteralPath (Join-Path $runPath 'keep-runtime.txt') -Encoding UTF8 -NoClobber
    if (-not $runner.WaitForExit(30000)) { throw 'Trial did not acknowledge retention within 30 seconds.' }
    $result = Get-Content -LiteralPath (Join-Path $runPath 'result.json') -Raw | ConvertFrom-Json
    if (-not $result.keptConsoleRuntime -or $result.errors.Count -gt 0) { throw 'Trial did not retain the console runtime successfully.' }
    Save-Receipt ([ordered]@{ action=$Action; success=$true; noChangeNeeded=$false; trialRunId=$runId; binding=$after; remoteDesktopSelection='Reapply path; verify by reconnecting UU.' })
}
catch {
    if (-not (Test-Path -LiteralPath $receiptPath)) { Save-Receipt ([ordered]@{ action=$Action; success=$false; failedAtUtc=[DateTime]::UtcNow.ToString('o'); exception=$_.Exception.ToString(); position=$_.InvocationInfo.PositionMessage }) }
    Write-Error $_ -ErrorAction Continue
    exit 1
}
