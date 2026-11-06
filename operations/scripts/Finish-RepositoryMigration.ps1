param([switch]$WaitForClients)
$ErrorActionPreference='Stop'
$ops=Split-Path -Parent $PSScriptRoot
$anode=Split-Path -Parent $ops
$parent=Split-Path -Parent $anode
$oldWork=[IO.Path]::GetFullPath((Join-Path $parent 'work'))
$oldScripts=[IO.Path]::GetFullPath((Join-Path $parent 'scripts'))
$evidence=Join-Path $ops 'artifacts\restructure-20261106'
$receipt=Join-Path $evidence 'deferred-path-migration.json'
if(Test-Path -LiteralPath $receipt){Get-Content -LiteralPath $receipt -Raw;return}
if(Test-Path -LiteralPath $oldWork){
    $unexpected=@(Get-ChildItem -LiteralPath $oldWork -Force | Where-Object Name -ne 'anode-screenshot-viewer-20261106-734d2b')
    if($unexpected.Count){throw 'Old work contains non-MCP entries; finish the reviewed migration first.'}
}
if(Test-Path -LiteralPath $oldScripts){
    $unexpected=@(Get-ChildItem -LiteralPath $oldScripts -Force | Where-Object Name -ne 'anode_local_mcp.py')
    if($unexpected.Count){throw 'Old scripts contains non-MCP entries; finish the reviewed migration first.'}
}
$deadline=(Get-Date).AddHours(24)
do {
    $clients=@(Get-CimInstance Win32_Process | Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($oldWork+'\',[StringComparison]::OrdinalIgnoreCase)) -or
        ($_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -like ('*'+$oldScripts+'\anode_local_mcp.py*'))
    })
    if($clients.Count -eq 0){break}
    if(-not $WaitForClients){[pscustomobject]@{status='WAITING_FOR_OLD_MCP_CLIENTS';processes=@($clients|Select-Object ProcessId,Name)}|ConvertTo-Json -Depth 3;return}
    Start-Sleep -Seconds 10
}while((Get-Date) -lt $deadline)
if($clients.Count -gt 0){throw 'Old MCP clients still running after the bounded wait; no files were moved.'}
$pairs=@(@($oldWork,(Join-Path $evidence 'previous-mcp-runtime')), @($oldScripts,(Join-Path $evidence 'previous-mcp-entry')))
foreach($pair in $pairs){
    $src=[IO.Path]::GetFullPath($pair[0]);$dst=[IO.Path]::GetFullPath($pair[1])
    if(-not $src.StartsWith($parent+'\',[StringComparison]::OrdinalIgnoreCase) -or -not $dst.StartsWith($evidence+'\',[StringComparison]::OrdinalIgnoreCase) -or $src -eq $parent){throw 'Migration scope rejected'}
    if(Test-Path -LiteralPath $src){
        if(Test-Path -LiteralPath $dst){throw ('Destination already exists: '+$dst)}
        Move-Item -LiteralPath $src -Destination $dst
    }
}
$result=[pscustomobject]@{status='OLD_MCP_PATHS_MOVED';at=(Get-Date).ToString('o');root=$parent;oldWorkExists=(Test-Path -LiteralPath $oldWork);oldScriptsExists=(Test-Path -LiteralPath $oldScripts);forcedTermination=$false;deletedFiles=0}
if(Test-Path -LiteralPath $receipt){throw 'Receipt already exists'}
[IO.File]::WriteAllText($receipt,($result|ConvertTo-Json -Depth 4))
$result|ConvertTo-Json -Depth 4
