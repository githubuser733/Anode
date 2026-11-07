#requires -Version 5.1
[CmdletBinding()]
param(
    # This local regression reproduces Codex's verified PowerShell 7 parent environment.
    [string]$CoreModulePath=(Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\Modules')
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root ('artifacts\uu-console-tests\' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($out)
$module = Join-Path $PSScriptRoot 'UUConsoleTask.psm1'
if (-not (Test-Path -LiteralPath $module)) { throw 'FAIL: ordinary UU task entry is not implemented.' }
Import-Module $module -Force
$passed = 0
function Assert-True($Value, [string]$Message) {
    if (-not $Value) { throw $Message }
}
function Check([string]$Name, [scriptblock]$Body) {
    & $Body
    $script:passed++
    'PASS ' + $Name
}
function New-Fixture([string]$Label, [string]$Body='') {
    $folder = Join-Path $out ($Label + '-' + [Guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($folder)
    $entry = Join-Path $folder 'entry.ps1'
    $marker = Join-Path $folder 'executed.txt'
    $source = "param(`$Action,`$EvidenceRoot)`r`n"+$Body+"`r`n[IO.File]::WriteAllText('" + $marker.Replace("'", "''") + "',`$Action)`r`n"
    [IO.File]::WriteAllText($entry, $source)
    [pscustomobject]@{ folder=$folder; entry=$entry; marker=$marker; hash=(Get-FileHash -LiteralPath $entry).Hash }
}
function Invoke-Bootstrap([string]$Code, [string]$LogName) {
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Code))
    $start=New-Object Diagnostics.ProcessStartInfo
    $start.FileName="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $start.Arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand '+$encoded
    $start.UseShellExecute=$false
    $start.CreateNoWindow=$true
    $start.RedirectStandardOutput=$true
    $start.RedirectStandardError=$true
    $process=[Diagnostics.Process]::Start($start)
    $stdout=$process.StandardOutput.ReadToEndAsync()
    $stderr=$process.StandardError.ReadToEndAsync()
    if(-not $process.WaitForExit(15000)) { throw 'Bootstrap fixture did not finish.' }
    $codeResult=$process.ExitCode
    [IO.File]::WriteAllText((Join-Path $out $LogName), $stdout.GetAwaiter().GetResult()+$stderr.GetAwaiter().GetResult())
    $process.Dispose()
    return $codeResult
}
function New-FakeTask([string]$Mode, [string]$Folder) {
    $task = [pscustomobject]@{ mode=$Mode; folder=$Folder; runs=0; LastTaskResult=0; active=0 }
    $task | Add-Member ScriptMethod GetInstances { param($Flags) return [pscustomobject]@{Count=$this.active} }
    $task | Add-Member ScriptMethod Run {
        param($Parameters)
        if ($null -ne $Parameters) { throw 'The caller must not supply task arguments.' }
        $this.runs++
        if ($this.mode -eq 'busy') { throw 'A busy task must not be started twice.' }
        if ($this.mode -eq 'success' -or $this.mode -eq 'failure') {
            $value = @{action='Enable';success=($this.mode -eq 'success');binding=@{bindingPass=$true};exception='controlled failure'}
            $value | ConvertTo-Json -Depth 4 | Out-File -LiteralPath (Join-Path $this.folder ('mode-'+[Guid]::NewGuid().ToString('N')+'.json')) -Encoding UTF8 -NoClobber
        }
        if ($this.mode -eq 'timeout') { $this.active=1 }
        if ($this.mode -eq 'crash') { $this.LastTaskResult=5 }
        return [pscustomobject]@{InstanceGuid=[Guid]::NewGuid().ToString()}
    }
    if ($Mode -eq 'busy') { $task.active=1 }
    return $task
}

Check 'only both unique workers in the current physical console count as ready' {
    $inspection = [pscustomobject]@{physicalConsoleSessionId=7;bindingPass=$true;workers=@(
        [pscustomobject]@{name='GameViewerServer.exe';sessionId=7},
        [pscustomobject]@{name='GameViewerHealthd.exe';sessionId=7})}
    Assert-True (Test-UUConsoleBinding $inspection) 'A complete binding was rejected.'
    $inspection.workers[1].sessionId=18
    Assert-True (-not (Test-UUConsoleBinding $inspection)) 'A health worker in the child session was accepted.'
    $inspection.workers=@($inspection.workers[0],$inspection.workers[0])
    Assert-True (-not (Test-UUConsoleBinding $inspection)) 'Duplicate server processes were accepted.'
    $inspection.physicalConsoleSessionId=0
    Assert-True (-not (Test-UUConsoleBinding $inspection)) 'Session zero was accepted.'
}
Check 'a pinned script executes with the fixed action' {
    $f=New-Fixture 'valid'
    $bootstrap=New-UUConsoleTaskBootstrap -Files @(@{path=$f.entry;sha256=$f.hash}) -EntryScript $f.entry -EvidenceRoot $f.folder -Action Enable
    Assert-True ((Invoke-Bootstrap $bootstrap 'valid.log') -eq 0) 'Valid bootstrap failed.'
    Assert-True ((Get-Content -LiteralPath $f.marker -Raw) -ceq 'Enable') 'The fixed action did not execute.'
}
Check 'a changed dependency blocks execution before the entry runs' {
    $f=New-Fixture 'tampered'
    $dependency=Join-Path $f.folder 'dependency.ps1'
    [IO.File]::WriteAllText($dependency, '# changed dependency')
    $bootstrap=New-UUConsoleTaskBootstrap -Files @(@{path=$f.entry;sha256=$f.hash},@{path=$dependency;sha256=('0'*64)}) -EntryScript $f.entry -EvidenceRoot $f.folder -Action Enable
    Assert-True ((Invoke-Bootstrap $bootstrap 'tampered.log') -ne 0) 'A changed payload executed.'
    Assert-True (-not (Test-Path -LiteralPath $f.marker)) 'The entry ran before all dependencies were verified.'
}
Check 'the verified files remain locked throughout execution' {
    $body=@'
try {
    $writer=[IO.File]::Open((Join-Path $PSScriptRoot 'dependency.ps1'),[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)
    $writer.Dispose()
    throw 'The dependency was writable during execution.'
} catch [IO.IOException] { }
'@
    $f=New-Fixture 'locked' $body
    $dependency=Join-Path $f.folder 'dependency.ps1'
    [IO.File]::WriteAllText($dependency,'# immutable during execution')
    $bootstrap=New-UUConsoleTaskBootstrap -Files @(@{path=$f.entry;sha256=$f.hash},@{path=$dependency;sha256=(Get-FileHash -LiteralPath $dependency).Hash}) -EntryScript $f.entry -EvidenceRoot $f.folder -Action Enable
    Assert-True ((Invoke-Bootstrap $bootstrap 'locked.log') -eq 0) 'The files were not held read-only.'
    Assert-True (Test-Path -LiteralPath $f.marker) 'The lock check never ran.'
}
Check 'simultaneous enable and restore tasks cannot both execute' {
    $f=New-Fixture 'busy-bootstrap'
    $mutex=New-Object Threading.Mutex($false,'Global\AgentWindowsOperation-UUConsoleTask-v1')
    $owned=$mutex.WaitOne(0)
    Assert-True $owned 'A real UU operation is running; do not run fixtures concurrently.'
    try {
        $bootstrap=New-UUConsoleTaskBootstrap -Files @(@{path=$f.entry;sha256=$f.hash}) -EntryScript $f.entry -EvidenceRoot $f.folder -Action Restore
        Assert-True ((Invoke-Bootstrap $bootstrap 'busy-bootstrap.log') -ne 0) 'A second operation executed concurrently.'
        Assert-True (-not (Test-Path -LiteralPath $f.marker)) 'A blocked operation ran its entry.'
    } finally { if($owned){$mutex.ReleaseMutex()}; $mutex.Dispose() }
}
Check 'the task definition is fixed SYSTEM and has no autonomous triggers' {
    $scheduler=New-Object -ComObject Schedule.Service
    $scheduler.Connect()
    $definition=New-UUConsoleTaskDefinition -Scheduler $scheduler -Bootstrap 'exit 0'
    Assert-True ($definition.Principal.UserId -eq 'SYSTEM') 'Unexpected task identity.'
    Assert-True ($definition.Principal.LogonType -eq 5) 'A password-dependent logon was used.'
    Assert-True ($definition.Actions.Count -eq 1) 'Unexpected extra task action.'
    Assert-True ($definition.Triggers.Count -eq 0) 'An autonomous trigger was installed.'
    Assert-True ($definition.Settings.MultipleInstances -eq 2) 'Concurrent task executions are allowed.'
    Assert-True ($definition.Actions.Item(1).Path -eq "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe") 'PowerShell path was not fixed.'
}
foreach($case in @('success','failure','crash','busy','timeout')) {
    $mode=$case
    Check ('receipt lifecycle '+$mode) {
        $folder=Join-Path $out $mode
        [void][IO.Directory]::CreateDirectory($folder)
        # An old receipt must never satisfy this request.
        @{action='Enable';success=$true} | ConvertTo-Json | Out-File -LiteralPath (Join-Path $folder 'mode-old.json') -Encoding UTF8 -NoClobber
        $task=New-FakeTask $mode $folder
        $caught=$null
        try { $result=Wait-UUConsoleTaskReceipt -Task $task -EvidenceRoot $folder -Action Enable -TimeoutSeconds 1 }
        catch { $caught=$_.Exception.Message }
        if($mode -eq 'success') {
            Assert-True ($null -eq $caught -and $result.success) 'The new successful receipt was not returned.'
        } else {
            $expected=@{failure='UU_TASK_FAILED';crash='UU_TASK_NO_RECEIPT';busy='UU_TASK_BUSY';timeout='UU_TASK_TIMEOUT'}[$mode]
            Assert-True ($caught -like ('*'+$expected+'*')) ('Wrong result for '+$mode+': '+$caught)
        }
        Assert-True ($task.runs -eq $(if($mode -eq 'busy'){0}else{1})) 'The request was started repeatedly.'
    }.GetNewClosure()
}
Check 'an absent task produces setup instructions without requesting elevation' {
    $caught=$null
    try { Get-UUConsoleRegisteredTask -TaskName ('UU-Missing-Test-'+[Guid]::NewGuid().ToString('N')) | Out-Null }
    catch { $caught=$_.Exception.Message }
    Assert-True ($caught -like '*UU_TASK_NOT_INSTALLED*') 'Missing installation was not identified.'
}
Check 'the CMD entry works with PowerShell 7 modules before Windows modules' {
    $folder=Join-Path $out 'launcher-environment'
    $launchers=Join-Path $folder 'launchers\maintenance'
    $scripts=Join-Path $folder 'scripts'
    Assert-True (Test-Path -LiteralPath (Join-Path $CoreModulePath 'Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')) 'Provide -CoreModulePath for a real PowerShell 7 module fixture.'
    foreach($path in @($launchers,$scripts)){[void][IO.Directory]::CreateDirectory($path)}
    $fixture=@'
param($Action)
$ErrorActionPreference='Stop'
$hash=Get-FileHash -LiteralPath $PSCommandPath
if($hash.Hash.Length -ne 64) { throw 'Hash command did not execute.' }
'LAUNCHER_MODULE_ENV_OK'
'@
    foreach($name in @('Set-UUConsoleMode.ps1','Install-UUConsoleTask.ps1')) { [IO.File]::WriteAllText((Join-Path $scripts $name),$fixture) }
    foreach($name in @('UU-Console-Enable.cmd','UU-Console-Restore.cmd','UU-Console-Setup.cmd')) {
        $launcher=Join-Path $launchers $name
        Copy-Item -LiteralPath (Join-Path $root ('launchers\maintenance\'+$name)) -Destination $launcher
        $start=New-Object Diagnostics.ProcessStartInfo
        $start.FileName=$env:ComSpec
        $start.Arguments='/d /c ""'+$launcher+'" --no-pause"'
        $start.UseShellExecute=$false
        $start.CreateNoWindow=$true
        $start.RedirectStandardOutput=$true
        $start.RedirectStandardError=$true
        $start.EnvironmentVariables['PSModulePath']=$CoreModulePath+';'+$env:PSModulePath
        $process=[Diagnostics.Process]::Start($start)
        $stdout=$process.StandardOutput.ReadToEndAsync()
        $stderr=$process.StandardError.ReadToEndAsync()
        if(-not $process.WaitForExit(10000)) { throw ('Fixture launcher timed out: '+$name) }
        $output=$stdout.GetAwaiter().GetResult()+$stderr.GetAwaiter().GetResult()
        [IO.File]::WriteAllText((Join-Path $folder ($name+'.log')),$output)
        Assert-True ($process.ExitCode -eq 0 -and $output.Contains('LAUNCHER_MODULE_ENV_OK')) ('Foreign module path broke '+$name+': '+$output)
        $process.Dispose()
    }
}
'UU_CONSOLE_TASK_TESTS_OK passed='+$passed
'EVIDENCE: '+$out
