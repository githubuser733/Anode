param([switch]$Preflight,[switch]$ShowErrors)
$ErrorActionPreference='Stop'
try {
    $root=Split-Path -Parent $PSScriptRoot
    $spec=Get-Content -LiteralPath (Join-Path $root 'config\notion-concurrent.json') -Raw -Encoding UTF8|ConvertFrom-Json
    $session=(Get-Process -Id $PID).SessionId
    if (!(Test-Path -LiteralPath $spec.executable -PathType Leaf)) { throw 'Installed Notion is missing.' }
    $version=(Get-Item -LiteralPath $spec.executable).VersionInfo.ProductVersion
    $asar=Join-Path (Split-Path -Parent $spec.executable) 'resources\app.asar'
    if ($version -ne $spec.verifiedProductVersion -or (Get-FileHash -LiteralPath $asar).Hash -ine $spec.packageSha256) { throw 'Notion version/package changed; revalidate the runtime mechanism first.' }
    $hosts=@(Get-CimInstance Win32_Process -Filter "Name='anode.exe'"|Where-Object {
        $_.SessionId -eq $session -and $_.CommandLine -match '(?i)(^|\s)__seat-host(\s|$)' -and
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)
    })
    $runtime=Join-Path $root $spec.runtime
    $profile=Join-Path $runtime 'profile'
    $outside=@(Get-CimInstance Win32_Process -Filter "Name='Notion.exe'"|Where-Object {$_.SessionId -ne $session})
    $prepared=Test-Path -LiteralPath (Join-Path $runtime 'profile-owner.json')
    if ($Preflight) {
        @{app='notion';session=$session;insideAnode=($hosts.Count -eq 1);version=$version;profile=$profile;profilePrepared=$prepared;profileArgumentVerified=$spec.profileArgumentVerified;outsideProcesses=$outside.Count;launchPerformed=$false}|ConvertTo-Json -Compress
        return
    }
    if ($hosts.Count -ne 1) { throw 'Open this shortcut INSIDE Anode, like the WorkBuddy shortcut.' }
    if (!$spec.profileArgumentVerified -and $outside.Count -gt 0) { throw 'First launch needs Notion in other sessions closed, to verify the profile argument before enabling concurrency.' }
    & python.exe (Join-Path $PSScriptRoot 'Prepare-NotionProfile.py')
    if ($LASTEXITCODE -ne 0) { throw 'Initial Notion profile is not ready. Existing profiles and login files were not overwritten.' }
    $info=[Diagnostics.ProcessStartInfo]::new($spec.executable)
    $info.UseShellExecute=$false
    $info.CreateNoWindow=$true
    $info.WorkingDirectory=Split-Path -Parent $spec.executable
    $info.Arguments='--user-data-dir="'+$profile+'"'
    foreach($key in @('ELECTRON_RUN_AS_NODE','NOTION_DESKTOP_USER_DATA_DIR','NOTION_DESKTOP_MCP','NOTION_DESKTOP_MCP_OWNER')) { $info.EnvironmentVariables.Remove($key) }
    $process=[Diagnostics.Process]::Start($info)
    $early=$process.WaitForExit(1800)
    $receipt=[ordered]@{app='notion';pid=$process.Id;session=$session;runtime=$runtime;profile=$profile;profileMechanism='command-line-user-data-dir';profileArgumentVerified=$spec.profileArgumentVerified;exitedEarly=$early;time=(Get-Date).ToString('o')}
    if($early){$receipt.exitCode=$process.ExitCode}
    $evidence=Join-Path $root $spec.evidenceDirectory
    $receipt|ConvertTo-Json|Out-File -LiteralPath (Join-Path $evidence ('concurrent-launch-'+[Guid]::NewGuid().ToString('N')+'.json')) -Encoding UTF8 -NoClobber
    $receipt|ConvertTo-Json -Compress
    if($early -and $process.ExitCode -ne 0){throw 'Notion exited with an error; inspect the launch receipt.'}
} catch {
    if($ShowErrors){Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.MessageBox]::Show($_.Exception.Message,'Notion in Anode')|Out-Null}
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
