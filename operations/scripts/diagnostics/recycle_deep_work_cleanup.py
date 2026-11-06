"""Validate a reviewed work cleanup; execute only with the current user approval."""
import argparse
import json
from pathlib import Path
import time

import pythoncom
import win32com.client
from win32com.shell import shell, shellcon

import recycle_work_builds_20261106 as helper

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work'
AUDIT = ROOT / 'artifacts/work-deep-cleanup-audit-20261106-68e2d1c0/v4'
MANIFEST = AUDIT / 'cleanup-manifest.json'
RECEIPT = AUDIT / 'current-batch-approval.json'
EXPECTED = '49b16cfd47888d2454659f03bcd7dab3327a09d717b361a098ae19a1131d73ea'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def validate(row, full_hash=True):
    path = Path(row['path'])
    if (path.resolve(strict=True) != path or not path.is_relative_to(WORK)
            or path in (WORK, ROOT, Path(path.anchor))):
        raise RuntimeError(f'Unsafe target: {path}')
    parent = path.parent
    while parent != WORK:
        if getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400:
            raise RuntimeError(f'Reparse ancestor: {parent}')
        parent = parent.parent
    files, dirs = helper.tree(path)
    if files != {x['path'] for x in row['files']} or dirs != set(row['directories']):
        raise RuntimeError(f'Target membership changed: {path}')
    if row['kind'] != ('directory' if path.is_dir() else 'file'):
        raise RuntimeError(f'Target type changed: {path}')
    for item in row['files']:
        file = Path(item['path'])
        s = file.stat()
        if (s.st_size, s.st_mtime_ns) != (item['size'], item['mtimeNs']):
            raise RuntimeError(f'File metadata changed: {file}')
        helper.exclusive_read_probe(file)
        if full_hash and helper.digest(file) != item['sha256']:
            raise RuntimeError(f'File hash changed: {file}')
    if row['category'] == 'duplicate-extension':
        primary = Path(row['duplicateOf'])
        expected = {str(Path(x['path']).relative_to(path)) for x in row['files']}
        actual = {str(x.relative_to(primary)) for x in primary.rglob('*') if x.is_file()}
        if expected != actual:
            raise RuntimeError(f'Primary browser extension changed: {primary}')
        for item in row['files']:
            copy = primary / Path(item['path']).relative_to(path)
            if copy.stat().st_size != item['size'] or helper.digest(copy) != item['sha256']:
                raise RuntimeError(f'Primary browser extension differs: {copy}')
    if row['category'] in {'historical-script', 'historical-run-evidence', 'historical-source-evidence'}:
        for item in row['files']:
            backup = Path(row['preservedCopyRoot']) / Path(item['path']).relative_to(WORK)
            if not backup.is_file() or helper.digest(backup) != item['sha256']:
                raise RuntimeError(f'Historical backup differs: {backup}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if helper.digest(MANIFEST) != EXPECTED:
        raise RuntimeError('Reviewed manifest hash changed')
    manifest = read_json(MANIFEST)
    if (manifest['scope'], manifest['targetCount'], manifest['fileCount'], manifest['totalBytes']) != (
            str(WORK), 58, 3945, 1006518492):
        raise RuntimeError('Reviewed manifest totals or scope differ')
    targets = [Path(x['path']) for x in manifest['targets']]
    if len(set(targets)) != 58 or any(a != b and a in b.parents for a in targets for b in targets):
        raise RuntimeError('Overlapping targets')
    if args.execute:
        receipt = read_json(RECEIPT)
        if not receipt.get('activeUserReplyVerified') or receipt.get('manifestSha256') != EXPECTED:
            raise RuntimeError('Current-batch approval missing or mismatched')
        if any((AUDIT / name).exists() for name in ('recycle-run.jsonl', 'cleanup-result.json', 'cleanup-failure.json')):
            raise RuntimeError('This batch was already attempted; refusing replay')
    active = read_json(ROOT / 'config/anode-local-mcp.json')
    binary = (ROOT / active['executable']).resolve(strict=True)
    if helper.digest(binary) != active['sha256'] or any(binary == p or p in binary.parents for p in targets):
        raise RuntimeError('Active Anode changed or overlaps targets')
    refs = helper.direct_references(targets)
    capacity = helper.recycle_capacity(manifest['totalBytes'])
    for row in manifest['targets']:
        validate(row)
        print(f"VALIDATED {row['category']} {row['path']} files={row['fileCount']}", flush=True)
    app = win32com.client.Dispatch('Shell.Application')
    folder = app.NameSpace(shellcon.CSIDL_BITBUCKET)
    if folder is None:
        raise RuntimeError('Recycle Bin unavailable')
    before = {str(entry.Path) for entry in folder.Items()}
    flags = (0x00080000 | shellcon.FOFX_EARLYFAILURE | shellcon.FOF_NOERRORUI
             | shellcon.FOF_SILENT | shellcon.FOF_WANTNUKEWARNING | shellcon.FOF_NO_CONNECTED_ELEMENTS)
    probe = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
    probe.SetOperationFlags(flags)
    print(f"DEEP_CLEANUP_PREFLIGHT_OK targets=58 files=3945 bytes=1006518492 processFieldsUnavailable={refs['processFieldsUnavailable']}", flush=True)
    if not args.execute:
        print('VALIDATION_ONLY; no file operation submitted', flush=True)
        return
    completed = []
    try:
        with (AUDIT / 'recycle-run.jsonl').open('x', encoding='utf-8') as log:
            for row in manifest['targets']:
                path = Path(row['path'])
                helper.direct_references([path])
                validate(row, full_hash=False)
                current = {str(entry.Path) for entry in folder.Items()}
                if not before.issubset(current):
                    raise RuntimeError('Existing Recycle Bin entry disappeared; stopping')
                before = current
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
                    after = {str(entry.Path) for entry in entries}
                    if not before.issubset(after):
                        raise RuntimeError('Existing Recycle Bin entry disappeared; stopping')
                    matches = []
                    for entry in entries:
                        if str(entry.Path) in before:
                            continue
                        parent = entry.ExtendedProperty('System.Recycle.DeletedFrom')
                        name = entry.ExtendedProperty('System.FileName')
                        if parent and name and Path(str(parent)) / str(name) == path:
                            matches.append(Path(str(entry.Path)))
                    if len(matches) == 1:
                        break
                    time.sleep(.1)
                if len(matches) != 1:
                    raise RuntimeError(f'No unique recoverable item: {path}')
                destination = matches[0]
                files, dirs = helper.tree(destination)
                if row['kind'] == 'file':
                    expected_files = {str(destination): row['files'][0]}
                    expected_dirs = set()
                else:
                    expected_files = {str(destination / Path(item['path']).relative_to(path)): item for item in row['files']}
                    expected_dirs = {str(destination / Path(item).relative_to(path)) for item in row['directories']}
                if files != set(expected_files) or dirs != expected_dirs:
                    raise RuntimeError(f'Recycled membership differs: {path}')
                for file, item in expected_files.items():
                    if Path(file).stat().st_size != item['size'] or helper.digest(file) != item['sha256']:
                        raise RuntimeError(f'Recycled file differs: {item["path"]}')
                record = {'phase': 'verified', 'source': str(path), 'recyclePath': str(destination),
                          'kind': row['kind'], 'files': len(expected_files), 'bytes': row['bytes'], 'hashesVerified': True}
                completed.append(record)
                before = after
                log.write(json.dumps(record, ensure_ascii=False) + '\n')
                log.flush()
                print(f"RECYCLED_VERIFIED {path} files={len(expected_files)}", flush=True)
        after = {str(entry.Path) for entry in folder.Items()}
        if (len(completed) != 58 or sum(x['files'] for x in completed) != 3945
                or any(path.exists() for path in targets) or not before.issubset(after)):
            raise RuntimeError('Final inventory differs')
        helper.direct_references(targets)
        result = {'status': 'RECYCLE_VERIFIED', 'targets': len(completed), 'files': sum(x['files'] for x in completed),
                  'bytes': manifest['totalBytes'], 'entries': completed, 'manifestSha256': EXPECTED,
                  'priorRecycleItemsPreserved': True, 'permanentDeletionFallbackUsed': False,
                  'restoreOperationTested': False, 'recycleCapacityBefore': capacity}
        with (AUDIT / 'cleanup-result.json').open('x', encoding='utf-8') as out:
            json.dump(result, out, ensure_ascii=False, indent=2)
        print('RECYCLE_VERIFIED targets=58 files=3945 originalsAbsent=true hashes=3945', flush=True)
    except Exception as error:
        failure = {'error': str(error), 'completed': completed, 'originalExists': {str(p): p.exists() for p in targets},
                   'permanentDeletionFallbackUsed': False}
        with (AUDIT / 'cleanup-failure.json').open('x', encoding='utf-8') as out:
            json.dump(failure, out, ensure_ascii=False, indent=2)
        raise


if __name__ == '__main__':
    main()
