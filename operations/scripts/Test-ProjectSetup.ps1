param([switch]$Live)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$config = Get-Content -LiteralPath (Join-Path $root 'config\verified-desktop.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($relative in @($config.anodeExecutable, $config.anodeGuide, $config.workBuddyLauncher, 'scripts\mcp_live_bridge.py', 'scripts\internal\live_call.ps1', 'README.txt', 'docs\操作说明.txt')) {
    if (!(Test-Path -LiteralPath (Join-Path $root $relative) -PathType Leaf)) { throw "Missing file: $relative" }
}
foreach ($file in @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' -File -Recurse)) {
    $tokens = $null
    $parseErrors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count -gt 0) { throw ($file.FullName+': '+($parseErrors.Message -join '; ')) }
}
'POWERSHELL_SYNTAX_OK'
& python.exe -c "import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8-sig')); print('PYTHON_SYNTAX_OK')" (Join-Path $PSScriptRoot 'mcp_live_bridge.py')
if ($LASTEXITCODE -ne 0) { throw 'Python syntax check failed.' }
& (Join-Path $PSScriptRoot 'Initialize-ProjectLaunchers.ps1') -CheckOnly
$active = Get-Content -LiteralPath (Join-Path $root 'artifacts\active-source-migration.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($entry in $active) {
    if ((Get-FileHash -LiteralPath $entry.destination -Algorithm SHA256).Hash -ine $entry.destinationSha256) { throw "Script hash mismatch: $($entry.destination)" }
}
'ACTIVE_SCRIPT_HASHES_OK count='+@($active).Count
$manifest = Get-Content -LiteralPath (Join-Path $root 'artifacts\migration-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$bytes = [long]0
foreach ($entry in $manifest.files) {
    $path = Join-Path $manifest.destinationRoot $entry.relativePath
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $entry.sha256) { throw "Archive hash mismatch: $path" }
    $bytes += (Get-Item -LiteralPath $path).Length
}
if (@($manifest.files).Count -ne $manifest.fileCount -or $bytes -ne $manifest.totalBytes) { throw 'Archive inventory mismatch.' }
'ARCHIVE_HASHES_OK files='+$manifest.fileCount+' bytes='+$bytes
$legacyPrefix = '\Documents\'+'Codex\'
foreach ($file in @((Get-Item -LiteralPath (Join-Path $root 'config\verified-desktop.json'))) + @(Get-ChildItem -LiteralPath $PSScriptRoot -File -Recurse)) {
    if ((Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8).Contains($legacyPrefix)) { throw "Legacy output dependency: $($file.FullName)" }
}
'PROJECT_PATHS_OK'
$retention=Get-Content -LiteralPath (Join-Path $root 'config\runtime-retention.json') -Raw -Encoding UTF8|ConvertFrom-Json
$activeRuntime=Get-Content -LiteralPath (Join-Path $root 'config\anode-local-mcp.json') -Raw -Encoding UTF8|ConvertFrom-Json
if($retention.policy -cne 'current-only' -or $retention.executable -cne $activeRuntime.executable){throw 'Expected single current Anode retention policy'}
if(@($retention.retiredDirectories).Count -ne 1 -or $retention.retiredDirectories[0] -cne 'work/anode-screenshot-viewer-20261106-734d2b'){throw 'Retired runtime inventory differs'}
foreach($retired in $retention.retiredDirectories){if(Test-Path -LiteralPath (Join-Path $root $retired)){throw ('Retired Anode runtime still exists: '+$retired)}}
$runtimeCopies=@(Get-ChildItem -LiteralPath (Join-Path $root 'work') -Directory -Filter 'anode-*'|ForEach-Object {Get-ChildItem -LiteralPath $_.FullName -Recurse -Filter 'anode.exe' -File})
if($runtimeCopies.Count -ne 1 -or $runtimeCopies[0].FullName -ine (Join-Path $root $activeRuntime.executable)){throw 'Expected exactly one current Anode runtime executable'}
'ANODE_SINGLE_CURRENT_RUNTIME_OK'
if ($Live) { & (Join-Path $PSScriptRoot 'Verify-Shared-WorkBuddy.ps1') }
'PROJECT_SETUP_OK'
