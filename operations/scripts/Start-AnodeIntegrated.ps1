param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$config = Get-Content -LiteralPath (Join-Path $root 'config/anode-local-mcp.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$exe = [IO.Path]::GetFullPath((Join-Path $root $config.executable))
$state = [IO.Path]::GetFullPath((Join-Path $root $config.stateDirectory))
if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ine $config.sha256) {
    throw 'The integrated build hash changed. Verify the release before changing its manifest.'
}
$hosts = @(Get-CimInstance Win32_Process -Filter "Name='anode.exe'" | Where-Object { $_.CommandLine -match '\s+up(?:\s|$)' })
$otherHosts = @($hosts | Where-Object { $_.ExecutablePath -ine $exe })
$arguments = @('--channel', $config.channel, 'start', '--width', [string]$config.width,
    '--height', [string]$config.height, '--state-dir', $state)
if ($config.sharedClipboard) { $arguments += '--clipboard' }
if ($config.rememberWindowsLogin) { $arguments += '--remember-login' }
elseif ($config.nativeSignIn) { $arguments += '--sign-in' } else { $arguments += '--hidden' }
if ($CheckOnly) {
    [ordered]@{status='ANODE_INTEGRATED_START_CHECK_OK'; executable=$exe; arguments=$arguments;
        blockedByOlderHost=($otherHosts.Count -gt 0); activeHosts=@($hosts | Select-Object ProcessId,ExecutablePath);
        launchPerformed=$false} | ConvertTo-Json -Depth 6
    return
}
if ($otherHosts.Count -gt 0) {
    throw 'An older Anode host is still running. Save its work and explicitly arrange the version switch first; this launcher will not stop that desktop.'
}
if ($hosts.Count -gt 0) {
    & $exe --channel $config.channel show | Out-Host
} else {
    & $exe @arguments | Out-Host
}
if ($LASTEXITCODE -ne 0) { throw "Anode returned exit code $LASTEXITCODE. Inspect the current session before retrying." }
