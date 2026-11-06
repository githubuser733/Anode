param([switch]$Preview)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$config = Get-Content -LiteralPath (Join-Path $root 'config\anode-activity-candidate.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$exe = Join-Path $root $config.executable
if (!(Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'Activity candidate is missing.' }
$out = Join-Path (Join-Path $root $config.evidenceDirectory) ('verify-publish-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $out | Out-Null
$checks = & $exe __timeline-check --out (Join-Path $out 'checks') 2>&1 | Out-String
$code = $LASTEXITCODE
$checks | Out-File -LiteralPath (Join-Path $out 'checks.log') -Encoding UTF8 -NoClobber
$checks
if ($code -ne 0) { throw 'Activity checks failed; all evidence was retained.' }
& python.exe (Join-Path $PSScriptRoot 'Test-AnodeTimelineProtocol.py') --exe $exe --out (Join-Path $out 'protocol')
if ($LASTEXITCODE -ne 0) { throw 'MCP discovery check failed.' }
if ($Preview) {
    $previewOutput = & $exe __viewer-preview --timeline --out (Join-Path $out 'preview') 2>&1 | Out-String
    $code = $LASTEXITCODE
    $previewOutput | Out-File -LiteralPath (Join-Path $out 'preview.log') -Encoding UTF8 -NoClobber
    if ($code -ne 0) { throw 'Activity layout preview failed.' }
}
@{status='ANODE_ACTIVITY_CANDIDATE_OK';executable=$exe;sha256=(Get-FileHash -LiteralPath $exe).Hash;liveDaemonReplaced=$false;time=(Get-Date).ToString('o')} |
    ConvertTo-Json | Out-File -LiteralPath (Join-Path $out 'verification.json') -Encoding UTF8 -NoClobber
'ANODE_ACTIVITY_CANDIDATE_OK'
'Evidence: '+$out
