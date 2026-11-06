"""Update reviewed active integration paths after the parent-to-operations relocation."""
import hashlib
import json
from pathlib import Path
import shutil

OPS = Path(__file__).resolve().parents[2]
ANODE = OPS.parent
PARENT = ANODE.parent
BACKUP = OPS / 'artifacts/restructure-20261106/path-edits-before'


def backup(path):
    target = BACKUP / path.relative_to(OPS)
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)


def mapped(value):
    if isinstance(value, dict):
        return {k: mapped(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mapped(v) for v in value]
    if not isinstance(value, str):
        return value
    replacements = [
        (PARENT / 'work/anode-fork', ANODE),
        (PARENT / 'work/anode-remember-source-118f620b', ANODE),
        (PARENT / 'work/deps', ANODE / 'tools'),
        (PARENT, OPS),
    ]
    # A single replacement prevents remapping an already-new prefix twice.
    for old, new in replacements:
        for a, b in ((str(old), str(new)), (old.as_posix(), new.as_posix())):
            if a in value:
                return value.replace(a, b)
    return value


def save_json(path, value):
    backup(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', 'utf-8')


def main():
    edited = []
    for path in (OPS / 'config').glob('*.json'):
        if path.name == 'ghost-runtime.json':
            continue
        old = json.loads(path.read_text('utf-8-sig'))
        value = mapped(old)
        if isinstance(value, dict) and str(value.get('sourceDirectory', '')).startswith('work/anode-'):
            value['sourceDirectory'] = '..'
        if path.name == 'anode-local-mcp.json':
            value.update(commit='c58a6679ec98378d04098bbe11f2566d0659c080',
                branch='codex/local-current', sourceStatus='committed-source',
                localVersion='local-2026.11.06.1')
        if value != old:
            save_json(path, value)
            edited.append(str(path.relative_to(OPS)))
    for relative in ['scripts/launch_shared_workbuddy_in_anode.ps1',
                     'scripts/Verify-Shared-WorkBuddy.ps1']:
        path = OPS / relative
        old = path.read_text('utf-8-sig')
        new = mapped(old)
        if new != old:
            backup(path)
            path.write_text(new, 'utf-8-sig')
            edited.append(relative)
    path = OPS / 'artifacts/active-source-migration.json'
    active = mapped(json.loads(path.read_text('utf-8-sig')))
    for entry in active:
        entry['destinationSha256'] = hashlib.sha256(Path(entry['destination']).read_bytes()).hexdigest()
    save_json(path, active)
    path = OPS / 'artifacts/migration-manifest.json'
    old = json.loads(path.read_text('utf-8-sig'))
    value = dict(old, destinationRoot=mapped(old['destinationRoot']))
    save_json(path, value)
    print(json.dumps({'status':'ACTIVE_PATHS_UPDATED', 'files':edited}, ensure_ascii=False))


if __name__ == '__main__':
    main()
