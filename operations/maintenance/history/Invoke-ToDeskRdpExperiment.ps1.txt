#requires -Version 5.1
<#
Applies the explicitly approved ToDesk 5.2.3.0 one-byte experiment.
Without -Apply this is read-only. It never starts or stops Anode.
Remote desktop selection must be verified by the user; config checks cannot prove it.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [ValidatePattern('^[0-9a-f]{32}$')][string]$RunId = ([Guid]::NewGuid().ToString('N')),
    [switch]$Apply,
    [switch]$RestartClient
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = 'C:\Program Files\ToDesk\config.ini'
$executable = 'C:\Program Files\ToDesk\ToDesk.exe'
$serviceName = 'ToDesk_Service'
Add-Type -AssemblyName System.Security

function Get-BytesSha256([byte[]]$Bytes) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hash.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant() }
    finally { $hash.Dispose() }
}

function Write-NewBytes([string]$Path, [byte[]]$Bytes) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($Bytes, 0, $Bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
}

function Get-RdpEntry([byte[]]$Bytes) {
    $text = [Text.Encoding]::GetEncoding(28591).GetString($Bytes)
    $entries = [regex]::Matches($text, '(?m)^[ \t]*rdpOptimization[ \t]*=[^\r\n]*')
    if ($entries.Count -ne 1) { throw 'Expected exactly one rdpOptimization entry.' }
    $entry = $entries[0]
    $value = [regex]::Match($entry.Value, '^([ \t]*rdpOptimization[ \t]*=[ \t]*)(?<value>[01])[ \t]*$')
    $sections = [regex]::Matches($text.Substring(0, $entry.Index), '(?m)^[ \t]*\[(?<name>[^\]\r\n]+)\][ \t]*\r?$')
    if (-not $value.Success -or $sections.Count -eq 0 -or $sections[$sections.Count - 1].Groups['name'].Value -ne 'ConfigInfo') {
        throw 'Unexpected RDP option format or section.'
    }
    return [pscustomobject]@{ Value = [int]$value.Groups['value'].Value; Offset = $entry.Index + $value.Groups['value'].Index }
}

function Get-AnodeSnapshot {
    @(Get-CimInstance Win32_Process -Filter "Name='anode.exe'" |
        Where-Object { $_.CommandLine -match '\s(up|__seat-host)(\s|$)' } |
        Sort-Object ProcessId |
        ForEach-Object {
            [pscustomobject]@{
                processId = [int]$_.ProcessId
                sessionId = [int]$_.SessionId
                createdUtc = $_.CreationDate.ToUniversalTime().ToString('o')
            }
        })
}

$manifestFullPath = (Resolve-Path -LiteralPath $ManifestPath).Path
$artifactsRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'artifacts')) + [IO.Path]::DirectorySeparatorChar
if (-not $manifestFullPath.StartsWith($artifactsRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The proposal must be inside this project artifacts directory.'
}
$evidenceRoot = Split-Path -Parent $manifestFullPath
$proposal = Get-Content -LiteralPath $manifestFullPath -Raw | ConvertFrom-Json
if ($proposal.schemaVersion -ne 1 -or $proposal.targetPath -ne $configPath -or $proposal.executablePath -ne $executable -or
    $proposal.section -ne 'ConfigInfo' -or $proposal.option -ne 'rdpOptimization' -or
    $proposal.originalValue -ne 1 -or $proposal.candidateValue -ne 0 -or $proposal.differentBytes -ne 1) {
    throw 'Proposal does not describe the approved single-option experiment.'
}
$scope = [Security.Cryptography.DataProtectionScope]::CurrentUser
$original = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes((Join-Path $evidenceRoot 'original-config.dpapi')), $null, $scope)
$candidate = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes((Join-Path $evidenceRoot 'candidate-config.dpapi')), $null, $scope)
if ((Get-BytesSha256 $original) -ne $proposal.originalSha256 -or (Get-BytesSha256 $candidate) -ne $proposal.candidateSha256 -or
    $original.Length -ne $candidate.Length -or $original.Length -ne $proposal.byteLength) {
    throw 'Encrypted proposal verification failed.'
}
$originalEntry = Get-RdpEntry $original
$candidateEntry = Get-RdpEntry $candidate
$differentBytes = 0
for ($index = 0; $index -lt $original.Length; $index++) {
    if ($original[$index] -ne $candidate[$index]) { $differentBytes++ }
}
if ($differentBytes -ne 1 -or $originalEntry.Value -ne 1 -or $candidateEntry.Value -ne 0 -or
    $originalEntry.Offset -ne $candidateEntry.Offset -or $originalEntry.Offset -ne $proposal.byteOffset) {
    throw 'Decrypted proposal is not the approved one-byte change.'
}
if ((Get-Item -LiteralPath $executable).VersionInfo.FileVersion -ne $proposal.executableVersion -or
    (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant() -ne $proposal.executableSha256) {
    throw 'ToDesk executable changed since preparation; reassess before applying.'
}
$serviceBefore = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
if ($serviceBefore.State -ne 'Running' -or $serviceBefore.PathName -ne ('"' + $executable + '" --runservice --launchhide')) {
    throw 'ToDesk service is not the expected running service.'
}
$liveEntry = Get-RdpEntry ([IO.File]::ReadAllBytes($configPath))
if ($liveEntry.Value -ne 1) { throw 'Live option is no longer 1; no change was applied.' }
$anodeBefore = @(Get-AnodeSnapshot)
if ($anodeBefore.Count -lt 2) { throw 'Expected running Anode daemon and seat host; reassess the live state.' }
if (-not $Apply) {
    'PREFLIGHT_OK installedConfigUnchanged=true encryptedProposalVerified=true currentRdpOptimization=1'
    'RESTART_CLIENT: ' + [bool]$RestartClient
    'ANODE_BEFORE: ' + (ConvertTo-Json -InputObject $anodeBefore -Compress)
    exit 0
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Apply requires an elevated Windows PowerShell process; no service or configuration was changed.'
}

$runPath = Join-Path $evidenceRoot ('run-' + $RunId)
if (Test-Path -LiteralPath $runPath) { throw 'Run directory already exists; refusing to overwrite evidence.' }
New-Item -ItemType Directory -Path $runPath | Out-Null
$journalPath = Join-Path $runPath 'journal.log'
function Write-Step([string]$Message) {
    [IO.File]::AppendAllText($journalPath, ([DateTime]::UtcNow.ToString('o') + ' ' + $Message + [Environment]::NewLine))
}
$result = [ordered]@{
    startedAtUtc = [DateTime]::UtcNow.ToString('o')
    configPath = $configPath
    serviceName = $serviceName
    serviceProcessIdBefore = [int]$serviceBefore.ProcessId
    anodeBefore = $anodeBefore
    configByteWritten = $false
    configWriteVerified = $false
    restartVerified = $false
    restartClientRequested = [bool]$RestartClient
    stoppedClientProcessIds = @()
    remoteDesktopSelection = 'UNTESTED - user must reconnect ToDesk and identify the desktop'
    unexecutedRecoveryPaths = 'UNTESTED unless an error below shows that path ran'
    errors = @()
}
$restartRequired = $false
try {
    Write-Step 'Preflight passed; stopping only ToDesk_Service.'
    $restartRequired = $true
    Stop-Service -Name $serviceName -ErrorAction Stop
    $service = Get-Service -Name $serviceName
    $service.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Stopped, [TimeSpan]::FromSeconds(30))
    Write-Step 'ToDesk_Service stopped.'
    if ($RestartClient) {
        # A service-only restart left the original client alive in run 1.
        # Stop only verified ToDesk clients after the service has stopped,
        # so the experiment starts without an old in-memory configuration.
        $clients = @(Get-CimInstance Win32_Process -Filter "Name='ToDesk.exe'")
        foreach ($client in $clients) {
            if ($client.SessionId -eq 0 -or $client.ExecutablePath -ne $executable) {
                throw 'Unexpected remaining ToDesk process; refusing to terminate it.'
            }
            $clientProcess = Get-Process -Id $client.ProcessId -ErrorAction SilentlyContinue
            if ($null -eq $clientProcess) { continue }
            if ($clientProcess.Path -ne $executable -or
                $clientProcess.StartTime.ToUniversalTime().Ticks -ne $client.CreationDate.ToUniversalTime().Ticks) {
                throw 'ToDesk client identity changed; refusing to terminate it.'
            }
            Write-Step ('Stopping verified ToDesk client PID ' + $client.ProcessId + ', session ' + $client.SessionId + '.')
            Stop-Process -InputObject $clientProcess -Force -ErrorAction Stop
            if (-not $clientProcess.WaitForExit(10000)) { throw 'ToDesk client did not exit within ten seconds.' }
            $result.stoppedClientProcessIds += [int]$client.ProcessId
        }
        if (@(Get-CimInstance Win32_Process -Filter "Name='ToDesk.exe'").Count -ne 0) {
            throw 'A ToDesk process remains or restarted; no configuration byte will be written.'
        }
        Write-Step 'No ToDesk processes remain; cached client state excluded from the next launch.'
    }
    Write-Step 'Opening current config exclusively.'
    $file = [IO.File]::Open($configPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        $beforeWrite = New-Object byte[] ([int]$file.Length)
        $read = 0
        while ($read -lt $beforeWrite.Length) {
            $count = $file.Read($beforeWrite, $read, $beforeWrite.Length - $read)
            if ($count -eq 0) { throw 'Unexpected end of configuration file.' }
            $read += $count
        }
        $entry = Get-RdpEntry $beforeWrite
        if ($entry.Value -ne 1) { throw 'Option changed during service shutdown; no byte written.' }
        $beforeHash = Get-BytesSha256 $beforeWrite
        $backupPath = Join-Path $runPath 'before-apply-config.dpapi'
        Write-NewBytes $backupPath ([Security.Cryptography.ProtectedData]::Protect($beforeWrite, $null, $scope))
        $backupReadback = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($backupPath), $null, $scope)
        if ((Get-BytesSha256 $backupReadback) -ne $beforeHash) { throw 'Fresh encrypted backup verification failed; no byte written.' }
        $expected = [byte[]]$beforeWrite.Clone()
        $expected[$entry.Offset] = 48
        $result.beforeApplySha256 = $beforeHash
        $result.expectedAfterSha256 = Get-BytesSha256 $expected
        $result.byteOffset = $entry.Offset
        $result.byteLength = $beforeWrite.Length
        $result.liveConfigChangedSinceProposal = ($beforeHash -ne $proposal.originalSha256)
        Write-Step 'Fresh encrypted backup verified; writing only ASCII 1 -> 0.'
        $null = $file.Seek($entry.Offset, [IO.SeekOrigin]::Begin)
        $file.WriteByte(48)
        $result.configByteWritten = $true
        $file.Flush($true)
    }
    finally { $file.Dispose() }
    $written = [IO.File]::ReadAllBytes($configPath)
    if ((Get-BytesSha256 $written) -ne $result.expectedAfterSha256) { throw 'Post-write hash differs from the one-byte candidate.' }
    $result.configWriteVerified = $true
    Write-Step 'Exact one-byte write verified.'
}
catch {
    $result.errors += $_.Exception.ToString()
    Write-Step ('Apply failed: ' + $_.Exception.ToString())
}
finally {
    if ($restartRequired) {
        try {
            Write-Step 'Ensuring ToDesk_Service is running again.'
            $service = Get-Service -Name $serviceName
            if ($service.Status -eq [ServiceProcess.ServiceControllerStatus]::StopPending) {
                $service.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Stopped, [TimeSpan]::FromSeconds(30))
            }
            $service.Refresh()
            if ($service.Status -ne [ServiceProcess.ServiceControllerStatus]::Running) { Start-Service -Name $serviceName -ErrorAction Stop }
            $service.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(30))
            $result.restartVerified = $true
            Write-Step 'ToDesk_Service reports Running.'
        }
        catch {
            $result.errors += $_.Exception.ToString()
            Write-Step ('Service restart failed: ' + $_.Exception.ToString())
        }
    }
}

try {
    $afterEntry = Get-RdpEntry ([IO.File]::ReadAllBytes($configPath))
    $result.rdpOptimizationAfter = $afterEntry.Value
    if ($afterEntry.Value -ne 0) { $result.errors += 'ToDesk did not retain rdpOptimization=0 after restart.' }
    $serviceAfter = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
    $result.serviceStateAfter = $serviceAfter.State
    $result.serviceProcessIdAfter = [int]$serviceAfter.ProcessId
    $result.anodeAfter = @(Get-AnodeSnapshot)
    $result.anodeUnchanged = ((ConvertTo-Json -InputObject $anodeBefore -Compress) -eq (ConvertTo-Json -InputObject $result.anodeAfter -Compress))
    if (-not $result.anodeUnchanged) { $result.errors += 'Anode process/session snapshot changed during the experiment; inspect before concluding.' }
}
catch { $result.errors += $_.Exception.ToString() }
$result.finishedAtUtc = [DateTime]::UtcNow.ToString('o')
$result.infrastructureChecksPassed = ($result.configWriteVerified -and $result.restartVerified -and $result.anodeUnchanged -and $result.errors.Count -eq 0)
$json = $result | ConvertTo-Json -Depth 8
Write-NewBytes (Join-Path $runPath 'result.json') ([Text.Encoding]::UTF8.GetBytes($json))
Write-Step ('Finished; infrastructureChecksPassed=' + $result.infrastructureChecksPassed + '; remote desktop selection UNTESTED.')
$json
if (-not $result.infrastructureChecksPassed) { exit 1 }
exit 0
