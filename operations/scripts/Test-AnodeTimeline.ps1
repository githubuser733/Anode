param([switch]$Preview)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$config=Get-Content -LiteralPath (Join-Path $root 'config\anode-timeline-candidate.json') -Raw -Encoding UTF8|ConvertFrom-Json
$exe=Join-Path $root $config.executable
if(!(Test-Path -LiteralPath $exe -PathType Leaf)){throw 'Timeline candidate build is missing.'}
$evidence=Join-Path (Join-Path $root $config.evidenceDirectory) ('verify-'+[Guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $evidence)
$checks=Join-Path $evidence 'checks'
$result=& $exe __timeline-check --out $checks 2>&1|Out-String
$code=$LASTEXITCODE
$result|Out-File -LiteralPath (Join-Path $evidence 'checks.log') -Encoding UTF8 -NoClobber
$result
if($code -ne 0){throw 'Timeline checks failed; evidence was retained.'}
& python.exe (Join-Path $PSScriptRoot 'Test-AnodeTimelineProtocol.py') --exe $exe --out (Join-Path $evidence 'protocol')
if($LASTEXITCODE -ne 0){throw 'MCP discovery differs from the original release.'}
if($Preview){
    $result=& $exe __viewer-preview --timeline --out (Join-Path $evidence 'preview') 2>&1|Out-String
    $code=$LASTEXITCODE
    $result|Out-File -LiteralPath (Join-Path $evidence 'preview.log') -Encoding UTF8 -NoClobber
    $result
    if($code -ne 0){throw 'Timeline preview failed; evidence was retained.'}
}
[ordered]@{status='ANODE_TIMELINE_OFFLINE_OK';executable=$exe;sha256=(Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash;preview=[bool]$Preview;liveSeatStarted=$false;time=(Get-Date).ToString('o')}|
    ConvertTo-Json|Out-File -LiteralPath (Join-Path $evidence 'verification.json') -Encoding UTF8 -NoClobber
'ANODE_TIMELINE_OFFLINE_OK'
'Evidence: '+$evidence
