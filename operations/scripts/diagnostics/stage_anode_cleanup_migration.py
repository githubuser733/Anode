"""Stage, but do not install, compatibility updates for retiring old Anode builds."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / 'artifacts/version-cleanup-audit-20261106-3d6a9c2b'
OLD = AUDIT / 'migration-original'
NEW = AUDIT / 'migration-staged'
FILES = ('mcp_live_bridge.py', 'Verify-Shared-WorkBuddy.ps1', 'Start-AnodeText.ps1', 'Start-AnodeTimeline.ps1')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def exclusive(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as out:
        out.write(data)


def main():
    if OLD.exists() or NEW.exists() or (AUDIT / 'migration-manifest.json').exists():
        raise RuntimeError('Migration staging already exists')
    records = []
    for name in FILES:
        source = ROOT / 'scripts' / name
        data = source.read_bytes()
        text = data.decode('utf-8-sig')
        if name == 'mcp_live_bridge.py':
            before = '    command = [str(root / "work/releases/anode-v0.11.3/portable/anode.exe"), "--channel", args.channel, "mcp"]'
            after = '''    active = json.loads((root / "config/anode-local-mcp.json").read_text(encoding="utf-8-sig"))
    executable = (root / active["executable"]).resolve(strict=True)
    if not executable.is_relative_to((root / "work").resolve()):
        raise ValueError("Active Anode executable is outside the project work directory")
    with executable.open("rb") as handle:
        actual_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual_hash != active["sha256"]:
        raise ValueError("Active Anode executable hash differs from its manifest")
    command = [str(executable), "--channel", args.channel, "mcp"]'''
            if text.count(before) != 1:
                raise RuntimeError('Unexpected bridge source; no patch made')
            text = text.replace(before, after)
        elif name == 'Verify-Shared-WorkBuddy.ps1':
            before = "$exe='F:\\AI infrastructure\\agent-Windows-operation\\work\\releases\\anode-v0.11.3\\portable\\anode.exe'"
            after = '''$root=Split-Path -Parent $PSScriptRoot
$config=Get-Content -LiteralPath (Join-Path $root 'config/anode-local-mcp.json') -Raw -Encoding UTF8|ConvertFrom-Json
$exe=[IO.Path]::GetFullPath((Join-Path $root $config.executable))
if((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash -ine $config.sha256){throw 'ACTIVE_ANODE_HASH_MISMATCH'}'''
            if text.count(before) != 1:
                raise RuntimeError('Unexpected WorkBuddy verification source; no patch made')
            text = text.replace(before, after)
        else:
            text = '''param([switch]$CheckOnly)
$ErrorActionPreference='Stop'
# Compatibility alias: both historical launch names now use the verified active version.
& (Join-Path $PSScriptRoot 'Start-AnodeIntegrated.ps1') -CheckOnly:$CheckOnly
'''
        staged = text.encode('utf-8')
        exclusive(OLD / name, data)
        exclusive(NEW / name, staged)
        if digest((OLD / name).read_bytes()) != digest(data) or digest((NEW / name).read_bytes()) != digest(staged):
            raise RuntimeError(f'Backup or stage readback failed: {name}')
        records.append({'source': str(source), 'originalSha256': digest(data), 'stagedSha256': digest(staged),
                        'backup': str(OLD / name), 'staged': str(NEW / name)})
        print('STAGED_ONLY', name, flush=True)
    with (AUDIT / 'migration-manifest.json').open('x', encoding='utf-8') as out:
        json.dump({'installed': False, 'files': records}, out, ensure_ascii=False, indent=2)
    print('MIGRATION_STAGED backupHashesVerified=true productionFilesChanged=false')


if __name__ == '__main__':
    main()
