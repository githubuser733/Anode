param([switch]$Live)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$spec=Get-Content -LiteralPath (Join-Path $root 'config\notion-concurrent.json') -Raw -Encoding UTF8|ConvertFrom-Json
& (Join-Path $PSScriptRoot 'Initialize-NotionConcurrentLauncher.ps1') -CheckOnly
if((Get-Item -LiteralPath $spec.executable).VersionInfo.ProductVersion -ne $spec.verifiedProductVersion){throw 'Notion version changed.'}
$asar=Join-Path (Split-Path -Parent $spec.executable) 'resources\app.asar'
if((Get-FileHash -LiteralPath $asar).Hash -ine $spec.packageSha256){throw 'Notion package changed.'}
@'
import json,pathlib,sys
root=pathlib.Path(sys.argv[1])
spec=json.loads((root/'config/notion-concurrent.json').read_text(encoding='utf-8'))
runtime=root/spec['runtime']
owner=json.loads((runtime/'profile-owner.json').read_text(encoding='utf-8'))
profile=runtime/'profile'
assert owner['app']=='notion' and pathlib.Path(owner['profile'])==profile
assert pathlib.Path(owner['source'])==pathlib.Path(spec['sourceProfile'])
for relative in owner['initialSnapshotFiles']:
    assert (profile/relative).is_file()
    assert not (profile/relative).samefile(pathlib.Path(spec['sourceProfile'])/relative)
print('NOTION_OWNED_PROFILE_SEPARATE_FROM_HOST_OK')
'@|python.exe - $root
if($LASTEXITCODE -ne 0){throw 'Notion profile identity check failed.'}
if($Live){
    $hosts=@(Get-CimInstance Win32_Process -Filter "Name='anode.exe'"|Where-Object {$_.CommandLine -match '(?i)(^|\s)__seat-host(\s|$)' -and $_.ExecutablePath.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)})
    if($hosts.Count -ne 1){throw 'Expected exactly one project Anode child host.'}
    $profile=Join-Path (Join-Path $root $spec.runtime) 'profile'
    $roots=@(Get-CimInstance Win32_Process -Filter "Name='Notion.exe'"|Where-Object {$_.CommandLine -notmatch '--type='})
    $child=@($roots|Where-Object {$_.SessionId -eq $hosts[0].SessionId -and $_.CommandLine.Contains($profile)})
    $main=@($roots|Where-Object {$_.SessionId -eq (Get-Process -Id $PID).SessionId -and !$_.CommandLine.Contains($profile)})
    if($child.Count -ne 1 -or $main.Count -ne 1){throw 'Expected one independent child Notion and one host Notion.'}
    'NOTION_CONCURRENT_LIVE_OK childSession='+$hosts[0].SessionId+' childPid='+$child[0].ProcessId+' mainPid='+$main[0].ProcessId
}
'NOTION_CONCURRENT_SETUP_OK'
