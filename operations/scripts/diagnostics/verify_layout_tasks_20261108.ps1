$ErrorActionPreference='Stop'
$ops=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$report=Join-Path $ops 'artifacts\directory-layout-20261108'
$plan=Get-Content -LiteralPath (Join-Path $report 'plan-v2.json') -Raw -Encoding UTF8|ConvertFrom-Json
$changed=@()
foreach($old in $plan.live.tasks){
 $now=Get-ScheduledTask -TaskName $old.TaskName -ErrorAction Stop
 $before=@(foreach($action in $old.Actions){
  $fields=@(foreach($name in @('Execute','Arguments','WorkingDirectory')){
   $property=@($action.CimInstanceProperties|Where-Object Name -eq $name)
   if($property.Count -ne 1){throw 'BASELINE_ACTION_PROPERTY_MISSING'}
   [string]$property[0].Value
  })
  $fields -join '|'
 })
 $after=@($now.Actions|ForEach-Object {@($_.Execute,$_.Arguments,$_.WorkingDirectory)-join '|'})
 if(($before-join "`n") -cne ($after-join "`n")){$changed+=$old.TaskName}
}
$processes=@(Get-Process -Id @(277676,295976,135008,241444) -ErrorAction Stop|Select-Object Id,ProcessName)
$status=@{taskActionsUnchanged=($changed.Count -eq 0);taskCount=@($plan.live.tasks).Count;changedTasks=$changed;processes=$processes;supersedes='postcheck.json (invalid comparison of serialized CIM actions)';workDirectories=@(Get-ChildItem -LiteralPath (Join-Path $ops 'work') -Directory).Count;artifactDirectories=@(Get-ChildItem -LiteralPath (Join-Path $ops 'artifacts') -Directory).Count}
$out=Join-Path $report 'postcheck-verified.json'
if(Test-Path -LiteralPath $out){throw 'VERIFIED_POSTCHECK_EXISTS'}
[IO.File]::WriteAllText($out,($status|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
if($changed.Count){throw ('TASK_ACTION_CHANGED '+($changed-join ','))}
'TASK_ACTIONS_UNCHANGED count='+@($plan.live.tasks).Count
'PROTECTED_PROCESSES_CONTINUOUS count='+$processes.Count
$status|ConvertTo-Json -Depth 4
