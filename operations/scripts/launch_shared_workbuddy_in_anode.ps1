param(
 [ValidateSet('workbuddy','workbuddy_ai')][string]$Variant='workbuddy',
 [switch]$Preflight,
 [switch]$ShowErrors
)
$ErrorActionPreference='Stop'
try {
 $session=(Get-Process -Id $PID).SessionId
 $seatHosts=@(Get-CimInstance Win32_Process -Filter "Name='anode.exe'"|Where-Object {
  $_.SessionId -eq $session -and $_.CommandLine -match '(?i)(^|\s)__seat-host(\s|$)'
 })
 $apps=@{
  workbuddy=@{exe='C:\Users\Zhang\AppData\Local\Programs\WorkBuddy\WorkBuddy.exe';config='C:\Users\Zhang\.workbuddy'}
  workbuddy_ai=@{exe='C:\Users\Zhang\AppData\Local\Programs\WorkBuddyAI\WorkBuddyAI.exe';config='C:\Users\Zhang\.workbuddy-ai'}
 }
 $selected=$apps[$Variant]
 $electronData=Join-Path 'F:\AI infrastructure\agent-Windows-operation\anode\operations\work\recheck-20261031-shared' ($Variant+'-electron')
 if(!(Test-Path -LiteralPath $selected.exe)){throw 'WorkBuddy executable was not found.'}
 if(!(Test-Path -LiteralPath $selected.config)){throw 'The shared main-desktop configuration directory was not found.'}
 $allowed=$seatHosts.Count -eq 1
 if($Preflight){
  [ordered]@{variant=$Variant;session=$session;allowed=$allowed;sharedConfig=$selected.config;electronData=$electronData;launchPerformed=$false}|ConvertTo-Json -Compress
  return
 }
 if(!$allowed){throw 'Open this shortcut INSIDE the Anode child session. It does not launch a second instance on the main desktop.'}
 if(!(Test-Path -LiteralPath $electronData)){
  New-Item -ItemType Directory -Path $electronData -ErrorAction Stop|Out-Null
 }
 $legacy=Join-Path $electronData 'anode-shared-launch.json'
 if(Test-Path -LiteralPath $legacy){
  $old=Get-Content -LiteralPath $legacy -Raw -Encoding UTF8|ConvertFrom-Json
  if($old.sharedConfig -ne $selected.config -or $old.electronData -ne $electronData){throw 'Existing runtime ownership metadata does not match.'}
 }
 $info=[Diagnostics.ProcessStartInfo]::new($selected.exe)
 $info.UseShellExecute=$false
 $info.CreateNoWindow=$true
 $info.WorkingDirectory=Split-Path -LiteralPath $selected.exe
 $info.EnvironmentVariables['WORKBUDDY_CONFIG_DIR']=$selected.config
 $info.EnvironmentVariables['CODEBUDDY_CONFIG_DIR']=$selected.config
 $info.EnvironmentVariables['WORKBUDDY_USER_DATA_DIR']=$electronData
 $app=[Diagnostics.Process]::Start($info)
 $exited=$app.WaitForExit(1500)
 $receipt=[ordered]@{variant=$Variant;pid=$app.Id;session=$session;sharedConfig=$selected.config;electronData=$electronData;exitedEarly=$exited;time=(Get-Date).ToString('o')}
 if($exited){$receipt.exitCode=$app.ExitCode}
 $receiptPath=Join-Path $electronData ('anode-shared-launch-'+[Guid]::NewGuid().ToString('N')+'.json')
 $receipt|ConvertTo-Json|Out-File -LiteralPath $receiptPath -Encoding UTF8 -NoClobber
 $receipt|ConvertTo-Json -Compress
} catch {
 if($ShowErrors){
  Add-Type -AssemblyName System.Windows.Forms
  [System.Windows.Forms.MessageBox]::Show($_.Exception.Message,'WorkBuddy in Anode')|Out-Null
 }
 Write-Error -Message $_.Exception.Message -ErrorAction Continue
 exit 1
}
