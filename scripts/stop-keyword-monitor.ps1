param([Parameter(Mandatory=$true)][string]$Config)
$ErrorActionPreference='Stop'
$settings=Get-Content -LiteralPath $Config -Raw -Encoding UTF8|ConvertFrom-Json
$root=[IO.Path]::GetFullPath($settings.root)+[IO.Path]::DirectorySeparatorChar
$runs=[IO.Path]::GetFullPath($settings.runsDirectory)
if(-not $runs.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)){throw 'Runs directory is outside this project.'}
$stopped=0
if(Test-Path -LiteralPath $runs){
    foreach($file in @(Get-ChildItem -LiteralPath $runs -Filter 'started.json' -File -Recurse)){
        $started=Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8|ConvertFrom-Json
        $runtime=Get-Content -LiteralPath (Join-Path $file.DirectoryName 'monitor-config.json') -Raw -Encoding UTF8|ConvertFrom-Json
        if([IO.Path]::GetFullPath($runtime.stateDirectory) -ine [IO.Path]::GetFullPath($settings.stateDirectory)){continue}
        $process=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$started.pid)
        $script=[IO.Path]::GetFullPath($settings.monitorScript)
        $runtimeConfig=[IO.Path]::GetFullPath((Join-Path $file.DirectoryName 'monitor-config.json'))
        if(-not $process -or -not $process.CommandLine -or -not $process.CommandLine.Contains($script) -or
            $process.CommandLine.IndexOf($runtimeConfig,[StringComparison]::OrdinalIgnoreCase) -lt 0){continue}
        $output=[IO.Path]::GetFullPath($started.output)
        if(-not $output.StartsWith($runs+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){
            throw 'Refusing a stop flag outside the verified runs directory.'
        }
        $flag=Join-Path $output 'stop.flag'
        if(-not (Test-Path -LiteralPath $flag)){
            $handle=[IO.FileStream]::new($flag,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
            $handle.Dispose()
        }
        $stopped++
        [pscustomobject]@{status='KEYWORD_MONITOR_STOP_REQUESTED';pid=$started.pid;output=$output}
    }
}
if($stopped -eq 0){[pscustomobject]@{status='NO_RUNNING_MONITOR';stateDirectory=$settings.stateDirectory}}
