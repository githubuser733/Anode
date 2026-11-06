param()
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$config=Get-Content -LiteralPath (Join-Path $root 'config/anode-chat-reader.json') -Raw -Encoding UTF8|ConvertFrom-Json
$exe=Join-Path $root $config.executable
$source=Join-Path $root $config.sourceDirectory
if((Get-FileHash -LiteralPath $exe).Hash.ToLowerInvariant() -ne $config.sha256){throw 'Reader executable hash differs.'}
$out=Join-Path (Join-Path $root $config.evidenceDirectory) ('reader-verify-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $out|Out-Null
$text=& $exe --channel text-validation __text-check 2>&1|Out-String
$code=$LASTEXITCODE
$text|Out-File -LiteralPath (Join-Path $out 'text.log') -Encoding UTF8 -NoClobber
$text
if($code -ne 0){throw 'Reader checks failed; evidence was retained.'}
$text=& $exe --channel text-validation __timeline-check --out (Join-Path $out 'timeline') 2>&1|Out-String
$code=$LASTEXITCODE
$text|Out-File -LiteralPath (Join-Path $out 'timeline.log') -Encoding UTF8 -NoClobber
$text
if($code -ne 0){throw 'Timeline checks failed; evidence was retained.'}
& python.exe (Join-Path $source 'scripts/test-text-protocol.py') --exe $exe --baseline-exe (Join-Path $root $config.baselineExecutable) --out (Join-Path $out 'protocol')
if($LASTEXITCODE -ne 0){throw 'Protocol checks failed.'}
& (Join-Path $source 'scripts/check-docs.ps1') -Anode $exe
[ordered]@{status='ANODE_CHAT_READER_CANDIDATE_OK';executable=$exe;sha256=$config.sha256;viewerReplaced=$false;liveChatReadPerformed=$false}|ConvertTo-Json|Out-File -LiteralPath (Join-Path $out 'verification.json') -Encoding UTF8 -NoClobber
'ANODE_CHAT_READER_CANDIDATE_OK'
'Evidence: '+$out
