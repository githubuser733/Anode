"""Hash a review pool for old Anode builds; this program never removes files."""
import csv
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / 'artifacts/version-cleanup-audit-20261106-3d6a9c2b'
WORK = ROOT / 'work'
ARTIFACTS = ROOT / 'artifacts'
PROTECTED_PARTS = {'backup', 'backups', '.git', '.ssh', 'credentials', 'secrets', 'profile',
                   'profiles', 'userdata', 'user-data', 'runtime-state', 'state'}
PROTECTED_NAMES = {'.env', 'credentials.json', 'secrets.json', 'passwords.json', 'wallet.dat'}
PROTECTED_EXTS = {'.pfx', '.p12', '.pem', '.key', '.sqlite', '.db'}

# "legacy-entry" paths have current references from old scripts/configuration.
# "rollback" is specifically the old v6 rollback version, not an active release.
SPECS = [
    ('work/anode-activity-20261102', 'legacy-entry'),
    ('work/anode-activity-20261103', 'legacy-entry'),
    ('work/anode-text-20261103', 'legacy-entry'),
    ('work/anode-integrated-20261104-031349-f64226', 'legacy-entry'),
    ('work/anode-capture-responsive-20261105-f07607fd8a', 'legacy-entry'),
    ('work/releases/anode-v0.11.3/portable', 'legacy-entry'),
    ('work/anode-timeline-20261031/publish-004', 'legacy-entry'),
    ('work/anode-login-repair-20261106-7e839c/publish-v6', 'rollback'),
    ('work/anode-remember-build-118f620b', 'build-output'),
]
SPECS += [
    ('artifacts/anode-login-repair-20261106-7e839c/' + name, 'build-output')
    for name in ('build', 'reconnect-final-build', 'reconnect-green-build', 'reconnect-probe-build',
                 'reconnect-red-build', 'reconnect-release-build', 'release-build', 'obj')
]
SPECS += [
    ('artifacts/anode-screenshot-viewer-20261106-734d2b/' + name, 'build-output')
    for name in ('red-build', 'green-build', 'green-build-2', 'green-build-3', 'release-build', 'obj')
]
SPECS += [
    ('artifacts/anode-capture-responsive-f07607fd8a/' + name, 'build-output')
    for name in ('candidate-build-001', 'preview-build-001', 'red-build-001', 'rows-build-001')
]


def safe_stat(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise RuntimeError(f'Reparse point refused: {path}')
    return info


def scan(relative, category):
    path = ROOT / relative
    scope = WORK if path.is_relative_to(WORK) else ARTIFACTS if path.is_relative_to(ARTIFACTS) else None
    if scope is None or path in (ROOT, scope, Path(path.anchor)) or path.resolve(strict=True) != path:
        raise RuntimeError(f'Unsafe target: {path}')
    parent = path
    while parent != scope:
        safe_stat(parent)
        parent = parent.parent
    files, dirs, todo = [], [], [path]
    while todo:
        item = todo.pop()
        info = safe_stat(item)
        rel = item.relative_to(path)
        if any(part.casefold() in PROTECTED_PARTS for part in rel.parts):
            raise RuntimeError(f'Protected directory refused: {item}')
        if stat.S_ISDIR(info.st_mode):
            dirs.append(str(item))
            with os.scandir(item) as children:
                todo.extend(Path(child.path) for child in children)
        elif stat.S_ISREG(info.st_mode):
            if item.name.casefold() in PROTECTED_NAMES or item.suffix.casefold() in PROTECTED_EXTS:
                raise RuntimeError(f'Potential sensitive file refused: {item}')
            if info.st_nlink > 1:
                raise RuntimeError(f'Hard link refused: {item}')
            with item.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = item.stat()
            if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                raise RuntimeError(f'Changed while hashing: {item}')
            files.append({'path': str(item), 'size': info.st_size, 'mtimeNs': info.st_mtime_ns, 'sha256': digest})
        else:
            raise RuntimeError(f'Unsupported item refused: {item}')
    files.sort(key=lambda item: item['path'].casefold())
    dirs.sort(key=str.casefold)
    result = {'path': str(path), 'category': category, 'files': files, 'directories': dirs,
              'fileCount': len(files), 'bytes': sum(item['size'] for item in files)}
    print(f"HASHED category={category} files={result['fileCount']} bytes={result['bytes']} {path}", flush=True)
    return result


def main():
    destinations = [AUDIT / name for name in ('candidate-pool.json', 'candidate-all-files.csv')]
    if any(path.exists() for path in destinations):
        raise RuntimeError('Review pool already exists')
    targets = [ROOT / path for path, _ in SPECS]
    if len(set(targets)) != len(targets) or any(a != b and a in b.parents for a in targets for b in targets):
        raise RuntimeError('Targets overlap')
    started = time.monotonic()
    rows = [scan(relative, category) for relative, category in SPECS]
    payload = {'status': 'PRELIMINARY_NOT_AUTHORIZED', 'targets': rows, 'targetCount': len(rows),
               'fileCount': sum(row['fileCount'] for row in rows), 'totalBytes': sum(row['bytes'] for row in rows),
               'elapsedSeconds': round(time.monotonic() - started, 2)}
    with destinations[0].open('x', encoding='utf-8') as out:
        json.dump(payload, out, ensure_ascii=False, indent=2)
    with destinations[1].open('x', encoding='utf-8-sig', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=['target', 'category', 'path', 'size', 'mtimeNs', 'sha256'])
        writer.writeheader()
        for row in rows:
            for file in row['files']:
                writer.writerow({'target': row['path'], 'category': row['category'], **file})
    with destinations[1].open(encoding='utf-8-sig', newline='') as stream:
        checked = list(csv.DictReader(stream))
    if len(checked) != payload['fileCount'] or sum(int(row['size']) for row in checked) != payload['totalBytes']:
        raise RuntimeError('Review pool readback failed')
    print(f"POOL_VERIFIED targets={len(rows)} files={len(checked)} bytes={payload['totalBytes']} moved=0")


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError) as exc:
        print('PREPARATION_FAILED:', exc, file=sys.stderr)
        raise SystemExit(1)
