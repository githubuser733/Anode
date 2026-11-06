"""Validate the reviewed version-cleanup manifest; execute only with current approval."""
import argparse
import json
from pathlib import Path
import time

import pythoncom
import win32com.client
from win32com.shell import shell, shellcon

import recycle_work_builds_20261106 as checks

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / 'artifacts/version-cleanup-audit-20261106-3d6a9c2b'
MANIFEST = AUDIT / 'cleanup-manifest.json'
RECEIPT = AUDIT / 'current-batch-approval.json'
EXPECTED_MANIFEST = 'c6e136398e1b9d5d44e5285f1480e64509e8d5d84f8a297ae7ea32483199ba86'
OLD_TASK = 'Anode-KeywordMonitor-214A4181AB7C2825E3104635'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def validate_target(row, scopes, hash_files):
    path = Path(row['path'])
    if (path.resolve(strict=True) != path or path in (ROOT, *scopes, Path(path.anchor))
            or not any(path.is_relative_to(scope) for scope in scopes)):
        raise RuntimeError(f'Unsafe or changed target path: {path}')
    scope = next(s for s in scopes if path.is_relative_to(s))
    parent = path.parent
    while parent != scope:
        if getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400:
            raise RuntimeError(f'Reparse ancestor refused: {parent}')
        parent = parent.parent
    files, dirs = checks.tree(path)
    if files != {item['path'] for item in row['files']} or dirs != set(row['directories']):
        raise RuntimeError(f'Contents changed: {path}')
    for item in row['files']:
        file = Path(item['path'])
        info = file.stat()
        if (info.st_size, info.st_mtime_ns) != (item['size'], item['mtimeNs']):
            raise RuntimeError(f'File metadata changed: {file}')
        checks.exclusive_read_probe(file)
        if hash_files and checks.digest(file) != item['sha256']:
            raise RuntimeError(f'File content changed: {file}')


def verify_migration():
    migration = read_json(AUDIT / 'migration-manifest.json')
    for item in migration['files']:
        if checks.digest(item['source']) != item['stagedSha256']:
            raise RuntimeError('Reviewed entry migration is not fully installed: ' + item['source'])
        if checks.digest(item['backup']) != item['originalSha256']:
            raise RuntimeError('Entry backup hash differs: ' + item['backup'])
    svc = win32com.client.Dispatch('Schedule.Service')
    svc.Connect()
    task = svc.GetFolder('\\').GetTask(OLD_TASK)
    if task.Enabled:
        raise RuntimeError('Retired no-trigger legacy fixture task is still enabled')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if checks.digest(MANIFEST) != EXPECTED_MANIFEST:
        raise RuntimeError('Reviewed manifest SHA-256 changed')
    manifest = read_json(MANIFEST)
    if (manifest['targetCount'], manifest['fileCount'], manifest['totalBytes']) != (27, 11632, 5626403201):
        raise RuntimeError('Reviewed manifest totals changed')
    scopes = [Path(x).resolve(strict=True) for x in manifest['scopes']]
    if scopes != [ROOT / 'work', ROOT / 'artifacts']:
        raise RuntimeError('Unexpected workspace scopes')
    targets = [Path(x['path']) for x in manifest['targets']]
    if len(set(targets)) != 27 or any(a != b and a in b.parents for a in targets for b in targets):
        raise RuntimeError('Duplicate or overlapping target')
    if args.execute:
        receipt = read_json(RECEIPT)
        if (not receipt.get('activeUserReplyVerified') or receipt.get('manifestSha256') != EXPECTED_MANIFEST
                or receipt.get('strategy') != manifest['entryStrategy']):
            raise RuntimeError('Current-batch approval missing or mismatched')
        if any((AUDIT / name).exists() for name in ('recycle-run.jsonl', 'cleanup-result.json', 'cleanup-failure.json')):
            raise RuntimeError('Cleanup already attempted; refusing replay')
        verify_migration()
    active = read_json(ROOT / 'config/anode-local-mcp.json')
    active_exe = (ROOT / active['executable']).resolve(strict=True)
    if (checks.digest(active_exe) != active['sha256']
            or any(active_exe == p or p in active_exe.parents for p in targets)):
        raise RuntimeError('Active Anode binary changed or overlaps proposed cleanup')
    refs = checks.direct_references(targets)
    capacity = checks.recycle_capacity(manifest['totalBytes'])
    for row in manifest['targets']:
        validate_target(row, scopes, hash_files=True)
        print(f"VALIDATED {row['path']} files={row['fileCount']}", flush=True)
    folder = win32com.client.Dispatch('Shell.Application').NameSpace(shellcon.CSIDL_BITBUCKET)
    if folder is None:
        raise RuntimeError('Recycle Bin unavailable')
    before_items = {str(item.Path) for item in folder.Items()}
    flags = (0x00080000 | shellcon.FOFX_EARLYFAILURE | shellcon.FOF_NOERRORUI
             | shellcon.FOF_SILENT | shellcon.FOF_WANTNUKEWARNING | shellcon.FOF_NO_CONNECTED_ELEMENTS)
    op = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
    op.SetOperationFlags(flags)
    print(f"PRECHECK_OK targets=27 files=11632 bytes=5626403201 processFieldsUnavailable={refs['processFieldsUnavailable']}", flush=True)
    if not args.execute:
        print('VALIDATION_ONLY; no file operation submitted', flush=True)
        return
    done = []
    try:
        with (AUDIT / 'recycle-run.jsonl').open('x', encoding='utf-8') as log:
            for row in manifest['targets']:
                path = Path(row['path'])
                checks.direct_references([path])
                validate_target(row, scopes, hash_files=False)
                current = {str(item.Path) for item in folder.Items()}
                if not before_items.issubset(current):
                    raise RuntimeError('Recycle Bin entry disappeared before the next operation')
                before_items = current
                log.write(json.dumps({'phase': 'intent', 'source': str(path)}, ensure_ascii=False) + '\n')
                log.flush()
                op = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
                op.SetOperationFlags(flags)
                op.DeleteItem(shell.SHCreateItemFromParsingName(str(path), None, shell.IID_IShellItem))
                op.PerformOperations()
                if op.GetAnyOperationsAborted() or path.exists():
                    raise RuntimeError(f'Recycle aborted or original remains: {path}')
                deadline, matches = time.monotonic() + 10, []
                while time.monotonic() < deadline:
                    entries = list(folder.Items())
                    after = {str(item.Path) for item in entries}
                    if not before_items.issubset(after):
                        raise RuntimeError('Recycle Bin entry disappeared; stopping')
                    matches = []
                    for entry in entries:
                        if str(entry.Path) in before_items:
                            continue
                        parent = entry.ExtendedProperty('System.Recycle.DeletedFrom')
                        name = entry.ExtendedProperty('System.FileName')
                        if parent and name and Path(str(parent)) / str(name) == path:
                            matches.append(Path(str(entry.Path)))
                    if len(matches) == 1:
                        break
                    time.sleep(.1)
                if len(matches) != 1:
                    raise RuntimeError(f'No unique recoverable Recycle Bin item: {path}')
                destination = matches[0]
                files, dirs = checks.tree(destination)
                expected_files = {
                    str(destination / Path(item['path']).relative_to(path)): item for item in row['files']
                }
                expected_dirs = {str(destination / Path(item).relative_to(path)) for item in row['directories']}
                if files != set(expected_files) or dirs != expected_dirs:
                    raise RuntimeError(f'Recycled directory contents differ: {path}')
                for file, item in expected_files.items():
                    if Path(file).stat().st_size != item['size'] or checks.digest(file) != item['sha256']:
                        raise RuntimeError(f'Recycled file hash differs: {item["path"]}')
                record = {'phase': 'verified', 'source': str(path), 'recyclePath': str(destination),
                          'files': len(expected_files), 'bytes': row['bytes'], 'hashesVerified': True}
                done.append(record)
                before_items = after
                log.write(json.dumps(record, ensure_ascii=False) + '\n')
                log.flush()
                print(f"RECYCLED_VERIFIED {path} files={len(expected_files)}", flush=True)
        current = {str(item.Path) for item in folder.Items()}
        if (len(done) != 27 or sum(x['files'] for x in done) != 11632
                or any(path.exists() for path in targets) or not before_items.issubset(current)):
            raise RuntimeError('Final inventory differs')
        checks.direct_references(targets)
        result = {'status': 'RECYCLE_VERIFIED', 'targets': len(done), 'files': sum(x['files'] for x in done),
                  'bytes': manifest['totalBytes'], 'entries': done, 'manifestSha256': EXPECTED_MANIFEST,
                  'priorRecycleItemsPreserved': True, 'permanentDeletionFallbackUsed': False,
                  'restoreOperationTested': False, 'recycleCapacityBefore': capacity}
        with (AUDIT / 'cleanup-result.json').open('x', encoding='utf-8') as out:
            json.dump(result, out, ensure_ascii=False, indent=2)
        print('RECYCLE_VERIFIED targets=27 files=11632 originalsAbsent=true hashes=11632', flush=True)
    except Exception as error:
        result = {'error': str(error), 'completed': done, 'originalExists': {str(path): path.exists() for path in targets},
                  'permanentDeletionFallbackUsed': False}
        with (AUDIT / 'cleanup-failure.json').open('x', encoding='utf-8') as out:
            json.dump(result, out, ensure_ascii=False, indent=2)
        raise


if __name__ == '__main__':
    main()
