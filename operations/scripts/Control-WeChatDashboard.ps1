param([ValidateSet('Install','Start','Stop','Status','Open')][string]$Action='Status')
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$config=Get-Content -LiteralPath (Join-Path $root 'config\wechat-dashboard.json') -Raw -Encoding UTF8|ConvertFrom-Json
$server=Join-Path $root 'scripts\wechat_dashboard\server.py'
function Invoke-Dashboard([string]$Mode){
    $start=New-Object Diagnostics.ProcessStartInfo
    $start.FileName=$config.pythonExecutable
    $start.Arguments='-X utf8 -B "'+$server+'" '+$Mode
    $start.WorkingDirectory=$root
    $start.UseShellExecute=$false
    $start.CreateNoWindow=$true
    $start.RedirectStandardOutput=$true
    $start.RedirectStandardError=$true
    $start.StandardOutputEncoding=[Text.Encoding]::UTF8
    $process=[Diagnostics.Process]::Start($start)
    $output=$process.StandardOutput.ReadToEndAsync()
    $errorText=$process.StandardError.ReadToEndAsync()
    if(-not $process.WaitForExit(10000)){throw 'Dashboard control timed out.'}
    if($process.ExitCode -ne 0){throw $errorText.GetAwaiter().GetResult()}
    return ($output.GetAwaiter().GetResult()|ConvertFrom-Json)
}
function Install-Dashboard {
    $service=New-Object -ComObject Schedule.Service
    $service.Connect()
    $account=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    $definition=$service.NewTask(0)
    $definition.RegistrationInfo.Description='Local read-only WeChat monitor dashboard. Native pythonw entry; no console.'
    $definition.Principal.UserId=$account
    $definition.Principal.LogonType=3
    $definition.Principal.RunLevel=0
    $definition.Settings.Enabled=$true
    $definition.Settings.Hidden=$true
    # Each repeated invocation performs the application's existing/port check.
    # IgnoreNew would reject it before that check while the server is running.
    $definition.Settings.MultipleInstances=0
    $definition.Settings.ExecutionTimeLimit='PT0S'
    $definition.Settings.DisallowStartIfOnBatteries=$false
    $definition.Settings.StopIfGoingOnBatteries=$false
    $exec=$definition.Actions.Create(0)
    $exec.Path=$config.pythonwExecutable
    $exec.Arguments='-X utf8 -B "'+$server+'" serve'
    $exec.WorkingDirectory=$root
    $timer=$definition.Triggers.Create(1)
    $timer.StartBoundary=(Get-Date).AddMinutes(1).ToString('s')
    $timer.Repetition.Interval='PT1M'
    $logon=$definition.Triggers.Create(9)
    $logon.UserId=$account
    $logon.Delay='PT30S'
    $existing=Get-ScheduledTask -TaskName $config.taskName -ErrorAction SilentlyContinue
    if($existing){
        if($existing.Actions.Execute -ine $exec.Path -or $existing.Actions.Arguments -cne $exec.Arguments){throw 'Existing dashboard task differs; it was not overwritten.'}
        if($existing.Settings.MultipleInstances -ne 0){
            $backup=Join-Path $root ('artifacts\wechat-dashboard-deploy-20261105\task-policy-'+[Guid]::NewGuid().ToString('N')+'.xml')
            [void][IO.Directory]::CreateDirectory((Split-Path -Parent $backup))
            [IO.File]::WriteAllText($backup,(Export-ScheduledTask -TaskName $config.taskName))
            $existing.Settings.MultipleInstances=0
            Set-ScheduledTask -InputObject $existing|Out-Null
        }
    }else{
        $null=$service.GetFolder('\').RegisterTaskDefinition($config.taskName,$definition,2,$account,$null,3,$null)
    }
}
if($Action -eq 'Status'){Invoke-Dashboard 'status'|ConvertTo-Json -Depth 4 -Compress;return}
if($Action -eq 'Stop'){
    $null=Invoke-Dashboard 'disable'
    if(Get-ScheduledTask -TaskName $config.taskName -ErrorAction SilentlyContinue){Disable-ScheduledTask -TaskName $config.taskName|Out-Null}
    '{"result":"DASHBOARD_STOP_REQUESTED"}'
    return
}
Install-Dashboard
if($Action -eq 'Install'){'{"result":"DASHBOARD_TASK_INSTALLED"}';return}
$null=Invoke-Dashboard 'enable'
Enable-ScheduledTask -TaskName $config.taskName|Out-Null
if(-not (Invoke-Dashboard 'status').running){Start-ScheduledTask -TaskName $config.taskName}
if($Action -eq 'Open'){
    $deadline=(Get-Date).AddSeconds(10)
    do{Start-Sleep -Milliseconds 250;$status=Invoke-Dashboard 'status'}while(-not $status.running -and (Get-Date) -lt $deadline)
    if(-not $status.running){throw 'Dashboard did not become ready.'}
    Start-Process $status.url
}
'{"result":"DASHBOARD_START_REQUESTED"}'
