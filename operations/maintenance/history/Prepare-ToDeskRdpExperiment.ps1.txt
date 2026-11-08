#requires -Version 5.1
<#+
Creates an encrypted backup and a one-byte candidate change for the existing
ToDesk RDP option. It never writes the installed configuration, stops services,
starts applications, or changes a Windows session. Live behavior is UNTESTED.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$configPath = 'C:\Program Files\ToDesk\config.ini'
$executable = 'C:\Program Files\ToDesk\ToDesk.exe'

function Get-BytesSha256([byte[]]$Bytes) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hash.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant() }
    finally { $hash.Dispose() }
}

function Write-NewBytes([string]$Path, [byte[]]$Bytes) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($Bytes, 0, $Bytes.Length); $stream.Flush() }
    finally { $stream.Dispose() }
}

Add-Type -AssemblyName System.Security
$original = [IO.File]::ReadAllBytes($configPath)
$originalHash = Get-BytesSha256 $original
# Latin-1 provides one character per byte, so the ASCII option can be changed
# without decoding or re-encoding any other configuration or account data.
$text = [Text.Encoding]::GetEncoding(28591).GetString($original)
$entries = [regex]::Matches($text, '(?m)^[ \t]*rdpOptimization[ \t]*=[^\r\n]*')
if ($entries.Count -ne 1) { throw 'Expected exactly one rdpOptimization entry; no installed file was changed.' }
$entry = $entries[0]
$valueMatch = [regex]::Match($entry.Value, '^([ \t]*rdpOptimization[ \t]*=[ \t]*)(?<value>[01])[ \t]*$')
if (-not $valueMatch.Success) { throw 'Unexpected RDP option format; no installed file was changed.' }
$sections = [regex]::Matches($text.Substring(0, $entry.Index), '(?m)^[ \t]*\[(?<name>[^\]\r\n]+)\][ \t]*\r?$')
if ($sections.Count -eq 0 -or $sections[$sections.Count - 1].Groups['name'].Value -ne 'ConfigInfo') {
    throw 'RDP option is not in the verified ConfigInfo section; no installed file was changed.'
}
if ($valueMatch.Groups['value'].Value -ne '1') {
    throw 'The current RDP option is no longer 1. Reassess the live state before preparing a new experiment.'
}

$offset = $entry.Index + $valueMatch.Groups['value'].Index
$candidate = [byte[]]$original.Clone()
$candidate[$offset] = [byte][char]'0'
$differentBytes = 0
for ($index = 0; $index -lt $original.Length; $index++) {
    if ($original[$index] -ne $candidate[$index]) { $differentBytes++ }
}
if ($differentBytes -ne 1 -or $original[$offset] -ne 49 -or $candidate[$offset] -ne 48) {
    throw 'Candidate must change exactly the ASCII value 1 to 0.'
}

$evidence = Join-Path (Join-Path $projectRoot 'artifacts') ('todesk-rdp-experiment-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $evidence | Out-Null
$scope = [Security.Cryptography.DataProtectionScope]::CurrentUser
Write-NewBytes (Join-Path $evidence 'original-config.dpapi') ([Security.Cryptography.ProtectedData]::Protect($original, $null, $scope))
Write-NewBytes (Join-Path $evidence 'candidate-config.dpapi') ([Security.Cryptography.ProtectedData]::Protect($candidate, $null, $scope))
$restoredOriginal = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes((Join-Path $evidence 'original-config.dpapi')), $null, $scope)
$restoredCandidate = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes((Join-Path $evidence 'candidate-config.dpapi')), $null, $scope)
$candidateHash = Get-BytesSha256 $candidate
if ((Get-BytesSha256 $restoredOriginal) -ne $originalHash -or (Get-BytesSha256 $restoredCandidate) -ne $candidateHash) {
    throw 'Encrypted backup/candidate round-trip verification failed. Installed configuration was not changed.'
}
if ((Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $originalHash) {
    throw 'ToDesk configuration changed during preparation. Do not apply this candidate; evidence has been retained.'
}

$manifest = [ordered]@{
    schemaVersion = 1
    preparedAtUtc = [DateTime]::UtcNow.ToString('o')
    targetPath = $configPath
    executablePath = $executable
    executableVersion = (Get-Item -LiteralPath $executable).VersionInfo.FileVersion
    executableSha256 = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    section = 'ConfigInfo'
    option = 'rdpOptimization'
    originalValue = 1
    candidateValue = 0
    byteOffset = $offset
    differentBytes = $differentBytes
    byteLength = $original.Length
    originalSha256 = $originalHash
    candidateSha256 = $candidateHash
    protection = 'Windows DPAPI CurrentUser'
    applied = $false
    liveBehaviorTested = $false
    evidenceBoundary = 'The option and RDP-related executable strings are verified; disabling it has not been shown to restore the physical console.'
}
$manifestPath = Join-Path $evidence 'proposal.json'
$manifest | ConvertTo-Json -Depth 4 | Out-File -LiteralPath $manifestPath -Encoding UTF8 -NoClobber
$readback = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($readback.applied -ne $false -or $readback.originalSha256 -ne $originalHash -or $readback.differentBytes -ne 1) {
    throw 'Proposal readback verification failed.'
}
'RDP_CANDIDATE_OK applied=false differentBytes=1 originalUnchanged=true encryptedRoundTrip=true liveTested=false'
'PROPOSAL: ' + $manifestPath
