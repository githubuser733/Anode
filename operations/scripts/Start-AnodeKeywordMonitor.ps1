param([switch]$CheckOnly)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
if($CheckOnly){
    & (Join-Path $root 'scripts/Control-WeChatResident.ps1') -Action Status
}else{
    & (Join-Path $root 'scripts/Control-WeChatResident.ps1') -Action Start
}
