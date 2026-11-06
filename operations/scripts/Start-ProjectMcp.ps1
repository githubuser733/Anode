param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$config = Get-Content -LiteralPath (Join-Path $root 'config\verified-desktop.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$script = Join-Path $PSScriptRoot 'mcp_live_bridge.py'
$python = (Get-Command python.exe -ErrorAction Stop).Source
$expectedEvidenceRoot = [IO.Path]::GetFullPath((Join-Path $root 'artifacts\mcp')) + [IO.Path]::DirectorySeparatorChar
$pattern = Join-Path $root 'work\project-mcp-*\anode\bridge.json'
foreach ($file in @(Get-ChildItem -Path $pattern -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)) {
    $info = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
    $running = Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$info.bridge_pid)
    if ($running -and $running.CommandLine -and $running.CommandLine.Contains($script) -and $running.CommandLine.Contains($root)) {
        if (![IO.Path]::GetFullPath($info.evidence).StartsWith($expectedEvidenceRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Existing project bridge has an unexpected evidence directory.'
        }
        return $info
    }
}
$runId = 'project-mcp-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)
$evidence = Join-Path $root ('artifacts\mcp\'+$runId)
$metadata = Join-Path $root ('work\'+$runId+'\anode\bridge.json')
$arguments = '"'+$script+'" anode --root "'+$root+'" --evidence "'+$evidence+'" --run-id '+$runId+' --channel '+$config.anodeChannel
if ($CheckOnly) {
    [pscustomobject]@{python=$python;arguments=$arguments;metadata=$metadata;evidence=$evidence;launchPerformed=$false}
    return
}
New-Item -ItemType Directory -Path $evidence -ErrorAction Stop | Out-Null
$stdout = Join-Path $evidence 'bridge.stdout.log'
$stderr = Join-Path $evidence 'bridge.stderr.log'
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $metadata) {
        $content = Get-Content -LiteralPath $metadata -Raw -Encoding UTF8
        if ($content.TrimEnd().EndsWith('}')) {
            $info = $content | ConvertFrom-Json
            if ($info.bridge_pid -ne $process.Id) { throw 'Bridge PID mismatch.' }
            return $info
        }
    }
    if ($process.HasExited) {
        throw ('Bridge exited with code '+$process.ExitCode+'. '+(Get-Content -LiteralPath $stderr -Raw -Encoding UTF8))
    }
    Start-Sleep -Milliseconds 150
}
throw "Bridge startup timed out; inspect $evidence before retrying."
