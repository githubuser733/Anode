param([switch]$CheckOnly)
$ErrorActionPreference='Stop'
$ops=Split-Path -Parent $PSScriptRoot
$repo=Split-Path -Parent $ops
$layout=Get-Content -LiteralPath (Join-Path $ops 'config\launcher-layout.json') -Raw -Encoding UTF8|ConvertFrom-Json
if($layout.main -cne 'Anode.lnk'){throw 'UNEXPECTED_MAIN_ENTRY'}
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
'LAUNCHERS_OK count='+@($layout.entries).Count
