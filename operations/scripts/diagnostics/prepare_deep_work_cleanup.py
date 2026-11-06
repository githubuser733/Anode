"""Prepare and verify a specific read-only cleanup list; never move/delete targets."""
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work'
BASE_AUDIT = ROOT / 'artifacts/work-deep-cleanup-audit-20261106-68e2d1c0'
AUDIT = BASE_AUDIT / 'v2'
EXTENSIONS = WORK / 'alpha-readonly-browser/session-7/Default/Extensions'
PROTECTED_DIRS = {'backup', 'backups', '.git', '.ssh', 'credentials', 'secrets', 'userdata', 'user-data'}
PROTECTED_NAMES = {'.env', 'credentials.json', 'secrets.json', 'passwords.json', 'wallet.dat'}
PROTECTED_EXT = {'.key', '.pem', '.pfx', '.p12', '.db', '.sqlite'}
SUSPICIOUS = ('credential', 'secret', 'private', 'token', 'password', 'wallet', 'keystore')
ROOT_SCRIPTS = [
    'fetch_trials.py', 'launch_shared_workbuddy_in_anode.ps1', 'live_call.ps1', 'mcp_live_bridge.py',
    'mcp_main_trial_bridge.py', 'mcp_probe_bridge.py', 'start_isolated_grok.ps1',
    'start_isolated_workbuddy.ps1', 'start_shared_workbuddy.ps1', 'test_anode_clipboard_gui.ps1',
    'test_anode_clipboard_roundtrip.ps1', 'test_anode_clipboard_roundtrip_v2.ps1',
    'test_anode_clipboard_roundtrip_v3.ps1', 'uia_action.ps1'
]
RUN_DIRS = [
    'anode-timeline-live-20261031', 'anode-timeline-live-hidden-20261031',
    'anode-timeline-session8-20261031', 'migration-installed-8c71', 'migration-postcheck-4e95',
    'migration-probe-0f34', 'project-mcp-20261031-113941-aec59fe8',
    'project-mcp-20261102-202523-14e27a13', 'project-mcp-20261102-222858-fecb52b6',
    'project-mcp-20261103-093624-233abaaa', 'project-mcp-20261104-022716-32701b4e', 'run2-20261031'
]


def checked_stat(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise RuntimeError(f'Reparse point refused: {path}')
    return info


def digest(path, before=None):
    before = before or path.stat()
    with path.open('rb') as inp:
        value = hashlib.file_digest(inp, 'sha256').hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f'Changed while hashing: {path}')
    return value


def inspect(path, category):
    if path.resolve(strict=True) != path or not path.is_relative_to(WORK) or path in (WORK, ROOT, Path(path.anchor)):
        raise RuntimeError(f'Unsafe target: {path}')
    ancestor = path
    while ancestor != WORK:
        checked_stat(ancestor)
        ancestor = ancestor.parent
    files, dirs, todo = [], [], [path]
    while todo:
        item = todo.pop()
        info = checked_stat(item)
        rel = item.relative_to(path)
        if any(part.casefold() in PROTECTED_DIRS for part in rel.parts):
            raise RuntimeError(f'Protected subdirectory: {item}')
        if stat.S_ISDIR(info.st_mode):
            dirs.append(str(item))
            with os.scandir(item) as it:
                todo.extend(Path(entry.path) for entry in it)
        elif stat.S_ISREG(info.st_mode):
            if item.name.casefold() in PROTECTED_NAMES or item.suffix.casefold() in PROTECTED_EXT:
                raise RuntimeError(f'Potential credential or database: {item}')
            if info.st_nlink > 1:
                raise RuntimeError(f'Hardlinked file refused: {item}')
            if category == 'duplicate-extension' and any(word in item.name.casefold() for word in SUSPICIOUS):
                raise RuntimeError(f'Potential sensitive extension filename: {item}')
            files.append({'path': str(item), 'size': info.st_size, 'mtimeNs': info.st_mtime_ns,
                          'sha256': digest(item, info)})
        else:
            raise RuntimeError(f'Unsupported item: {item}')
    files.sort(key=lambda row: row['path'].casefold())
    dirs.sort(key=str.casefold)
    return {'path': str(path), 'category': category, 'kind': 'directory' if path.is_dir() else 'file',
            'fileCount': len(files), 'bytes': sum(x['size'] for x in files), 'files': files, 'directories': dirs}


def snapshot(row):
    """Keep exact copies of tiny old scripts and run evidence before recycling."""
    archive = AUDIT / 'preserved-work-evidence'
    for item in row['files']:
        source = Path(item['path'])
        destination = archive / source.relative_to(WORK)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as inp, destination.open('xb') as out:
            shutil.copyfileobj(inp, out, 1024 * 1024)
        if digest(destination) != item['sha256'] or digest(source) != item['sha256']:
            raise RuntimeError(f'Evidence backup mismatch: {source}')


def main():
    out = AUDIT / 'cleanup-manifest.json'
    if out.exists() or (AUDIT / 'cleanup-targets.txt').exists() or (AUDIT / 'cleanup-files.csv').exists():
        raise RuntimeError('Cleanup manifest already exists')
    AUDIT.mkdir(exist_ok=False)
    compare = json.loads((BASE_AUDIT / 'edge-extension-comparison.json').read_text('utf-8'))
    primary = Path(compare['primaryRoot'])
    chosen = []
    for row in compare['entries']:
        if not row['identicalToPrimary'] or row['files'] == 0:
            continue
        folder = Path(row['path'])
        if folder.parent != EXTENSIONS:
            raise RuntimeError('Unexpected extension location')
        items = list(folder.rglob('*'))
        if any(any(word in p.name.casefold() for word in SUSPICIOUS) for p in items if p.is_file()):
            continue
        if any(p.is_dir() and p.name.casefold() in PROTECTED_DIRS for p in items):
            continue
        chosen.append((folder, 'duplicate-extension'))
    if len(chosen) != 22:
        raise RuntimeError(f'Extension selection changed: {len(chosen)}')
    specs = [
        (WORK / 'notion-package-inspection-20261102', 'old-notion-7.41-package'),
        (WORK / 'anode-remember-source-118f620b/src/Anode/bin', 'generated-build'),
        (WORK / 'anode-remember-source-118f620b/src/Anode/obj', 'generated-build'),
        (WORK / 'releases/anode-v0.11.3/parts', 'duplicate-download-parts'),
        (WORK / 'alpha-readonly-guest/session-7/component_crx_cache', 'inactive-browser-component-cache'),
    ] + chosen + [(WORK / name, 'historical-script') for name in ROOT_SCRIPTS]
    specs += [(WORK / name, 'historical-run-evidence') for name in RUN_DIRS]
    specs += [
        (WORK / 'anode-timeline-20261031/source', 'historical-source-evidence'),
        (WORK / 'sources/anode-v0.11.3', 'historical-source-evidence'),
        (WORK / 'grokbot-comparison-20261031', 'historical-source-evidence'),
        (WORK / 'alpha-upstream-docs-20261104-09232cbc', 'historical-source-evidence'),
    ]
    specs += [(WORK / name, 'empty-folder') for name in
              ('anode-login-repair-20261106-7e839c', 'anode-remember-login-20261105-118f620b')]
    paths = [p for p, _ in specs]
    if len(paths) != len(set(paths)) or any(a != b and a in b.parents for a in paths for b in paths):
        raise RuntimeError('Duplicate or overlapping target')
    rows = []
    for path, category in specs:
        row = inspect(path, category)
        if category == 'empty-folder' and (row['files'] or len(row['directories']) != 1):
            raise RuntimeError(f'Folder is no longer empty: {path}')
        if category == 'duplicate-extension':
            duplicate = primary / path.name
            other = {str(p.relative_to(duplicate)) for p in duplicate.rglob('*') if p.is_file()}
            local = {str(Path(item['path']).relative_to(path)) for item in row['files']}
            if other != local:
                raise RuntimeError(f'Primary extension membership changed: {path}')
            for item in row['files']:
                original = duplicate / Path(item['path']).relative_to(path)
                if original.stat().st_size != item['size'] or digest(original) != item['sha256']:
                    raise RuntimeError(f'Primary extension content changed: {path}')
            row['duplicateOf'] = str(duplicate)
        if category in {'historical-script', 'historical-run-evidence', 'historical-source-evidence'}:
            snapshot(row)
        rows.append(row)
        print(f"PREPARED {category} files={row['fileCount']} bytes={row['bytes']} {path}", flush=True)
    data = {'status': 'AWAITING_CURRENT_BATCH_APPROVAL', 'scope': str(WORK), 'targets': rows,
            'targetCount': len(rows), 'fileCount': sum(x['fileCount'] for x in rows),
            'totalBytes': sum(x['bytes'] for x in rows), 'mode': 'WindowsRecycleBinOnly',
            'note': 'No target was moved or deleted. Recheck references, hashes and occupancy after approval.'}
    with out.open('x', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    with (AUDIT / 'cleanup-targets.txt').open('x', encoding='utf-8') as handle:
        handle.write('本批完整目标；尚未回收。逐文件见 cleanup-files.csv。\n')
        for row in rows:
            handle.write(f"{row['path']} | {row['fileCount']} 文件 | {row['bytes']} 字节 | {row['category']}\n")
        handle.write(f"总计：{data['targetCount']} 项，{data['fileCount']} 文件，{data['totalBytes']} 字节。\n")
    with (AUDIT / 'cleanup-files.csv').open('x', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['target', 'category', 'path', 'size', 'mtimeNs', 'sha256'])
        writer.writeheader()
        for row in rows:
            for item in row['files']:
                writer.writerow({'target': row['path'], 'category': row['category'], **item})
    with (AUDIT / 'cleanup-files.csv').open(encoding='utf-8-sig', newline='') as handle:
        checked = list(csv.DictReader(handle))
    if len(checked) != data['fileCount'] or sum(int(x['size']) for x in checked) != data['totalBytes']:
        raise RuntimeError('CSV verification failed')
    print(f"MANIFEST_VERIFIED targets={data['targetCount']} files={data['fileCount']} bytes={data['totalBytes']} moved=0")


if __name__ == '__main__':
    main()
