param([switch]$CheckOnly,[switch]$SignIn)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$cfg=Get-Content -LiteralPath (Join-Path $root 'config\anode-remember-login-candidate-20261105.json') -Raw -Encoding UTF8|ConvertFrom-Json
$exe=[IO.Path]::GetFullPath((Join-Path $root $cfg.executable))
$state=[IO.Path]::GetFullPath((Join-Path $root $cfg.stateDirectory))
$stream=[IO.File]::OpenRead($exe)
$sha=[Security.Cryptography.SHA256]::Create()
try{$actualHash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}
finally{$stream.Dispose();$sha.Dispose()}
if($actualHash -cne $cfg.sha256){throw 'Candidate binary changed; verify before use.'}
$hosts=@(Get-CimInstance Win32_Process -Filter "Name='anode.exe'"|Where-Object{$_.CommandLine -match '\s+up(?:\s|$)'})
$others=@($hosts|Where-Object{[IO.Path]::GetFullPath($_.ExecutablePath) -ine $exe})
$arguments=@('--channel',$cfg.channel,'start','--width',[string]$cfg.width,'--height',[string]$cfg.height,'--state-dir',$state,'--remember-login')
if($cfg.sharedClipboard){$arguments+='--clipboard'}
if($SignIn){$arguments+='--sign-in'}
if($CheckOnly){
    [ordered]@{result='ANODE_REMEMBER_ENTRY_CHECK_OK';executable=$exe;arguments=$arguments;blockedByExistingHost=($others.Count -gt 0);credentialAccess=$false;processStarted=$false}|ConvertTo-Json -Depth 6
    return
}
if($others.Count){throw 'The existing Anode has not enabled remember-login. Save its work and arrange the reviewed version switch; this entry does not stop it.'}
if($hosts.Count){
    if($SignIn){throw 'Anode is running; use its native Sign in button for the credential dialog.'}
    & $exe --channel $cfg.channel show|Out-Host
}else{& $exe @arguments|Out-Host}
if($LASTEXITCODE -ne 0){throw ('Anode returned '+$LASTEXITCODE+'. Inspect status before trying again.')}
