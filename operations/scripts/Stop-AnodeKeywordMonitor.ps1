param()
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
& (Join-Path $root 'scripts/Control-WeChatResident.ps1') -Action Stop
