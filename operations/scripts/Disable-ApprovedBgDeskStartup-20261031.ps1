param([switch]$Apply)
# Only the two values and nine tasks included in the 2026-10-31 cleanup approval.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$evidence = Join-Path $root 'artifacts\maintenance-cleanup-20261031'
$backup = Join-Path $evidence 'protected-system-backup'
$values = @(Get-Content -LiteralPath (Join-Path $backup 'bgdesk-logon-values.json') -Raw | ConvertFrom-Json)
$expectedNames = @('Agent','ApplyTaskbar','ContextProbe','Host','Session','ViewerDiagnostic','ViewerFix','ViewerFixExternal','ViewerTaskbar') |
    ForEach-Object { 'AgentWindowsOperation-BgDesk-' + $_ }
if ($values.Count -ne 2) { throw 'Unexpected registry backup count' }
$approvedRegistry = @('HKCU:\Environment|UserInitMprLogonScript', 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run|BgDeskAgent')
foreach ($entry in $values) {
    if (($entry.path + '|' + $entry.name) -notin $approvedRegistry) { throw 'Unapproved registry value' }
    $key = Get-Item -LiteralPath $entry.path
    if ($key.GetValue($entry.name) -cne $entry.value -or $key.GetValueKind($entry.name).ToString() -ne $entry.kind) {
        throw "Registry value changed since backup: $($entry.name)"
    }
}
foreach ($name in $expectedNames) {
    $task = Get-ScheduledTask -TaskPath '\' -TaskName $name
    if ($task.State -eq 'Running') { throw "Task is running: $name" }
    $saved = [xml](Get-Content -LiteralPath (Join-Path $backup ($name + '.xml')) -Raw)
    $current = [xml](Export-ScheduledTask -TaskPath '\' -TaskName $name)
    foreach ($section in @('Actions','Principals','Triggers')) {
        if ($saved.Task.$section.InnerXml -cne $current.Task.$section.InnerXml) {
            throw "Task changed since backup: $name $section"
        }
    }
}
'STARTUP_PRECHECK_OK values=2 tasks=9 backupsMatch=true'
if (!$Apply) { 'VALIDATION_ONLY; no settings changed'; return }
$logPath = Join-Path $evidence 'startup-actions.jsonl'
$stream = [IO.File]::Open($logPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
$writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
try {
    foreach ($name in $expectedNames) {
        Disable-ScheduledTask -TaskPath '\' -TaskName $name | Out-Null
        if ((Get-ScheduledTask -TaskPath '\' -TaskName $name).Settings.Enabled) { throw "Task not disabled: $name" }
        $writer.WriteLine((@{task=$name;disabled=$true} | ConvertTo-Json -Compress)); $writer.Flush()
    }
    foreach ($entry in $values) {
        Set-ItemProperty -LiteralPath $entry.path -Name $entry.name -Value ''
        if ((Get-Item -LiteralPath $entry.path).GetValue($entry.name) -cne '') { throw "Value not cleared: $($entry.name)" }
        $writer.WriteLine((@{path=$entry.path;name=$entry.name;cleared=$true} | ConvertTo-Json -Compress)); $writer.Flush()
    }
} finally { $writer.Dispose() }
$result = @{disabledTasks=9;clearedValues=2;tasksUnregistered=0;backupDirectory=$backup;credentialsTouched=$false}
$out = [IO.File]::Open((Join-Path $evidence 'startup-disabled.json'), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
$writer = [IO.StreamWriter]::new($out, [Text.UTF8Encoding]::new($false))
try { $writer.WriteLine(($result | ConvertTo-Json -Depth 4)) } finally { $writer.Dispose() }
'STARTUP_DISABLED_OK tasks=9 values=2 backupsPreserved=true'
