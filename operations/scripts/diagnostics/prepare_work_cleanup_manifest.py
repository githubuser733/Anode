"""Create a read-only, reviewable manifest of selected old build directories."""
import csv
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work'
AUDIT = ROOT / 'artifacts/work-cleanup-audit-20261106-6f7bb3e2'
PROHIBITED_DIRECTORIES = {'backups', 'backup', 'credentials', 'secrets', 'profile', 'profiles',
                          'userdata', 'user-data', '.git', '.ssh'}
PROHIBITED_FILES = {'.env', 'credentials.json', 'secrets.json', 'passwords.json', 'wallet.dat'}
PROHIBITED_EXTENSIONS = {'.pfx', '.p12', '.pem', '.key', '.sqlite', '.db'}


def inspect(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise RuntimeError(f'Reparse point refused: {path}')
    return info


def file_hash(path, before):
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f'File changed while reading: {path}')
    return digest


def main():
    scope = WORK.resolve(strict=True)
    if scope != WORK or scope == ROOT or scope == Path(scope.anchor):
        raise RuntimeError('Unexpected workspace scope')
    expected = json.loads((AUDIT / 'candidate-paths.json').read_text('utf-8'))
    if (len(expected), sum(row['files'] for row in expected), sum(row['bytes'] for row in expected)) != (26, 13078, 4952471602):
        raise RuntimeError('Candidate inventory differs from reviewed selection')
    for name in ('cleanup-manifest.json', 'cleanup-all-files.csv', 'cleanup-targets.txt'):
        if (AUDIT / name).exists(): raise RuntimeError(f'Output already exists: {name}')
    for parent in [scope, *scope.parents]: inspect(parent)
    targets = []
    all_files = []
    started = time.time()
    for row in expected:
        folder = Path(row['path'])
        if folder.resolve(strict=True) != folder or not folder.is_relative_to(scope) or folder in (ROOT, scope, Path(folder.anchor)):
            raise RuntimeError(f'Candidate escaped work scope: {folder}')
        current = folder
        while current != scope:
            inspect(current); current = current.parent
        files, directories, todo = [], [], [folder]
        while todo:
            item = todo.pop()
            info = inspect(item)
            rel = item.relative_to(folder)
            if any(part.casefold() in PROHIBITED_DIRECTORIES for part in rel.parts):
                raise RuntimeError(f'Protected directory refused: {item}')
            if stat.S_ISDIR(info.st_mode):
                directories.append(str(item))
                with os.scandir(item) as entries:
                    todo.extend(Path(entry.path) for entry in entries)
            elif stat.S_ISREG(info.st_mode):
                if item.name.casefold() in PROHIBITED_FILES or item.suffix.casefold() in PROHIBITED_EXTENSIONS:
                    raise RuntimeError(f'Potential sensitive file refused: {item}')
                if info.st_nlink > 1: raise RuntimeError(f'Hard link refused: {item}')
                files.append({'path': str(item), 'size': info.st_size, 'mtimeNs': info.st_mtime_ns,
                              'sha256': file_hash(item, info)})
            else:
                raise RuntimeError(f'Unsupported file type: {item}')
        files.sort(key=lambda value: value['path'].casefold())
        directories.sort(key=str.casefold)
        if len(files) != row['files'] or sum(item['size'] for item in files) != row['bytes']:
            raise RuntimeError(f'Candidate changed after inventory: {folder}')
        targets.append({'path': str(folder), 'fileCount': len(files), 'bytes': row['bytes'],
                        'directories': directories, 'files': files})
        all_files.extend({'target': str(folder), **item} for item in files)
        print(f'HASHED {folder} files={len(files)} bytes={row["bytes"]}', flush=True)
    result = {'status': 'AWAITING_CURRENT_BATCH_APPROVAL', 'scope': str(scope), 'targets': targets,
              'targetCount': len(targets), 'fileCount': len(all_files), 'totalBytes': sum(row['bytes'] for row in targets),
              'mode': 'WindowsRecycleBinOnly', 'wholeDirectories': True,
              'excluded': ['active release', 'v6 rollback', 'source trees', 'dependency tools', 'runtime data',
                           'credentials', 'backups', 'historical evidence', 'builds directly mentioned by old evidence'],
              'note': 'This is only an inventory; no paths were moved or deleted. Revalidate after approval.',
              'elapsedSeconds': round(time.time() - started, 3)}
    with (AUDIT / 'cleanup-manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    with (AUDIT / 'cleanup-all-files.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['target', 'path', 'size', 'mtimeNs', 'sha256'])
        writer.writeheader(); writer.writerows(all_files)
    with (AUDIT / 'cleanup-targets.txt').open('x', encoding='utf-8') as stream:
        stream.write('完整回收目标（目录内逐文件见 cleanup-all-files.csv；尚未回收）\n')
        for target in targets:
            stream.write(f"{target['path']} | {target['fileCount']} 个文件 | {target['bytes']} 字节\n")
        stream.write(f"总计：{len(targets)} 个目录，{len(all_files)} 个文件，{result['totalBytes']} 字节。\n")
    # Independently read back all outputs before presenting them to the user.
    loaded = json.loads((AUDIT / 'cleanup-manifest.json').read_text('utf-8'))
    with (AUDIT / 'cleanup-all-files.csv').open('r', encoding='utf-8-sig', newline='') as stream:
        checked = list(csv.DictReader(stream))
    if len(checked) != loaded['fileCount'] or sum(int(row['size']) for row in checked) != loaded['totalBytes']:
        raise RuntimeError('Readback counts differ')
    print(f"MANIFEST_VERIFIED targets={len(targets)} files={len(checked)} bytes={loaded['totalBytes']} moved=0", flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f'MANIFEST_PREPARATION_FAILED: {error}', file=sys.stderr)
        raise SystemExit(1)
