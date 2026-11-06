param(
    [ValidateSet('anode','bgdesk')][string]$Engine='anode',
    [Parameter(Mandatory=$true)][int]$ExpectedSession,
    [switch]$Preflight
)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$config=Get-Content -LiteralPath (Join-Path $root 'config\grokbot-multiopen.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$session=(Get-Process -Id $PID).SessionId
$hostName=if($Engine -eq 'anode'){'anode.exe'}else{'bgdeskw.exe'}
$hostPattern=if($Engine -eq 'anode'){'(?i)(^|\s)__seat-host(\s|$)'}else{'(?i)(^|\s)agent(\s|$)'}
$hosts=@(Get-CimInstance Win32_Process -Filter ("Name='"+$hostName+"'") | Where-Object {$_.SessionId -eq $session -and $_.CommandLine -match $hostPattern})
$allowed=$session -eq $ExpectedSession -and $hosts.Count -eq 1
$runtime=Join-Path (Join-Path $root $config.runtimeDirectory) $Engine
$profile=Join-Path $runtime 'app'
$data=Join-Path $runtime 'data'
$seedPath=Join-Path $runtime 'login-seed.json'
foreach($path in @($config.executable,$config.sourceUserData,$config.sourceDataRoot)){
    if(!(Test-Path -LiteralPath $path)){throw "Missing Grok Bot source: $path"}
}
if($Preflight){
    [ordered]@{engine=$Engine;session=$session;expectedSession=$ExpectedSession;allowed=$allowed;profile=$profile;dataRoot=$data;seedExists=(Test-Path -LiteralPath $seedPath);launchPerformed=$false}|ConvertTo-Json -Compress
    return
}
if(!$allowed){throw 'REFUSED_WRONG_SESSION: run inside the verified child session of the selected engine.'}
function Copy-NewVerifiedFile([string]$Source,[string]$Destination){
    $before=(Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
    $reader=[IO.File]::Open($Source,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
    try{
        $writer=[IO.File]::Open($Destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try{$reader.CopyTo($writer)}finally{$writer.Dispose()}
    }finally{$reader.Dispose()}
    $after=(Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
    $copied=(Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash
    if($before -ne $after -or $before -ne $copied){throw 'Source changed during login snapshot; inspect the new runtime before retrying.'}
    return [ordered]@{source=$Source;destination=$Destination;sha256=$copied;bytes=(Get-Item -LiteralPath $Destination).Length}
}
if(!(Test-Path -LiteralPath $seedPath)){
    if((Test-Path -LiteralPath $profile) -or (Test-Path -LiteralPath $data)){throw 'Unowned or incomplete runtime exists; refusing to overwrite it.'}
    New-Item -ItemType Directory -Path $profile -ErrorAction Stop | Out-Null
    New-Item -ItemType Directory -Path $data -ErrorAction Stop | Out-Null
    $copies=@()
    foreach($name in $config.seedUserDataFiles){$copies+=Copy-NewVerifiedFile (Join-Path $config.sourceUserData $name) (Join-Path $profile $name)}
    foreach($name in $config.seedDataFiles){$copies+=Copy-NewVerifiedFile (Join-Path $config.sourceDataRoot $name) (Join-Path $data $name)}
    [ordered]@{engine=$Engine;executable=$config.executable;sourceUserData=$config.sourceUserData;sourceDataRoot=$config.sourceDataRoot;profile=$profile;dataRoot=$data;time=(Get-Date).ToString('o');copies=$copies}|ConvertTo-Json -Depth 6|Out-File -LiteralPath $seedPath -Encoding UTF8 -NoClobber
}
$seed=Get-Content -LiteralPath $seedPath -Raw -Encoding UTF8 | ConvertFrom-Json
if($seed.engine -ne $Engine -or $seed.executable -ne $config.executable -or $seed.profile -ne $profile -or $seed.dataRoot -ne $data){throw 'Runtime ownership mismatch.'}
foreach($file in @(Get-ChildItem -LiteralPath $runtime -File -Filter 'launch-*.json' | Sort-Object LastWriteTime -Descending)){
    $prior=Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    $running=Get-Process -Id $prior.pid -ErrorAction SilentlyContinue
    if($running -and $running.SessionId -eq $session -and $running.Path -eq $config.executable){
        [ordered]@{engine=$Engine;pid=$running.Id;session=$session;profile=$profile;launchPerformed=$false;alreadyRunning=$true}|ConvertTo-Json -Compress
        return
    }
}
$info=[Diagnostics.ProcessStartInfo]::new($config.executable)
$info.UseShellExecute=$false
$info.CreateNoWindow=$true
$info.WorkingDirectory=Split-Path -LiteralPath $config.executable
$info.EnvironmentVariables['SAND_USER_DATA_DIR']=$profile
$info.EnvironmentVariables['SAND_DATA_ROOT']=$data
$info.EnvironmentVariables['SAND_DISABLE_UPDATES']='1'
$app=[Diagnostics.Process]::Start($info)
$exited=$app.WaitForExit(1500)
$receipt=[ordered]@{engine=$Engine;pid=$app.Id;session=$session;executable=$config.executable;profile=$profile;dataRoot=$data;loginSeed=$seedPath;launchPerformed=$true;exitedEarly=$exited;time=(Get-Date).ToString('o')}
if($exited){$receipt.exitCode=$app.ExitCode}
$receipt|ConvertTo-Json -Depth 6|Out-File -LiteralPath (Join-Path $runtime ('launch-'+[Guid]::NewGuid().ToString('N')+'.json')) -Encoding UTF8 -NoClobber
$receipt|ConvertTo-Json -Compress
if($exited){throw 'Grok Bot exited during startup; inspect this receipt before retrying.'}
