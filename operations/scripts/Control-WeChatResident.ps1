param([ValidateSet('Install','Start','Stop','Status','Ensure','Worker','TestNotification')][string]$Action='Status')
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$manifest=Get-Content -LiteralPath (Join-Path $root 'config\wechat-resident-tray.json') -Raw -Encoding UTF8|ConvertFrom-Json
$python=$manifest.pythonExecutable
function Invoke-Hidden([string]$Path,[string]$Arguments){
    $start=New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName=$Path
    $start.Arguments=$Arguments
    $start.WorkingDirectory=$root
    $start.UseShellExecute=$false
    $start.CreateNoWindow=$true
    $start.WindowStyle=[Diagnostics.ProcessWindowStyle]::Hidden
    $start.RedirectStandardOutput=$true
    $start.RedirectStandardError=$true
    $start.StandardOutputEncoding=[Text.Encoding]::UTF8
    $start.StandardErrorEncoding=[Text.Encoding]::UTF8
    $process=[Diagnostics.Process]::Start($start)
    $output=$process.StandardOutput.ReadToEndAsync()
    $errors=$process.StandardError.ReadToEndAsync()
    if(-not $process.WaitForExit(45000)){throw 'Resident command timed out; no process was forcibly terminated.'}
    if($process.ExitCode -ne 0){throw ('Resident command failed with exit '+$process.ExitCode+'. See artifacts\wechat-resident\tray\events.jsonl. '+$errors.GetAwaiter().GetResult())}
    return $output.GetAwaiter().GetResult()
}
if($Action -eq 'Status' -or $Action -eq 'TestNotification'){
    $command=if($Action -eq 'Status'){'status'}else{'test-notification'}
    $script=Join-Path $root 'scripts\wechat_monitor\resident.py'
    $raw=Invoke-Hidden $python ('-X utf8 -B "'+$script+'" '+$command)
    $raw|ConvertFrom-Json|ConvertTo-Json -Depth 10 -Compress
    return
}
$exe=Join-Path $root $manifest.executable
if((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ine $manifest.sha256){throw 'Tray executable hash changed; verify the build before updating the manifest.'}
if($Action -eq 'Install'){
    foreach($pair in @(@($manifest.workerTask,'--tray'),@($manifest.watchdogTask,'--ensure'))){
        $task=Get-ScheduledTask -TaskName $pair[0]
        $expected='--root "'+$root+'" '+$pair[1]
        if($task.Actions.Execute -ine $exe -or $task.Actions.Arguments -cne $expected){throw 'Tray task registration differs from the verified manifest.'}
    }
    [pscustomobject]@{result='TRAY_TASKS_VERIFIED'}|ConvertTo-Json -Compress
    return
}
$mode=switch($Action){'Start'{'--resume'}'Stop'{'--pause'}default{'--ensure'}}
$null=Invoke-Hidden $exe ('--root "'+$root+'" '+$mode)
[pscustomobject]@{result=$(switch($Action){'Start'{'TRAY_START_REQUESTED'}'Stop'{'RESIDENT_STOP_REQUESTED'}default{'TRAY_ENSURED'}});consoleWindow=$false;forcedTermination=$false}|ConvertTo-Json -Compress
