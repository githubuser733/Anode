param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$config = Get-Content -LiteralPath (Join-Path $root 'config\multiopen-apps.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$target = (Get-Command powershell.exe).Source
$script = Join-Path $PSScriptRoot 'Start-AppInAnode.ps1'
$shell = New-Object -ComObject WScript.Shell
foreach ($entry in $config.apps.PSObject.Properties) {
    $spec = $entry.Value
    $path = Join-Path (Join-Path $root 'launchers') $spec.shortcut
    $arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$script+'" -App '+$entry.Name+' -ShowErrors'
    if (!(Test-Path -LiteralPath $spec.executable)) { throw "Application missing: $($spec.executable)" }
    if (!(Test-Path -LiteralPath $path)) {
        if ($CheckOnly) { throw "Shortcut missing: $path" }
        $shortcut = $shell.CreateShortcut($path)
        $shortcut.TargetPath = $target
        $shortcut.Arguments = $arguments
        $shortcut.WorkingDirectory = $root
        $shortcut.WindowStyle = 7
        $shortcut.IconLocation = $spec.executable + ',0'
        $shortcut.Description = 'Open inside Anode: '+$entry.Name+' with a separate desktop runtime'
        $shortcut.Save()
    }
    $actual = $shell.CreateShortcut($path)
    if ($actual.TargetPath -ine $target -or $actual.Arguments -cne $arguments -or $actual.WorkingDirectory -ine $root -or $actual.IconLocation -ine ($spec.executable+',0')) {
        throw "Shortcut differs; it was not overwritten: $path"
    }
}
'MULTIOPEN_SHORTCUTS_OK count=3'
