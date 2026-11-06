$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$config=Get-Content -LiteralPath (Join-Path $root 'config/anode-local-mcp.json') -Raw -Encoding UTF8|ConvertFrom-Json
$exe=[IO.Path]::GetFullPath((Join-Path $root $config.executable))
if((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ine $config.sha256){throw 'ACTIVE_ANODE_HASH_MISMATCH'}
$text=& $exe --channel main status --json|Out-String
if($LASTEXITCODE -ne 0){throw 'ANODE_STATUS_FAILED'}
$seat=$text|ConvertFrom-Json
if($seat.state -ne 'ready' -or !$seat.loginComplete){throw 'ANODE_NOT_READY'}
if($seat.seat.screen.width -ne 1920 -or $seat.seat.screen.height -ne 1080){throw 'DISPLAY_NOT_1920x1080'}
'DISPLAY_OK_1920x1080'
$runtime='F:\AI infrastructure\agent-Windows-operation\anode\operations\work\recheck-20261031-shared\workbuddy-electron'
$receipt=$null
foreach($file in (Get-ChildItem -LiteralPath $runtime -Filter 'anode-shared-launch*.json' -File|Sort-Object LastWriteTime -Descending)){
 $candidate=Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8|ConvertFrom-Json
 if($candidate.sharedConfig -ne 'C:\Users\Zhang\.workbuddy' -or $candidate.session -ne $seat.session){continue}
 $running=Get-Process -Id $candidate.pid -ErrorAction SilentlyContinue
 if($running -and $running.SessionId -eq $seat.session -and $running.ProcessName -eq 'WorkBuddy' -and $running.Path -eq 'C:\Users\Zhang\AppData\Local\Programs\WorkBuddy\WorkBuddy.exe'){
  $receipt=$candidate;break
 }
}
if($null -eq $receipt){throw 'NO_LIVE_SHARED_WORKBUDDY_RECEIPT'}
if($receipt.sharedConfig -ne 'C:\Users\Zhang\.workbuddy'){throw 'SHARED_CONFIG_MISMATCH'}
'SHARED_CONFIG_OK'
$child=Get-Process -Id $receipt.pid -ErrorAction Stop
if($child.SessionId -ne $seat.session -or $child.ProcessName -ne 'WorkBuddy'){throw 'CHILD_PROCESS_MISMATCH'}
$parent=@(Get-Process -Name WorkBuddy -ErrorAction Stop|Where-Object SessionId -eq $seat.parentSession)
if($parent.Count -eq 0){throw 'PARENT_WORKBUDDY_NOT_RUNNING'}
'CONCURRENT_SESSIONS_OK parent='+$seat.parentSession+' child='+$seat.session
$journal='C:\Users\Zhang\.workbuddy\projects\c-Users-Zhang-WorkBuddy-2026-10-31-10-35-58\47700063-fb1b-4326-808a-8aa078ab036b.jsonl'
$found=$false
foreach($line in (Get-Content -LiteralPath $journal -Encoding UTF8)){
 if(!$line.Trim()){continue}
 $row=$line|ConvertFrom-Json
 if($row.role -eq 'assistant'){
  foreach($part in $row.content){
   if($part.text -is [string] -and $part.text.Trim() -ceq 'ANODE_SHARED_WORKBUDDY_OK'){$found=$true}
  }
 }
}
if(!$found){throw 'EXPECTED_SHARED_ASSISTANT_REPLY_MISSING'}
'SHARED_ASSISTANT_REPLY_OK'
'VERIFY_SHARED_WORKBUDDY_OK checks=4'
