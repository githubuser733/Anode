param([switch]$Live)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'Initialize-MultiopenLaunchers.ps1') -CheckOnly
$config = Get-Content -LiteralPath (Join-Path $root 'config\multiopen-apps.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($entry in $config.apps.PSObject.Properties) {
    $actual = (Get-Item -LiteralPath $entry.Value.executable).VersionInfo.ProductVersion
    if ($actual -ne $entry.Value.verifiedProductVersion) { throw "Revalidation needed after application update: $($entry.Name) $actual" }
}
'MULTIOPEN_VERSIONS_OK apps=3'
@'
import ast, json, sqlite3, sys
from pathlib import Path
root=Path(sys.argv[1])
ast.parse((root/'scripts/Prepare-MultiopenProfile.py').read_text(encoding='utf-8'))
config=json.loads((root/'config/multiopen-apps.json').read_text(encoding='utf-8'))['apps']
for app in ('trae', 'opencode'):
    spec=config[app]
    runtime=root/spec['runtime']
    owner=json.loads((runtime/'profile-owner.json').read_text(encoding='utf-8'))
    assert owner['app']==app and Path(owner['runtime'])==runtime
    assert Path(owner['profile']).is_dir()
for app in ('trae', 'opencode'):
    for rel, source in config[app].get('sharedDirectories', {}).items():
        assert (root/config[app]['runtime']/rel).samefile(source), rel
trae=config['trae']
main_settings=json.loads((Path(trae['sourceProfile'])/'User/settings.json').read_text(encoding='utf-8-sig'))
main_settings.update(trae.get('settingsOverrides', {}))
child_settings=json.loads((root/trae['runtime']/'profile/User/settings.json').read_text(encoding='utf-8-sig'))
assert child_settings==main_settings
assert child_settings['window.startupWindowType']=='agent'
assert (root/trae['runtime']/'profile/ModularData/ai-agent/database.db').samefile(Path(trae['sourceProfile'])/'ModularData/ai-agent/database.db')
print('TRAE_AGENT_MAIN_CONFIG_AND_SHARED_DATA_OK')
for name in ('auth.json', 'opencode.db'):
    assert (root/config['opencode']['runtime']/'data/opencode'/name).samefile(Path(config['opencode']['sharedDirectories']['data/opencode'])/name)
db=root/config['trae']['runtime']/'profile/User/globalStorage/state.vscdb'
connection=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=5)
try:
    assert connection.execute('PRAGMA quick_check').fetchone()[0]=='ok'
finally:
    connection.close()
print('MULTIOPEN_PROFILE_LINKS_AND_DATABASE_OK')
'@ | python.exe - $root
if ($LASTEXITCODE -ne 0) { throw 'Multiopen profile validation failed' }
if ($Live) {
    $hosts = @(Get-CimInstance Win32_Process -Filter "Name='anode.exe'" | Where-Object {
        $_.CommandLine -match '(?i)(^|\s)__seat-host(\s|$)' -and $_.ExecutablePath.StartsWith($root+'\', [StringComparison]::OrdinalIgnoreCase)
    })
    if ($hosts.Count -ne 1) { throw 'Expected one active project Anode child session' }
    $child = $hosts[0].SessionId
    $processes = @(Get-CimInstance Win32_Process | Where-Object { $_.SessionId -eq $child })
    foreach ($entry in $config.apps.PSObject.Properties) {
        if (!@($processes | Where-Object { $_.ExecutablePath -ieq $entry.Value.executable }).Count) {
            throw "Application is not running in the child session: $($entry.Name)"
        }
    }
    'MULTIOPEN_LIVE_PROCESSES_OK apps=3 childSession='+$child
}
'MULTIOPEN_SETUP_OK'
