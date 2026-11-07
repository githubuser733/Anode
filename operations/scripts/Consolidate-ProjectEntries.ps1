$ErrorActionPreference='Stop'
$ops=Split-Path -Parent $PSScriptRoot
$anode=Split-Path -Parent $ops
$parent=Split-Path -Parent $anode
$ghost=Join-Path $parent 'ghost'
$evidence=Join-Path $ops 'artifacts\entrypoints-20261107'
$archive=Join-Path $evidence 'retired-launchers'
if((Test-Path -LiteralPath (Join-Path $evidence 'entry-moves.json')) -or (Test-Path -LiteralPath (Join-Path $ops 'config\launcher-layout.json'))){
 'ENTRY_MIGRATION_ALREADY_APPLIED; use Initialize-ProjectLaunchers.ps1 -CheckOnly to verify the current layout'
 return
}
$shell=New-Object -ComObject WScript.Shell
$pythonw=(Get-Content -LiteralPath (Join-Path $ops 'config\wechat-dashboard.json') -Raw|ConvertFrom-Json).pythonwExecutable
$anodeCfg=Get-Content -LiteralPath (Join-Path $ops 'config\anode-local-mcp.json') -Raw|ConvertFrom-Json
$main=@(
 @{Path=(Join-Path $anode 'Anode.lnk');Target=$pythonw;Args=('-X utf8 -B "'+(Join-Path $ops 'scripts\anode_main.py')+'"');Cwd=$ops;Icon=((Join-Path $ops $anodeCfg.executable)+',0');Description='Anode - open or start the verified current version'},
 @{Path=(Join-Path $ghost 'Ghost.lnk');Target=(Join-Path $ghost 'local\panel\GhostPanel.exe');Args='';Cwd=(Join-Path $ghost 'local');Icon=((Join-Path $ghost 'local\panel\GhostPanel.exe')+',0');Description='Ghost - local background operation control panel'}
)
foreach($entry in $main){
 if(Test-Path -LiteralPath $entry.Path){
  $current=$shell.CreateShortcut($entry.Path)
  if([IO.Path]::GetFullPath($current.TargetPath) -ine [IO.Path]::GetFullPath($entry.Target) -or $current.Arguments -cne $entry.Args -or [IO.Path]::GetFullPath($current.WorkingDirectory) -ine [IO.Path]::GetFullPath($entry.Cwd)){throw ('ENTRY_ALREADY_EXISTS_WITH_DIFFERENT_TARGET: '+$entry.Path)}
  continue
 }
 if(-not (Test-Path -LiteralPath $entry.Target)){throw 'ENTRY_TARGET_MISSING'}
 $link=$shell.CreateShortcut($entry.Path);$link.TargetPath=$entry.Target;$link.Arguments=$entry.Args;$link.WorkingDirectory=$entry.Cwd;$link.IconLocation=$entry.Icon;$link.Description=$entry.Description;$link.Save()
}
$moves=@()
$old=Join-Path $ops 'launchers'
foreach($file in @(Get-ChildItem -LiteralPath $old -File)){
 if($file.Name -like 'Anode-*' -or $file.Name -like 'Ghost-*' -or $file.Name -in @('微信群关键词监控-启动.lnk','微信群关键词监控-停止.lnk')){$target=Join-Path $archive ('anode\'+$file.Name)}
 elseif($file.Extension -eq '.cmd'){$target=Join-Path $old ('maintenance\'+$file.Name)}
 elseif($file.Name -match '^(企微|微信群|群消息)'){$target=Join-Path $old ('monitoring\'+$file.Name)}
 else{$target=Join-Path $old ('apps\'+$file.Name)}
 $moves+=@{source=$file.FullName;destination=$target;sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash;bytes=$file.Length}
}
foreach($file in @(Get-ChildItem -LiteralPath (Join-Path $ghost 'local\launchers') -File)){
 $moves+=@{source=$file.FullName;destination=(Join-Path $ghost ('local\artifacts\entrypoints-20261107\retired-launchers\'+$file.Name));sha256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash;bytes=$file.Length}
}
foreach($move in $moves){
 $src=[IO.Path]::GetFullPath($move.source);$dst=[IO.Path]::GetFullPath($move.destination)
 if(-not $src.StartsWith($parent+'\',[StringComparison]::OrdinalIgnoreCase) -or -not $dst.StartsWith($parent+'\',[StringComparison]::OrdinalIgnoreCase) -or $src -eq $parent){throw 'ENTRY_MOVE_SCOPE_REJECTED'}
 if(Test-Path -LiteralPath $dst){throw ('DESTINATION_EXISTS: '+$dst)}
 [IO.Directory]::CreateDirectory((Split-Path -Parent $dst))|Out-Null
 Move-Item -LiteralPath $src -Destination $dst
 if(Test-Path -LiteralPath $src){throw 'ORIGINAL_ENTRY_REMAINS'}
 if((Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash -cne $move.sha256){throw 'MOVED_ENTRY_HASH_CHANGED'}
}
$moves|ConvertTo-Json -Depth 5|Out-File -LiteralPath (Join-Path $evidence 'entry-moves.json') -Encoding utf8 -NoClobber
# CMD tools moved down one directory; preserve their original bytes before editing.
foreach($file in Get-ChildItem -LiteralPath (Join-Path $old 'maintenance') -Filter '*.cmd' -File){
 $copy=Join-Path $evidence ('before-maintenance\'+$file.Name);[IO.Directory]::CreateDirectory((Split-Path -Parent $copy))|Out-Null
 Copy-Item -LiteralPath $file.FullName -Destination $copy
 $content=[IO.File]::ReadAllText($file.FullName).Replace('%~dp0..\scripts\','%~dp0..\..\scripts\')
 [IO.File]::WriteAllText($file.FullName,$content,[Text.Encoding]::ASCII)
}
$entries=@()
$currentLinks=@(Get-Item -LiteralPath (Join-Path $anode 'Anode.lnk'))+@(Get-ChildItem -LiteralPath $old -Filter '*.lnk' -File -Recurse)
foreach($file in $currentLinks){
 $link=$shell.CreateShortcut($file.FullName)
 $entries+=[ordered]@{path=$file.FullName.Substring($anode.Length+1);target=$link.TargetPath;arguments=$link.Arguments;workingDirectory=$link.WorkingDirectory;icon=$link.IconLocation;description=$link.Description}
}
[ordered]@{schemaVersion=1;main='Anode.lnk';entries=$entries}|ConvertTo-Json -Depth 5|Out-File -LiteralPath (Join-Path $ops 'config\launcher-layout.json') -Encoding utf8 -NoClobber
[pscustomobject]@{status='PROJECT_ENTRIES_CONSOLIDATED';moved=$moves.Count;deleted=0;anodeMain=$main[0].Path;ghostMain=$main[1].Path;activeAnodeLinks=$entries.Count}|ConvertTo-Json
