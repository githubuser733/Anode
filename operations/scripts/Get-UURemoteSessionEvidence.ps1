#requires -Version 5.1
<# Read-only Windows and UU process evidence. No input, restart or configuration changes. #>
[CmdletBinding()]
param([ValidatePattern('^[0-9a-f]{32}$')][string]$RunId = ([Guid]::NewGuid().ToString('N')))
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class UUSessionEvidenceNative {
    [DllImport("kernel32.dll")]
    public static extern uint WTSGetActiveConsoleSessionId();
    [DllImport("wtsapi32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool WTSGetChildSessionId(out uint sessionId);
}
'@
[uint32]$child = [uint32]::MaxValue
$childQuerySucceeded = [UUSessionEvidenceNative]::WTSGetChildSessionId([ref]$child)
$processes = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^GameViewer.*\.exe$|^anode\.exe$' } | ForEach-Object {
    [pscustomobject]@{
        name = $_.Name
        processId = [int]$_.ProcessId
        parentProcessId = [int]$_.ParentProcessId
        sessionId = [int]$_.SessionId
        createdUtc = $_.CreationDate.ToUniversalTime().ToString('o')
        executablePath = $_.ExecutablePath
        argumentFlagsOnly = @([regex]::Matches([string]$_.CommandLine, '(?<!\S)--?[A-Za-z][A-Za-z0-9_-]*') | ForEach-Object Value)
        numericSessionArguments = @([regex]::Matches([string]$_.CommandLine, '(?i)(?<!\S)--?(?:session(?:_?id)?|sid|console)[ =]+[0-9]+(?=\s|$)') | ForEach-Object Value)
    }
})
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$result = [ordered]@{
    observedAtUtc = [DateTime]::UtcNow.ToString('o')
    readOnly = $true
    isAdministrator = ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    observerSessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId
    physicalConsoleSessionId = [UUSessionEvidenceNative]::WTSGetActiveConsoleSessionId()
    childSessionQuerySucceeded = $childQuerySucceeded
    childSessionId = $child
    uuService = (Get-CimInstance Win32_Service -Filter "Name='GameViewerService'" | Select-Object Name, State, ProcessId, PathName)
    processes = $processes
    userReportedDesktop = 'Anode child desktop while Anode remains running'
    remoteCompatibility = 'FAIL - no fix has been applied'
}
$folder = Join-Path (Join-Path $projectRoot 'artifacts') 'uu-session-investigation-20261103'
if (-not (Test-Path -LiteralPath $folder)) { New-Item -ItemType Directory -Path $folder | Out-Null }
$outputPath = Join-Path $folder ('session-evidence-' + $RunId + '.json')
$result | ConvertTo-Json -Depth 8 | Out-File -LiteralPath $outputPath -Encoding UTF8 -NoClobber
$readback = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json
if (-not $readback.readOnly -or $readback.processes.Count -ne $processes.Count) { throw 'Evidence readback mismatch.' }
$readback | ConvertTo-Json -Depth 8
'EVIDENCE: ' + $outputPath
