# Run with powershell.exe -NoProfile -NonInteractive -Mta -File through Anode exec.
param(
    [Parameter(Mandatory=$true)][string]$AnodeExecutable,
    [Parameter(Mandatory=$true)][int]$ProcessId,
    [Parameter(Mandatory=$true)][string]$WindowTitle,
    [Parameter(Mandatory=$true)][string]$ListName,
    [ValidateRange(1,500)][int]$MaxElements=100,
    [ValidateRange(1,20000)][int]$MaxChars=10000,
    [ValidateRange(1,20000)][int]$BudgetMs=4000
)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$session=(Get-Process -Id $PID).SessionId
$statusText=& $AnodeExecutable --channel main status --json | Out-String
$statusCode=$LASTEXITCODE
$status=$statusText|ConvertFrom-Json
if($statusCode -ne 0 -or $status.state -ne 'ready' -or -not $status.agentReady -or
    $session -le 0 -or $status.seat.session -ne $session -or $status.parentSession -eq $session) {
    throw 'Refusing chat access outside the ready, daemon-verified child session.'
}
$target=Get-Process -Id $ProcessId
if($target.SessionId -ne $session){throw 'Refusing a process outside the child session.'}
# Use the same .NET UIA client as Anode. Windows PowerShell's legacy UIA client
# exposed only a shallow tree for the installed WeChat build during live checks.
$result=& $AnodeExecutable --channel main __chat-read --pid $ProcessId --window-title $WindowTitle --list-name $ListName --max-elements $MaxElements --max-chars $MaxChars --budget-ms $BudgetMs 2>&1 | Out-String
$code=$LASTEXITCODE
if($code -ne 0){throw $result}
$result
