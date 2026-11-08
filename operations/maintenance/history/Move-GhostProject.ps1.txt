param([Parameter(Mandatory=$true)][string]$PlanPath,[switch]$Apply)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$planFile = (Resolve-Path -LiteralPath $PlanPath -ErrorAction Stop).Path
$plan = Get-Content -LiteralPath $planFile -Raw -Encoding UTF8 | ConvertFrom-Json
$audit = Split-Path -Parent $planFile
$destinationRoot = [System.IO.Path]::GetFullPath((Join-Path $root 'work\ghost'))
if ($plan.projectRoot -ine $root -or $plan.destinationRoot -ine $destinationRoot -or $plan.auditDirectory -ine $audit) { throw 'Plan scope mismatch.' }
if (-not $audit.StartsWith((Join-Path $root 'artifacts')+'\',[System.StringComparison]::OrdinalIgnoreCase)) { throw 'Audit directory is outside this project.' }
if (Test-Path -LiteralPath $destinationRoot) { throw 'Destination already exists; no merge or overwrite is allowed.' }
Add-Type -AssemblyName Microsoft.VisualBasic
foreach ($methodName in @('MoveDirectory','MoveFile')) {
    if ($null -eq [Microsoft.VisualBasic.FileIO.FileSystem].GetMethod($methodName,[type[]]@([string],[string],[bool]))) { throw ('Required move overload unavailable: '+$methodName) }
}
$running = @(Get-CimInstance Win32_Process -ErrorAction Stop)
if (@($running | Where-Object Name -match '^ghost(?:-mcp|-http)?\.exe$').Count -ne 0) { throw 'Ghost is running; migration was not attempted.' }
$anodeBefore = @($running | Where-Object Name -eq 'anode.exe' | Select-Object ProcessId,SessionId,ExecutablePath,CreationDate)
foreach ($move in $plan.moves) {
    $source = (Resolve-Path -LiteralPath $move.source -ErrorAction Stop).Path
    $dest = [System.IO.Path]::GetFullPath($move.destination)
    if ($source -ine $move.source -or (Split-Path -Parent $source) -ine $plan.legacyWorkRoot) { throw ('Source scope mismatch: '+$source) }
    if ((Split-Path -Parent $dest) -ine $destinationRoot -or $dest -ieq $root -or $dest -ieq $destinationRoot) { throw ('Destination scope mismatch: '+$dest) }
    if (Test-Path -LiteralPath $dest) { throw ('Destination collision: '+$dest) }
    $tree = @((Get-Item -LiteralPath $source -Force))
    if ($move.isDirectory) { $tree += @(Get-ChildItem -LiteralPath $source -Force -Recurse -ErrorAction Stop) }
    if (@($tree | Where-Object {$_.Attributes -band [System.IO.FileAttributes]::ReparsePoint}).Count -ne 0) { throw ('Reparse point: '+$source) }
    $files = @($tree | Where-Object {-not $_.PSIsContainer})
    if ($files.Count -ne $move.fileCount) { throw ('Source file count changed: '+$source) }
    foreach ($entry in $move.files) {
        $file = Get-Item -LiteralPath $entry.path -ErrorAction Stop
        if ($file.Length -ne $entry.bytes -or (Get-FileHash -LiteralPath $entry.path -Algorithm SHA256).Hash -ine $entry.sha256) { throw ('Source changed: '+$entry.path) }
    }
    Write-Output ('MOVE_SOURCE='+$source)
    Write-Output ('MOVE_DESTINATION='+$dest)
}
Write-Output ('MIGRATION_PLAN_VERIFIED targets='+@($plan.moves).Count+' files='+$plan.fileCount+' bytes='+$plan.totalBytes)
if (-not $Apply) { Write-Output 'VALIDATION_ONLY: no files moved or deleted'; exit 0 }

function Write-NewJson([string]$Name,$Data) {
    $path = Join-Path $audit $Name
    $stream = [System.IO.File]::Open($path,[System.IO.FileMode]::CreateNew,[System.IO.FileAccess]::Write,[System.IO.FileShare]::Read)
    $writer = [System.IO.StreamWriter]::new($stream,[System.Text.UTF8Encoding]::new($false))
    try { $writer.Write(($Data | ConvertTo-Json -Depth 12)) } finally { $writer.Dispose() }
}
Write-NewJson 'anode-processes-before.json' @($anodeBefore)
$null = New-Item -ItemType Directory -Path $destinationRoot -ErrorAction Stop
$completed = @()
try {
    foreach ($move in $plan.moves) {
        if (Test-Path -LiteralPath $move.destination) { throw ('Destination appeared during migration: '+$move.destination) }
        if ($move.isDirectory) { [Microsoft.VisualBasic.FileIO.FileSystem]::MoveDirectory($move.source,$move.destination,$false) }
        else { [Microsoft.VisualBasic.FileIO.FileSystem]::MoveFile($move.source,$move.destination,$false) }
        if (Test-Path -LiteralPath $move.source) { throw ('Original physical path still exists: '+$move.source) }
        foreach ($entry in $move.files) {
            $file = Get-Item -LiteralPath $entry.destination -ErrorAction Stop
            if ($file.Length -ne $entry.bytes -or (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash -ine $entry.sha256) { throw ('Destination verification failed: '+$entry.destination) }
        }
        $actualCount = if ($move.isDirectory) { @(Get-ChildItem -LiteralPath $move.destination -File -Recurse -Force).Count } else { 1 }
        if ($actualCount -ne $move.fileCount) { throw ('Destination inventory mismatch: '+$move.destination) }
        $completed += [ordered]@{source=$move.source;destination=$move.destination;files=$move.fileCount;bytes=$move.bytes;hashesVerified=$true;originalAbsent=$true}
    }
    $current = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    foreach ($before in $anodeBefore) {
        $after = @($current | Where-Object ProcessId -eq $before.ProcessId)
        if ($after.Count -ne 1 -or $after[0].ExecutablePath -ine $before.ExecutablePath -or $after[0].SessionId -ne $before.SessionId -or $after[0].CreationDate -ne $before.CreationDate) { throw ('Anode process changed: '+$before.ProcessId) }
    }
    Write-NewJson 'anode-processes-after.json' @($current | Where-Object Name -eq 'anode.exe' | Select-Object ProcessId,SessionId,ExecutablePath,CreationDate)
    Write-NewJson 'migration-result.json' ([ordered]@{status='MOVED_AND_HASH_VERIFIED';fileCount=$plan.fileCount;totalBytes=$plan.totalBytes;moves=$completed;anodeProcessesPreserved=@($anodeBefore).Count;historicalArchiveChanged=$false;cleanupExecuted=$false})
    Write-Output ('GHOST_MIGRATION_OK files='+$plan.fileCount+' bytes='+$plan.totalBytes)
    Write-Output ('ANODE_PROCESSES_PRESERVED count='+@($anodeBefore).Count)
} catch {
    Write-NewJson 'migration-failure.json' ([ordered]@{error=$_.Exception.Message;completed=$completed;cleanupExecuted=$false})
    throw
}
