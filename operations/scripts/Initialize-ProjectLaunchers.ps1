param([switch]$CheckOnly)
$ErrorActionPreference='Stop'
$ops=Split-Path -Parent $PSScriptRoot
$repo=Split-Path -Parent $ops
$layout=Get-Content -LiteralPath (Join-Path $ops 'config\launcher-layout.json') -Raw -Encoding UTF8|ConvertFrom-Json
if($layout.main -cne 'Anode.lnk'){throw 'UNEXPECTED_MAIN_ENTRY'}
$active=Get-Content -LiteralPath (Join-Path $ops 'config\anode-local-mcp.json') -Raw -Encoding UTF8|ConvertFrom-Json
$nativeExe=[IO.Path]::GetFullPath((Join-Path $ops $active.executable))
$main=@($layout.entries|Where-Object path -ceq 'Anode.lnk')
if($main.Count -ne 1 -or $main[0].target -ine $nativeExe -or [IO.Path]::GetExtension($main[0].target) -ine '.exe' -or $main[0].arguments -notmatch '^--channel [a-z0-9-]+ open(?: |$)'){throw 'EXPECTED_NATIVE_ANODE_MAIN_ENTRY'}
if((Get-FileHash -LiteralPath $nativeExe -Algorithm SHA256).Hash -ine $active.sha256){throw 'ACTIVE_ANODE_EXECUTABLE_HASH_CHANGED'}
$shell=New-Object -ComObject WScript.Shell
foreach($entry in $layout.entries){
 $path=[IO.Path]::GetFullPath((Join-Path $repo $entry.path))
 if(-not $path.StartsWith($repo+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'ENTRY_OUTSIDE_REPO'}
 if(-not(Test-Path -LiteralPath $entry.target)){throw ('TARGET_MISSING: '+$entry.target)}
 if(-not(Test-Path -LiteralPath $entry.workingDirectory)){throw 'WORKING_DIRECTORY_MISSING'}
 if(-not(Test-Path -LiteralPath $path)){
  if($CheckOnly){throw ('ENTRY_MISSING: '+$path)}
  [IO.Directory]::CreateDirectory((Split-Path -Parent $path))|Out-Null
  $link=$shell.CreateShortcut($path);$link.TargetPath=$entry.target;$link.Arguments=$entry.arguments;$link.WorkingDirectory=$entry.workingDirectory;$link.IconLocation=$entry.icon;$link.Description=$entry.description;$link.Save()
 }
 $link=$shell.CreateShortcut($path)
 if($link.TargetPath -ine $entry.target -or $link.Arguments -cne $entry.arguments -or $link.WorkingDirectory -ine $entry.workingDirectory -or $link.IconLocation -ine $entry.icon){throw ('ENTRY_DIFFERS: '+$path)}
}
$rootLinks=@(Get-ChildItem -LiteralPath $repo -Filter '*.lnk' -File)
if($rootLinks.Count -ne 1 -or $rootLinks[0].Name -cne 'Anode.lnk'){throw 'EXPECTED_SINGLE_ROOT_ENTRY'}
if(@(Get-ChildItem -LiteralPath (Join-Path $ops 'launchers') -File|Where-Object Extension -in @('.lnk','.cmd')).Count){throw 'LOOSE_LEGACY_ENTRY_FOUND'}
'SINGLE_MAIN_ENTRY_OK Anode.lnk'
'NATIVE_ANODE_MAIN_ENTRY_OK'
'ACTIVE_ANODE_EXECUTABLE_HASH_OK'
'LAUNCHERS_OK count='+@($layout.entries).Count
