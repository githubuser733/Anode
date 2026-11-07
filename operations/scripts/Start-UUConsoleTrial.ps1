#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Inspect', 'Apply', 'Restore')][string]$Mode = 'Inspect',
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{32}$')][string]$RunId,
    [string]$EvidenceRoot
)
$ErrorActionPreference = 'Stop'
if (-not $EvidenceRoot) { $EvidenceRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'artifacts\uu-session-investigation-20261103' }
$folder = $EvidenceRoot
$prefix = Join-Path $folder ('runner-' + $RunId)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
[ordered]@{
    startedUtc = [DateTime]::UtcNow.ToString('o')
    mode = $Mode
    isAdministrator = ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
} | ConvertTo-Json | Out-File -LiteralPath ($prefix + '-started.json') -Encoding UTF8 -NoClobber
try {
    & (Join-Path $PSScriptRoot 'Invoke-UUConsoleTrial.ps1') -Mode $Mode -RunId $RunId -EvidenceRoot $EvidenceRoot
    [ordered]@{ finishedUtc = [DateTime]::UtcNow.ToString('o'); scriptExitCode = $LASTEXITCODE } |
        ConvertTo-Json | Out-File -LiteralPath ($prefix + '-completed.json') -Encoding UTF8 -NoClobber
}
catch {
    [ordered]@{
        failedUtc = [DateTime]::UtcNow.ToString('o')
        exception = $_.Exception.ToString()
        errorId = $_.FullyQualifiedErrorId
        position = $_.InvocationInfo.PositionMessage
        scriptStackTrace = $_.ScriptStackTrace
    } | ConvertTo-Json -Depth 5 | Out-File -LiteralPath ($prefix + '-error.json') -Encoding UTF8 -NoClobber
    Write-Error $_ -ErrorAction Continue
    exit 1
}
