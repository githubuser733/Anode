param([Parameter(Mandatory=$true)][DateTimeOffset]$Since)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$config=Get-Content -LiteralPath (Join-Path $root 'config\anode-timeline-candidate.json') -Raw -Encoding UTF8|ConvertFrom-Json
$journalDirectory=Join-Path (Join-Path $root $config.stateDirectory) 'timeline'
$events=@{}
foreach($file in Get-ChildItem -LiteralPath $journalDirectory -Filter '*.jsonl' -File){
    foreach($line in Get-Content -LiteralPath $file.FullName -Encoding UTF8){
        if([string]::IsNullOrWhiteSpace($line)){continue}
        try {$entry=$line|ConvertFrom-Json} catch {throw "Unreadable journal row in $($file.FullName): $($_.Exception.Message)"}
        if([DateTimeOffset]::Parse([string]$entry.at) -lt $Since){continue}
        if(!$events.ContainsKey($entry.id) -or [long]$entry.sequence -gt [long]$events[$entry.id].sequence){$events[$entry.id]=$entry}
    }
}
$human=@($events.Values|Where-Object actor -eq 'Human')
$agent=@($events.Values|Where-Object actor -eq 'Agent')
$clicks=@($human|Where-Object operation -eq 'input.click').Count
$text=@($human|Where-Object operation -eq 'input.text').Count
$takes=@($human|Where-Object operation -eq 'control.take').Count
$releases=@($human|Where-Object operation -eq 'control.release').Count
[ordered]@{since=$Since.ToString('o');humanClicks=$clicks;humanText=$text;takeControl=$takes;releaseControl=$releases;agentEntries=$agent.Count}|ConvertTo-Json
if($clicks -lt 1 -or $text -lt 1 -or $takes -lt 1 -or $releases -lt 1){throw 'ANODE_TIMELINE_LIVE_METADATA_INCOMPLETE: expected real Human click, text and control boundaries after -Since.'}
'ANODE_TIMELINE_LIVE_METADATA_OK (journal coverage only; verify actual app content and Viewer separately)'
