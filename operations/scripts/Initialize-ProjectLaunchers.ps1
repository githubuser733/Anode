param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$config = Get-Content -LiteralPath (Join-Path $root 'config\verified-desktop.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$anode = Join-Path $root $config.anodeExecutable
$workBuddy = Join-Path $root $config.workBuddyLauncher
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
foreach ($file in @($anode, $workBuddy)) {
    if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing file: $file" }
}
$specs = @(
    @{Name='Anode-Start-1920x1080-Clipboard.lnk'; Target=$anode; Arguments=($config.anodeStartupArguments -join ' '); Description='Start Anode at 1920x1080 with shared clipboard'},
    @{Name='Anode-Show.lnk'; Target=$anode; Arguments=('--channel '+$config.anodeChannel+' show'); Description='Show the existing Anode viewer'},
    @{Name='Anode-Hide.lnk'; Target=$anode; Arguments=('--channel '+$config.anodeChannel+' hide'); Description='Hide the viewer and keep the child session running'},
    @{Name='WorkBuddy-Shared-In-Anode.lnk'; Target=$powershell; Arguments=('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$workBuddy+'" -Variant '+$config.workBuddyVariant+' -ShowErrors'); Description='Open inside Anode: shared WorkBuddy config and separate Electron runtime'}
)
$folder = Join-Path $root 'launchers'
if (!(Test-Path -LiteralPath $folder -PathType Container)) {
    if ($CheckOnly) { throw "Missing folder: $folder" }
    New-Item -ItemType Directory -Path $folder -ErrorAction Stop | Out-Null
}
$shell = New-Object -ComObject WScript.Shell
foreach ($spec in $specs) {
    $path = Join-Path $folder $spec.Name
    if (!(Test-Path -LiteralPath $path)) {
        if ($CheckOnly) { throw "Missing shortcut: $path" }
        $shortcut = $shell.CreateShortcut($path)
        $shortcut.TargetPath = $spec.Target
        $shortcut.Arguments = $spec.Arguments
        $shortcut.WorkingDirectory = $root
        $shortcut.Description = $spec.Description
        $shortcut.Save()
    }
    $actual = $shell.CreateShortcut($path)
    if ($actual.TargetPath -ine $spec.Target -or $actual.Arguments -cne $spec.Arguments -or $actual.WorkingDirectory -ine $root) {
        throw "Shortcut differs from config; existing file was not overwritten: $path"
    }
}
'LAUNCHERS_OK count='+$specs.Count
