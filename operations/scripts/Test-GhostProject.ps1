param([switch]$SkipDoctor)
$ErrorActionPreference='Stop'
$parent=Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$check=Join-Path $parent 'ghost\local\scripts\Test-GhostProject.ps1'
& $check -SkipDoctor:$SkipDoctor
