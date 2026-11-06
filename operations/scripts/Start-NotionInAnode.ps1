param([switch]$Preflight,[switch]$ShowErrors)
$ErrorActionPreference = 'Stop'
try {
    $root = Split-Path -Parent $PSScriptRoot
    $config = Get-Content -LiteralPath (Join-Path $root 'config\notion-shared-host.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $session = (Get-Process -Id $PID).SessionId
    if (!(Test-Path -LiteralPath $config.executable -PathType Leaf)) { throw 'Installed Notion executable is missing.' }
    $version = (Get-Item -LiteralPath $config.executable).VersionInfo.ProductVersion
    if ($version -ne $config.verifiedProductVersion) { throw "Notion version changed ($version); revalidate this launcher first." }
    $asar = Join-Path (Split-Path -Parent $config.executable) 'resources\app.asar'
    if ((Get-FileHash -LiteralPath $asar -Algorithm SHA256).Hash -ine $config.packageSha256) { throw 'Notion package changed; revalidate this launcher first.' }
    $expectedProfile = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'Notion'
    if ($expectedProfile -ine $config.sharedProfile -or !(Test-Path -LiteralPath $config.sharedProfile -PathType Container)) { throw 'Current Windows account does not resolve to the verified host Notion profile.' }
    $hosts = @(Get-CimInstance Win32_Process -Filter "Name='anode.exe'" | Where-Object {
        $_.SessionId -eq $session -and $_.CommandLine -match '(?i)(^|\s)__seat-host(\s|$)' -and
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)
    })
    $outside = @(Get-CimInstance Win32_Process -Filter "Name='Notion.exe'" | Where-Object { $_.SessionId -ne $session })
    $report = [ordered]@{app='notion';session=$session;version=$version;sharedProfile=$config.sharedProfile;insideAnode=($hosts.Count -eq 1);outsideProcesses=$outside.Count;allowed=($hosts.Count -eq 1 -and $outside.Count -eq 0);launchPerformed=$false}
    if ($Preflight) { $report | ConvertTo-Json -Compress; return }
    if ($hosts.Count -ne 1) { throw '请在 Anode 子桌面内打开此快捷方式。' }
    if ($outside.Count -gt 0) { throw '主桌面或其他会话的 Notion 正在运行。此入口共用同一个主机配置目录；请先正常退出其他会话的 Notion，再打开此入口。不会关闭任何现有程序。' }
    $info = [Diagnostics.ProcessStartInfo]::new($config.executable)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WorkingDirectory = Split-Path -Parent $config.executable
    foreach ($key in @('ELECTRON_RUN_AS_NODE','NOTION_DESKTOP_USER_DATA_DIR','NOTION_DESKTOP_MCP','NOTION_DESKTOP_MCP_OWNER')) { $info.EnvironmentVariables.Remove($key) }
    # No profile override: the installed app uses this same Windows user's normal host profile.
    $process = [Diagnostics.Process]::Start($info)
    $earlyExit = $process.WaitForExit(1800)
    $receipt = [ordered]@{app='notion';pid=$process.Id;session=$session;executable=$config.executable;sharedProfile=$config.sharedProfile;profileCopied=$false;launchPerformed=$true;exitedEarly=$earlyExit;time=(Get-Date).ToString('o')}
    if ($earlyExit) { $receipt.exitCode=$process.ExitCode }
    $evidence = Join-Path $root $config.evidenceDirectory
    if (!(Test-Path -LiteralPath $evidence)) { New-Item -ItemType Directory -Path $evidence | Out-Null }
    $receipt | ConvertTo-Json | Out-File -LiteralPath (Join-Path $evidence ('launch-'+[Guid]::NewGuid().ToString('N')+'.json')) -Encoding UTF8 -NoClobber
    $receipt | ConvertTo-Json -Compress
    if ($earlyExit -and $process.ExitCode -ne 0) { throw 'Notion exited with an error; see the launch receipt.' }
} catch {
    if ($ShowErrors) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message,'Notion in Anode') | Out-Null
    }
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
