param([ValidateSet('Install','Start','Stop','Status','Preview')][string]$Action='Status')
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$cfg=Get-Content -LiteralPath (Join-Path $root 'config\wechat-wecom-forward.json') -Raw -Encoding UTF8|ConvertFrom-Json
$script=Join-Path $root 'scripts\wechat_forward\forward.py'
function Invoke-Forward([string]$Mode){
    $start=New-Object Diagnostics.ProcessStartInfo
    $start.FileName=$cfg.pythonExecutable
    $start.Arguments='-X utf8 -B "'+$script+'" '+$Mode
    $start.WorkingDirectory=$root
    $start.UseShellExecute=$false
    $start.CreateNoWindow=$true
    $start.RedirectStandardOutput=$true
    $start.RedirectStandardError=$true
    $start.StandardOutputEncoding=[Text.Encoding]::UTF8
    $start.StandardErrorEncoding=[Text.Encoding]::UTF8
    $process=[Diagnostics.Process]::Start($start)
    $output=$process.StandardOutput.ReadToEndAsync()
    $errors=$process.StandardError.ReadToEndAsync()
    if(-not $process.WaitForExit(90000)){throw 'Forward control timed out; inspect local state before attempting again.'}
    if($process.ExitCode -ne 0){throw ('Forward command failed. '+$errors.GetAwaiter().GetResult())}
    return ($output.GetAwaiter().GetResult()|ConvertFrom-Json)
}
function Install-Forward {
    $service=New-Object -ComObject Schedule.Service
    $service.Connect()
    $account=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    $task=$service.NewTask(0)
    $task.RegistrationInfo.Description='Authorized full-text keyword forwarding via official WeCom CLI; independent from local collection.'
    $task.Principal.UserId=$account
    $task.Principal.LogonType=3
    $task.Principal.RunLevel=0
    $task.Settings.Enabled=$true
    $task.Settings.Hidden=$true
    $task.Settings.MultipleInstances=0 # Duplicate processes return under the application's owner lock.
    $task.Settings.ExecutionTimeLimit='PT0S'
    $task.Settings.DisallowStartIfOnBatteries=$false
    $task.Settings.StopIfGoingOnBatteries=$false
    $exec=$task.Actions.Create(0)
    $exec.Path=$cfg.pythonwExecutable
    $exec.Arguments='-X utf8 -B "'+$script+'" serve'
    $exec.WorkingDirectory=$root
    $timer=$task.Triggers.Create(1)
    $timer.StartBoundary=(Get-Date).AddMinutes(1).ToString('s')
    $timer.Repetition.Interval='PT1M'
    $logon=$task.Triggers.Create(9)
    $logon.UserId=$account
    $logon.Delay='PT30S'
    $existing=Get-ScheduledTask -TaskName $cfg.taskName -ErrorAction SilentlyContinue
    if($existing){
        if($existing.Actions.Execute -ine $exec.Path -or $existing.Actions.Arguments -cne $exec.Arguments -or $existing.Actions.WorkingDirectory -ine $root){throw 'Existing forwarding task differs; it was not overwritten.'}
    }else{
        $null=$service.GetFolder('\').RegisterTaskDefinition($cfg.taskName,$task,2,$account,$null,3,$null)
    }
}
if($Action -in @('Status','Preview')){
    Invoke-Forward $Action.ToLowerInvariant()|ConvertTo-Json -Depth 8 -Compress
    return
}
if($Action -eq 'Stop'){
    $null=Invoke-Forward 'disable'
    if(Get-ScheduledTask -TaskName $cfg.taskName -ErrorAction SilentlyContinue){Disable-ScheduledTask -TaskName $cfg.taskName|Out-Null}
    '{"result":"WECOM_FORWARD_STOP_REQUESTED","forcedTermination":false}'
    return
}
Install-Forward
if($Action -eq 'Install'){'{"result":"WECOM_FORWARD_TASK_INSTALLED"}';return}
$null=Invoke-Forward 'enable'
Enable-ScheduledTask -TaskName $cfg.taskName|Out-Null
if(-not (Invoke-Forward 'status').running){Start-ScheduledTask -TaskName $cfg.taskName}
'{"result":"WECOM_FORWARD_START_REQUESTED"}'
