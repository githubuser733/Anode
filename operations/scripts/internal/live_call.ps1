param(
    [Parameter(Mandatory=$true)][string]$Tool,
    [string]$ArgumentsJson = '{}',
    [Parameter(Mandatory=$true)][int]$Port,
    [int]$TimeoutSeconds = 90,
    [switch]$ScreenshotAfter
)
$ErrorActionPreference='Stop'
function Invoke-LiveRpc([string]$Name,[object]$ToolArguments) {
    $request=@{method='tools/call';params=@{name=$Name;arguments=$ToolArguments};timeout=$TimeoutSeconds}
    $body=[System.Text.Encoding]::UTF8.GetBytes(($request | ConvertTo-Json -Depth 16 -Compress))
    $record=Invoke-RestMethod -Uri ('http://127.0.0.1:'+$Port+'/rpc') -Method Post -ContentType 'application/json; charset=utf-8' -Body $body -TimeoutSec ($TimeoutSeconds+15)
    if($record.bridge_error -or $record.response.bridge_error -or $record.response.error){throw ($record | ConvertTo-Json -Depth 8 -Compress)}
    [ordered]@{tool=$Name;id=$record.response.id;elapsed=$record.elapsed_seconds;content=$record.response.result.content;parentBefore=$record.parent_before;parentAfter=$record.parent_after} | ConvertTo-Json -Depth 12 -Compress | Write-Output
    if($record.response.result.isError){throw ('MCP tool returned isError: '+$Name)}
}
$toolArguments=$ArgumentsJson | ConvertFrom-Json
Invoke-LiveRpc $Tool $toolArguments
if($ScreenshotAfter){Invoke-LiveRpc 'seat_screenshot' @{format='png'}}
