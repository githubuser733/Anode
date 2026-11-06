param([switch]$CheckOnly)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$config=Get-Content -LiteralPath (Join-Path $root 'config\notion-shared-host.json') -Raw -Encoding UTF8|ConvertFrom-Json
$path=Join-Path (Join-Path $root 'launchers') $config.shortcut
$target=(Get-Command powershell.exe).Source
$arguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $PSScriptRoot 'Start-NotionInAnode.ps1')+'" -ShowErrors'
$shell=New-Object -ComObject WScript.Shell
if (!(Test-Path -LiteralPath $path)) {
    if ($CheckOnly) { throw 'Notion shortcut is missing.' }
    $shortcut=$shell.CreateShortcut($path)
    $shortcut.TargetPath=$target
    $shortcut.Arguments=$arguments
    $shortcut.WorkingDirectory=$root
    $shortcut.WindowStyle=7
    $shortcut.IconLocation=$config.executable+',0'
    $shortcut.Description='Notion in Anode: same host profile; close Notion in other sessions first'
    $shortcut.Save()
}
$actual=$shell.CreateShortcut($path)
if ($actual.TargetPath -ine $target -or $actual.Arguments -cne $arguments -or $actual.WorkingDirectory -ine $root -or $actual.IconLocation -ine ($config.executable+',0')) { throw 'Existing Notion shortcut differs; it was not overwritten.' }
'NOTION_SHARED_HOST_SHORTCUT_OK'
