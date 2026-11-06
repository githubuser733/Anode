param([switch]$CheckOnly)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$spec=Get-Content -LiteralPath (Join-Path $root 'config\notion-concurrent.json') -Raw -Encoding UTF8|ConvertFrom-Json
$path=Join-Path (Join-Path $root 'launchers') $spec.shortcut
$target=(Get-Command powershell.exe).Source
$arguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $PSScriptRoot 'Start-NotionConcurrentInAnode.ps1')+'" -ShowErrors'
$shell=New-Object -ComObject WScript.Shell
if(!(Test-Path -LiteralPath $path)) {
    if($CheckOnly){throw 'Notion concurrent shortcut is missing.'}
    $shortcut=$shell.CreateShortcut($path)
    $shortcut.TargetPath=$target
    $shortcut.Arguments=$arguments
    $shortcut.WorkingDirectory=$root
    $shortcut.WindowStyle=7
    $shortcut.IconLocation=$spec.executable+',0'
    $shortcut.Description='Notion in Anode with an owned independent runtime and initial host login snapshot'
    $shortcut.Save()
}
$actual=$shell.CreateShortcut($path)
if($actual.TargetPath -ine $target -or $actual.Arguments -cne $arguments -or $actual.WorkingDirectory -ine $root -or $actual.IconLocation -ine ($spec.executable+',0')) {throw 'Existing Notion shortcut differs; it was not overwritten.'}
'NOTION_CONCURRENT_SHORTCUT_OK'
