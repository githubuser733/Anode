param(
    [Parameter(Mandatory=$true)][string]$Config,
    [switch]$Worker,
    [string]$RunDirectory,
    [switch]$CheckOnly
)
$ErrorActionPreference='Stop'
$settings=Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$root=[IO.Path]::GetFullPath($settings.root)
function In-Project([string]$Path){
    $full=[IO.Path]::GetFullPath($Path)
    if(-not $full.StartsWith($root+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){
        throw "Path is outside this project: $full"
    }
    return $full
}
foreach($property in @('anode','reader','monitorScript','stateDirectory','runsDirectory')){
    $settings.$property=In-Project $settings.$property
}
foreach($pair in @(@('reader','readerSha256'),@('monitorScript','monitorSha256'))){
    if((Get-FileHash -LiteralPath $settings.($pair[0]) -Algorithm SHA256).Hash -ine $settings.($pair[1])){
        throw ('Verified file hash changed: '+$settings.($pair[0]))
    }
}
function New-Text([string]$Path,[string]$Text){
    $file=[IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
    $writer=[IO.StreamWriter]::new($file,[Text.UTF8Encoding]::new($false))
    try{$writer.WriteLine($Text)}finally{$writer.Dispose()}
}
function New-Json([string]$Path,$Value){New-Text $Path ($Value|ConvertTo-Json -Depth 15)}
function Anode-Call([string]$Executable,[string]$Arguments){
    $info=[Diagnostics.ProcessStartInfo]::new()
    $info.FileName=$Executable
    $info.Arguments=$Arguments
    $info.UseShellExecute=$false
    $info.CreateNoWindow=$true
    $info.RedirectStandardOutput=$true
    $info.RedirectStandardError=$true
    $info.StandardOutputEncoding=[Text.UTF8Encoding]::new($false)
    $info.StandardErrorEncoding=[Text.UTF8Encoding]::new($false)
    $p=[Diagnostics.Process]::Start($info)
    try{
        $stdout=$p.StandardOutput.ReadToEndAsync()
        $stderr=$p.StandardError.ReadToEndAsync()
        if(-not $p.WaitForExit(20000)){throw 'Anode command did not return; inspect existing work before retrying.'}
        return [pscustomobject]@{code=$p.ExitCode;stdout=$stdout.GetAwaiter().GetResult();stderr=$stderr.GetAwaiter().GetResult()}
    }finally{$p.Dispose()}
}
$probe=Anode-Call $settings.anode '--channel main status --json'
if($probe.code -ne 0){throw 'Anode is unavailable; the monitor will not start a seat.'}
$state=$probe.stdout|ConvertFrom-Json
if($state.state -ne 'ready' -or -not $state.viewOnly){throw 'Use the existing ready Anode seat and release human control first.'}
if((Get-Process -Id $PID).SessionId -ne [int]$state.parentSession){throw 'Run this launcher in the verified parent session.'}
$account=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$ownerLock=Join-Path $settings.stateDirectory 'owner.lock'
if(Test-Path -LiteralPath $ownerLock){
    try{
        $probeLock=[IO.FileStream]::new($ownerLock,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
        $probeLock.Dispose()
    }catch [IO.IOException]{
        [pscustomobject]@{status='ALREADY_RUNNING';stateDirectory=$settings.stateDirectory;reason='stateLockHeld'}
        return
    }
}
$hash=[Security.Cryptography.SHA256]::Create()
try{
    $key=([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($settings.stateDirectory.ToUpperInvariant())))).Replace('-','').Substring(0,24)
    $taskKey=([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes(([IO.Path]::GetFullPath($Config)+$PSCommandPath).ToUpperInvariant())))).Replace('-','').Substring(0,24)
}finally{$hash.Dispose()}
$mutex=[Threading.Mutex]::new($false,('Local\AnodeKeywordMonitor-'+$key))
$held=$false
try{
    try{$held=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$held=$true}
    if(-not $held){[pscustomobject]@{status='ALREADY_RUNNING';stateDirectory=$settings.stateDirectory};return}
    if($CheckOnly){
        [pscustomobject]@{status='KEYWORD_MONITOR_LAUNCHER_CHECK_OK';root=$root;session=$state.session;parentSession=$state.parentSession;
            windowTitle=$settings.windowTitle;taskCreated=$false;seatStarted=$false;hashesVerified=$true}
        return
    }
    if(-not $Worker){
        $runName='run-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)
        $RunDirectory=Join-Path $settings.runsDirectory $runName
        New-Item -ItemType Directory -Path $RunDirectory -ErrorAction Stop|Out-Null
        $scheduler=New-Object -ComObject 'Schedule.Service'
        $scheduler.Connect()
        $folder=$scheduler.GetFolder('\')
        $definition=$scheduler.NewTask(0)
        $definition.RegistrationInfo.Author='Anode keyword monitor'
        $definition.RegistrationInfo.Description='On-demand local keyword monitor; uses the existing Anode seat.'
        $definition.Settings.Enabled=$true
        $definition.Settings.Hidden=$true
        $definition.Settings.AllowDemandStart=$true
        $definition.Settings.DisallowStartIfOnBatteries=$false
        $definition.Settings.StopIfGoingOnBatteries=$false
        $definition.Settings.RunOnlyIfIdle=$false
        $definition.Settings.ExecutionTimeLimit='PT0S'
        $definition.Principal.UserId=$account
        $definition.Principal.LogonType=3
        $definition.Principal.RunLevel=0
        $action=$definition.Actions.Create(0)
        $action.Path=(Get-Command powershell.exe -ErrorAction Stop).Source
        $action.Arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Config "'+([IO.Path]::GetFullPath($Config))+'" -Worker -RunDirectory "$(Arg0)"'
        $action.WorkingDirectory=$root
        $taskName='Anode-KeywordMonitor-'+$taskKey
        New-Json (Join-Path $RunDirectory 'task-definition.json') @{taskName=$taskName;account=$account;parentSession=$state.parentSession;
            executable=$action.Path;arguments=$action.Arguments;onDemand=$true;runLevel=0;taskDefinitionsRetained=$true}
        $existing=@(Get-ScheduledTask -TaskPath '\' -TaskName $taskName -ErrorAction SilentlyContinue)
        if($existing.Count -gt 0){
            $oldActions=@($existing[0].Actions)
            if($oldActions.Count -ne 1 -or $oldActions[0].Execute -ine $action.Path -or
                $oldActions[0].Arguments -ine $action.Arguments -or $oldActions[0].WorkingDirectory -ine $root){
                throw 'Existing monitor task differs; it was not overwritten.'
            }
            $registered=$folder.GetTask($taskName)
        }else{
            $registered=$folder.RegisterTaskDefinition($taskName,$definition,2,$account,$null,3,$null)
        }
        # The child worker needs this mutex; release it immediately before dispatch.
        $mutex.ReleaseMutex();$held=$false
        $running=$registered.RunEx($RunDirectory,4,[int]$state.parentSession,$null)
        if($null -eq $running){throw 'Task Scheduler returned no running instance.'}
        [pscustomobject]@{status='KEYWORD_MONITOR_TASK_DISPATCHED';taskName=$taskName;runDirectory=$RunDirectory;seatStarted=$false}
        return
    }
    $RunDirectory=In-Project $RunDirectory
    $attempt=Join-Path $RunDirectory ('attempt-'+[Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $attempt -ErrorAction Stop|Out-Null
    $agent=[string]$settings.agent
    $lease=Anode-Call $settings.reader ('--channel main --agent '+$agent+' __chat-lease acquire --ttl 120 --wait 0')
    if($lease.code -ne 0){New-Text (Join-Path $attempt 'startup-error.txt') $lease.stderr;throw 'Could not acquire the existing ready seat; no seat was started.'}
    $grant=$lease.stdout|ConvertFrom-Json
    if(-not $grant.leaseToken){throw 'No lease token was returned.'}
    $token=[string]$grant.leaseToken
    try{
        $configValue=@{root=$root;anode=$settings.anode;reader=$settings.reader;agent=$agent;
            session=[int]$state.session;parentSession=[int]$state.parentSession;pid=0;
            windowTitle=$settings.windowTitle;processName=$settings.processName;listName=$settings.listName;
            keywords=@($settings.keywords);tailCount=[int]$settings.tailCount;intervalMs=[int]$settings.intervalMs;
            segmentMs=300000;durationSeconds=[int]$settings.durationSeconds;stateDirectory=$settings.stateDirectory;
            output=(Join-Path $attempt 'monitor');maxScreenshots=1000;maxScreenshotBytes=1073741824}
        $runtimeConfig=Join-Path $attempt 'monitor-config.json'
        New-Json $runtimeConfig $configValue
        $info=[Diagnostics.ProcessStartInfo]::new()
        $info.FileName=(Get-Command python.exe -ErrorAction Stop).Source
        $info.Arguments='-B "'+$settings.monitorScript+'" --config "'+$runtimeConfig+'"'
        $info.WorkingDirectory=$root
        $info.UseShellExecute=$false
        $info.CreateNoWindow=$true
        $info.RedirectStandardInput=$true
        $info.RedirectStandardOutput=$true
        $info.RedirectStandardError=$true
        $info.StandardOutputEncoding=[Text.UTF8Encoding]::new($false)
        $info.StandardErrorEncoding=[Text.UTF8Encoding]::new($false)
        $monitor=[Diagnostics.Process]::Start($info)
        $stdout=$monitor.StandardOutput.ReadToEndAsync();$stderr=$monitor.StandardError.ReadToEndAsync()
        New-Json (Join-Path $attempt 'started.json') @{pid=$monitor.Id;parentSession=$state.parentSession;session=$state.session;
            startedAtUtc=[DateTime]::UtcNow.ToString('O');output=$configValue.output;leaseTokenStored=$false}
        $monitor.StandardInput.WriteLine((@{leaseToken=$token}|ConvertTo-Json -Compress))
        $monitor.StandardInput.Close()
        $monitor.WaitForExit()
        New-Text (Join-Path $attempt 'monitor.stdout') $stdout.GetAwaiter().GetResult()
        New-Text (Join-Path $attempt 'monitor.stderr') $stderr.GetAwaiter().GetResult()
        New-Json (Join-Path $attempt 'exit.json') @{exitCode=$monitor.ExitCode;endedAtUtc=[DateTime]::UtcNow.ToString('O')}
        if($monitor.ExitCode -ne 0){throw 'Monitor exited unsuccessfully; its complete stderr was retained.'}
    }finally{
        $last=Anode-Call $settings.anode '--channel main status --json'
        if($last.code -eq 0 -and (($last.stdout|ConvertFrom-Json).lease.ownerAgentId -eq $agent)){
            $release=Anode-Call $settings.reader ('--channel main --agent '+$agent+' --lease '+$token+' __chat-lease release --cancel-jobs')
            if($release.code -ne 0){New-Text (Join-Path $attempt 'release-error.txt') $release.stderr}
        }
        $token=$null
    }
}finally{
    if($held){$mutex.ReleaseMutex()}
    $mutex.Dispose()
}
