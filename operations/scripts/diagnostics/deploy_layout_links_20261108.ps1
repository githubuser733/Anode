$ErrorActionPreference='Stop'
$ops=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$report=Join-Path $ops 'artifacts\directory-layout-20261108'
$layoutPath=Join-Path $ops 'config\launcher-layout.json'
$backup=Join-Path $report 'launcher-layout.before.json'
if(Test-Path -LiteralPath $backup){throw 'ALREADY_DEPLOYED_OR_REVIEW_BACKUP'}
Copy-Item -LiteralPath $layoutPath -Destination $backup
$layout=Get-Content -LiteralPath $layoutPath -Raw -Encoding UTF8|ConvertFrom-Json
$entries=@(
 @{name='UU-运行记录.lnk';target=(Join-Path $ops 'artifacts\uu-console-task-v1');description='UU 当前任务输出；只打开记录目录，不执行切换'},
 @{name='历史材料.lnk';target=(Join-Path $ops 'artifacts\history');description='已归档测试与应用数据；不是日常程序入口'},
 @{name='目录用途说明.lnk';target=(Join-Path $ops 'docs\目录用途与归档索引-20261108.md');description='当前 work、artifacts 的用途及原路径索引'}
)
foreach($entry in $entries){
 if(-not(Test-Path -LiteralPath $entry.target)){throw ('TARGET_MISSING '+$entry.target)}
 $relative='operations\launchers\maintenance\'+$entry.name
 if(@($layout.entries|Where-Object path -eq $relative).Count){throw 'DUPLICATE_LAYOUT_ENTRY'}
 $layout.entries+= [pscustomobject]@{path=$relative;target=$entry.target;arguments='';workingDirectory=$ops;icon=($env:SystemRoot+'\System32\shell32.dll,3');description=$entry.description}
}
[IO.File]::WriteAllText($layoutPath,($layout|ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false))
& (Join-Path $ops 'scripts\Initialize-ProjectLaunchers.ps1')
if(-not $?){throw 'LAUNCHER_DEPLOY_FAILED'}
'LAYOUT_LINKS_DEPLOYED count=3'
