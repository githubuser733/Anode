param(
    [Parameter(Mandatory=$true)][string]$OutDirectory,
    [Parameter(Mandatory=$true)][int]$OriginalCodexPid,
    [ValidateRange(1,1800)][int]$DurationSeconds=900
)
$ErrorActionPreference='Stop'
$directory=[IO.Path]::GetFullPath($OutDirectory)
if(-not (Test-Path -LiteralPath $directory -PathType Container)){throw 'Create the evidence directory first.'}
function Write-NewJson([string]$Name,$Value){
    $file=[IO.File]::Open((Join-Path $directory $Name),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    $writer=[IO.StreamWriter]::new($file,[Text.UTF8Encoding]::new($false))
    try{$writer.Write((ConvertTo-Json -InputObject $Value -Depth 8))}finally{$writer.Dispose()}
}
$initial=Get-CimInstance Win32_Process -Filter "ProcessId=$OriginalCodexPid"
if(-not $initial -or $initial.Name -ne 'ChatGPT.exe'){throw 'Expected Codex main process is no longer present.'}
$created=$initial.CreationDate.ToUniversalTime().ToString('o')
$began=(Get-Date).ToUniversalTime()
Write-NewJson 'watch-start.json' ([ordered]@{watcherPid=$PID;originalPid=$OriginalCodexPid;originalCreated=$created;session=$initial.SessionId;beganUtc=$began.ToString('o');durationSeconds=$DurationSeconds})
$file=[IO.File]::Open((Join-Path $directory 'process-watch.jsonl'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
$writer=[IO.StreamWriter]::new($file,[Text.UTF8Encoding]::new($false))
$samples=0;$changed=$false
try{
    $deadline=(Get-Date).AddSeconds($DurationSeconds)
    do{
        $processes=@(Get-CimInstance Win32_Process -Filter "Name='ChatGPT.exe' OR Name='Alpha.exe' OR Name='anode.exe'")
        $same=@($processes|Where-Object {$_.ProcessId -eq $OriginalCodexPid -and $_.CreationDate.ToUniversalTime().ToString('o') -ceq $created})
        $alive=$same.Count -eq 1
        if(-not $alive){$changed=$true}
        $row=[ordered]@{atUtc=(Get-Date).ToUniversalTime().ToString('o');originalCodexAlive=$alive;
            codexMain=@($processes|Where-Object {$_.Name -eq 'ChatGPT.exe' -and $_.CommandLine -notmatch '--type='}|ForEach-Object {@{pid=$_.ProcessId;parent=$_.ParentProcessId;session=$_.SessionId;created=$_.CreationDate.ToUniversalTime().ToString('o')}});
            alphaMain=@($processes|Where-Object {$_.Name -eq 'Alpha.exe' -and $_.CommandLine -notmatch '--type='}|ForEach-Object {@{pid=$_.ProcessId;session=$_.SessionId}});
            anodeHosts=@($processes|Where-Object {$_.Name -eq 'anode.exe' -and $_.CommandLine -match '\s(?:up|__seat-host)(?:\s|$)'}|ForEach-Object {@{pid=$_.ProcessId;session=$_.SessionId}})}
        $writer.WriteLine(($row|ConvertTo-Json -Depth 5 -Compress));$writer.Flush();$samples++
        if((Get-Date) -ge $deadline -or (Test-Path -LiteralPath (Join-Path $directory 'watch.stop'))){break}
        Start-Sleep -Seconds 2
    }while($true)
}finally{$writer.Dispose()}
$events=@()
foreach($log in @('Microsoft-Windows-AppXDeploymentServer/Operational','Microsoft-Windows-AppModel-Runtime/Admin','Application')){
    $ids=if($log -eq 'Application'){@(1000,1001,1002)}elseif($log -like '*AppModel*'){@(201,208,211,212,217)}else{@(603)}
    $events+=@(Get-WinEvent -FilterHashtable @{LogName=$log;StartTime=$began.ToLocalTime();Id=$ids} -ErrorAction SilentlyContinue|Where-Object {$_.Message -match 'OpenAI\.Codex|ChatGPT\.exe'}|ForEach-Object {[ordered]@{time=$_.TimeCreated.ToString('o');log=$log;id=$_.Id;record=$_.RecordId;message=$_.Message;xml=$_.ToXml()}})
}
Write-NewJson 'events-after.json' @($events)
Write-NewJson 'verification.json' ([ordered]@{result=if($changed){'OBSERVED_CODEX_RESTART'}else{'ORIGINAL_CODEX_SURVIVED_OBSERVATION'};originalPid=$OriginalCodexPid;samples=$samples;beganUtc=$began.ToString('o');endedUtc=(Get-Date).ToUniversalTime().ToString('o');requestedDurationSeconds=$DurationSeconds;originalChanged=$changed;newCodexEvents=$events.Count})
