param([switch]$CheckOnly)
$ErrorActionPreference='Stop'
# Compatibility alias: both historical launch names now use the verified active version.
& (Join-Path $PSScriptRoot 'Start-AnodeIntegrated.ps1') -CheckOnly:$CheckOnly
