param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$config = Get-Content -LiteralPath (Join-Path $root 'config/anode-text-candidate.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$exe = Join-Path $root $config.executable
$source = Join-Path $root $config.sourceDirectory
$baseline = Join-Path $root $config.baselineExecutable
if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() -ne $config.sha256) { throw 'Candidate hash differs from the recorded build.' }
$out = Join-Path (Join-Path $root $config.evidenceDirectory) ('verify-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $out | Out-Null
function Check([string]$Name, [scriptblock]$Command) {
    $lines = New-Object 'System.Collections.Generic.List[string]'
    $failed = $false
    try { & $Command 2>&1 | ForEach-Object { $lines.Add([string]$_) } }
    catch { $failed = $true; $lines.Add(($_ | Out-String)) }
    $lines | Out-File -LiteralPath (Join-Path $out ($Name + '.log')) -Encoding UTF8 -NoClobber
    $lines
    if ($failed) { throw ($Name + ' check failed; evidence was retained at ' + $out) }
}
Check 'text' { & $exe --channel text-validation __text-check 2>&1 | Out-String; if ($LASTEXITCODE -ne 0) { throw 'Text checks failed.' } }
Check 'timeline' { & $exe --channel text-validation __timeline-check --out (Join-Path $out 'timeline') 2>&1 | Out-String; if ($LASTEXITCODE -ne 0) { throw 'Timeline checks failed.' } }
Check 'protocol' {
    & python.exe (Join-Path $source 'scripts/test-text-protocol.py') --exe $exe --baseline-exe $baseline --out (Join-Path $out 'protocol')
    if ($LASTEXITCODE -ne 0) { throw 'Protocol checks failed.' }
}
Check 'docs' { & (Join-Path $source 'scripts/check-docs.ps1') -Anode $exe }
[ordered]@{status='ANODE_TEXT_CANDIDATE_OK';executable=$exe;sha256=$config.sha256;activated=$false;wechatLiveTested=$false} |
    ConvertTo-Json | Out-File -LiteralPath (Join-Path $out 'verification.json') -Encoding UTF8 -NoClobber
'ANODE_TEXT_CANDIDATE_OK'
'Evidence: ' + $out
