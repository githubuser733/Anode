#requires -Version 5.1
$ErrorActionPreference = 'Stop'

function Test-UUConsoleBinding($Inspection) {
    if ($null -eq $Inspection -or -not $Inspection.bindingPass) { return $false }
    $console = [long]$Inspection.physicalConsoleSessionId
    if ($console -le 0 -or $console -eq [uint32]::MaxValue) { return $false }
    $workers = @($Inspection.workers)
    if ($workers.Count -ne 2) { return $false }
    foreach ($name in @('GameViewerServer.exe','GameViewerHealthd.exe')) {
        if (@($workers | Where-Object { $_.name -eq $name -and $_.sessionId -eq $console }).Count -ne 1) { return $false }
    }
    return $true
}

function New-UUConsoleTaskBootstrap {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][object[]]$Files,
        [Parameter(Mandatory=$true)][string]$EntryScript,
        [Parameter(Mandatory=$true)][string]$EvidenceRoot,
        [ValidateSet('Enable','Restore')][string]$Action='Enable'
    )
    $json = ConvertTo-Json -InputObject @($Files) -Compress
    $template = @'
$ErrorActionPreference='Stop'
$locked=New-Object 'System.Collections.Generic.List[System.IDisposable]'
$mutex=New-Object Threading.Mutex($false,'Global\AgentWindowsOperation-UUConsoleTask-v1')
$ownsMutex=$false
try {
    try {
        if(-not $mutex.WaitOne(0)) { throw 'UU_TASK_BUSY: another fixed console operation is running.' }
        $ownsMutex=$true
    } catch [Threading.AbandonedMutexException] {
        $ownsMutex=$true
        throw 'UU_TASK_PREVIOUS_INTERRUPTED: inspect the current service and worker state before another operation.'
    }
    $files=ConvertFrom-Json '__FILES__'
    foreach($file in $files) {
        $stream=[IO.File]::Open($file.path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        $locked.Add($stream)
        $algorithm=[Security.Cryptography.SHA256]::Create()
        try { $actual=([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-','') }
        finally { $algorithm.Dispose() }
        if($actual -ine $file.sha256) { throw ('UU_PAYLOAD_HASH_MISMATCH: '+$file.path) }
    }
    $global:LASTEXITCODE=0
    & '__ENTRY__' -Action __ACTION__ -EvidenceRoot '__EVIDENCE__'
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine($_.Exception.ToString())
    exit 1
} finally {
    foreach($handle in $locked) { $handle.Dispose() }
    if($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
'@
    return $template.Replace('__FILES__',$json.Replace("'","''")).Replace('__ENTRY__',$EntryScript.Replace("'","''")).Replace('__ACTION__',$Action).Replace('__EVIDENCE__',$EvidenceRoot.Replace("'","''"))
}

function New-UUConsoleTaskDefinition {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)]$Scheduler,[Parameter(Mandatory=$true)][string]$Bootstrap)
    $definition=$Scheduler.NewTask(0)
    $definition.RegistrationInfo.Description='Version-pinned UU console binding. Fixed operation only; no caller commands, passwords or automatic triggers.'
    $definition.Principal.UserId='SYSTEM'
    $definition.Principal.LogonType=5
    $definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$true
    $definition.Settings.AllowDemandStart=$true
    $definition.Settings.MultipleInstances=2
    # Retention/restore waits are handled inside the trial. Do not terminate it mid-restore.
    $definition.Settings.ExecutionTimeLimit='PT15M'
    $definition.Settings.DisallowStartIfOnBatteries=$false
    $definition.Settings.StopIfGoingOnBatteries=$false
    $exec=$definition.Actions.Create(0)
    $exec.Path="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Bootstrap))
    $exec.Arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand '+$encoded
    if($exec.Arguments.Length -gt 30000) { throw 'UU_TASK_COMMAND_TOO_LONG' }
    $exec.WorkingDirectory="$env:SystemRoot\System32"
    return $definition
}

function Get-UUConsoleRegisteredTask {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$TaskName)
    $scheduler=New-Object -ComObject Schedule.Service
    $scheduler.Connect()
    try { return $scheduler.GetFolder('\').GetTask($TaskName) }
    catch {
        if ($_.Exception.HResult -eq -2147024894 -or $_.Exception.InnerException.HResult -eq -2147024894) {
            throw ('UU_TASK_NOT_INSTALLED: '+$TaskName+'. Run launchers\maintenance\UU-Console-Setup.cmd once; daily launch never requests elevation.')
        }
        throw ('UU_TASK_UNAVAILABLE: '+$TaskName+'; '+$_.Exception.Message)
    }
}

function Wait-UUConsoleTaskReceipt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]$Task,
        [Parameter(Mandatory=$true)][string]$EvidenceRoot,
        [ValidateSet('Enable','Restore')][string]$Action='Enable',
        [ValidateRange(1,300)][int]$TimeoutSeconds=180
    )
    if ($Task.GetInstances(0).Count -gt 0) { throw 'UU_TASK_BUSY: a request is already running; no duplicate was submitted.' }
    $seen=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach($file in @(Get-ChildItem -LiteralPath $EvidenceRoot -File -Filter 'mode-*.json')) { [void]$seen.Add($file.FullName) }
    $null=$Task.Run($null)
    $deadline=[DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        foreach($file in @(Get-ChildItem -LiteralPath $EvidenceRoot -File -Filter 'mode-*.json' | Where-Object { -not $seen.Contains($_.FullName) })) {
            try { $receipt=Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json }
            catch { continue } # The writer may still be completing this new receipt.
            if($receipt.action -ne $Action) { continue }
            if(-not $receipt.success) { throw ('UU_TASK_FAILED: '+$receipt.exception+'; receipt='+$file.FullName) }
            $receipt | Add-Member NoteProperty taskReceiptPath $file.FullName -Force
            return $receipt
        }
        if($Task.GetInstances(0).Count -eq 0 -and $Task.LastTaskResult -ne 267009) {
            throw ('UU_TASK_NO_RECEIPT: task finished without a fresh receipt; result='+$Task.LastTaskResult+'. No automatic retry.')
        }
        Start-Sleep -Milliseconds 250
    } while([DateTime]::UtcNow -lt $deadline)
    throw 'UU_TASK_TIMEOUT: result unknown; the independent task may still finish or restore. Check Status and receipts before another request.'
}

function Invoke-UUConsoleRegisteredTask {
    [CmdletBinding()]
    param([ValidateSet('Enable','Restore')][string]$Action='Enable')
    $root=Split-Path -Parent $PSScriptRoot
    $config=Get-Content -LiteralPath (Join-Path $root 'config\uu-console-task.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $task=Get-UUConsoleRegisteredTask -TaskName $config.taskNames.$Action
    if($task.Definition.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $task.Definition.Principal.LogonType -ne 5 -or
        $task.Definition.Actions.Count -ne 1 -or $task.Definition.Triggers.Count -ne 0 -or
        $task.Definition.Actions.Item(1).Path -ine "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe") {
        throw 'UU_TASK_DEFINITION_CHANGED: the registered task does not match the fixed SYSTEM operation.'
    }
    return Wait-UUConsoleTaskReceipt -Task $task -EvidenceRoot (Join-Path $root $config.evidenceRoot) -Action $Action
}

Export-ModuleMember -Function Test-UUConsoleBinding,New-UUConsoleTaskBootstrap,New-UUConsoleTaskDefinition,Get-UUConsoleRegisteredTask,Wait-UUConsoleTaskReceipt,Invoke-UUConsoleRegisteredTask
