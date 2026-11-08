$ErrorActionPreference='Stop'
$ops=Split-Path -Parent $PSScriptRoot
$parent=Split-Path -Parent (Split-Path -Parent $ops)
$evidence=Join-Path $ops 'artifacts\restructure-20261106'
$backup=Join-Path $evidence 'shortcuts-before'
[IO.Directory]::CreateDirectory($backup)|Out-Null
function Map-PathText([string]$value){
 if(-not $value){return $value}
 if($value.Contains($ops) -or $value.Contains((Join-Path $parent 'ghost\local'))){return $value}
 return $value.Replace($parent,$ops).Replace($parent.Replace('\','/'),$ops.Replace('\','/'))
}
$taskResults=@()
foreach($task in @(Get-ScheduledTask|Where-Object TaskName -like 'AgentWindowsOperation*')){
 $changed=$false
 foreach($action in $task.Actions){
  foreach($property in @('Execute','Arguments','WorkingDirectory')){
   $old=$action.$property;$new=Map-PathText $old
   if($old -cne $new){$action.$property=$new;$changed=$true}
  }
 }
 if($changed){Set-ScheduledTask -InputObject $task|Out-Null}
 $actual=Get-ScheduledTask -TaskName $task.TaskName
 $taskResults+=[pscustomobject]@{name=$actual.TaskName;changed=$changed;enabled=$actual.Settings.Enabled;actions=@($actual.Actions|Select-Object Execute,Arguments,WorkingDirectory)}
}
$shell=New-Object -ComObject WScript.Shell
$places=@((Join-Path $ops 'launchers'),[Environment]::GetFolderPath('Desktop'),(Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu'),(Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar'))
$links=@()
foreach($place in $places){if(Test-Path -LiteralPath $place){$links+=Get-ChildItem -LiteralPath $place -Filter '*.lnk' -File -Recurse}}
$results=@()
foreach($file in $links|Sort-Object FullName -Unique){
 $link=$shell.CreateShortcut($file.FullName);$edits=@{}
 foreach($property in @('TargetPath','Arguments','WorkingDirectory','IconLocation')){
  $old=$link.$property;$new=Map-PathText $old
  if($old -cne $new){$edits[$property]=$new}
 }
 if($edits.Count){
  $saved=Join-Path $backup (([Guid]::NewGuid().ToString('N'))+'.lnk')
  Copy-Item -LiteralPath $file.FullName -Destination $saved
  foreach($property in $edits.Keys){$link.$property=$edits[$property]}
  $link.Save()
  $actual=$shell.CreateShortcut($file.FullName)
  foreach($property in $edits.Keys){if($actual.$property -cne $edits[$property]){throw ('SHORTCUT_READBACK_FAILED: '+$file.FullName)}}
  $results+=[pscustomobject]@{path=$file.FullName;backup=$saved;target=$actual.TargetPath;arguments=$actual.Arguments;workingDirectory=$actual.WorkingDirectory}
 }
}
$result=[pscustomobject]@{status='ENTRY_MIGRATION_VERIFIED';tasks=$taskResults;shortcuts=$results}
$result|ConvertTo-Json -Depth 8|Out-File -LiteralPath (Join-Path $evidence 'entries-after.json') -Encoding utf8 -NoClobber
[pscustomobject]@{status=$result.status;tasks=$taskResults.Count;shortcuts=$results.Count}|ConvertTo-Json
