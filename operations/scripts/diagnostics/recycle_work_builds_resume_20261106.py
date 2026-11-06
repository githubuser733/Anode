"""Resume the approved work cleanup after the first item was independently verified.

Default mode is validation only. --execute acts solely on the remaining 25
entries in the unchanged, previously approved manifest.
"""
import argparse
import json
import time
from pathlib import Path

import pythoncom
import win32com.client
from win32com.shell import shell, shellcon

import recycle_work_builds_20261106 as original


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def shell_items(folder):
    entries = list(folder.Items())
    return entries, {str(item.Path) for item in entries}


def recycled_destination(entries, source, exclude=()):
    matches = []
    for item in entries:
        if str(item.Path) in exclude:
            continue
        parent = item.ExtendedProperty('System.Recycle.DeletedFrom')
        name = item.ExtendedProperty('System.FileName')
        if parent and name and Path(str(parent)) / str(name) == source:
            matches.append(Path(str(item.Path)))
    if len(matches) != 1:
        raise RuntimeError(f'Expected one matching Recycle Bin item for {source}, got {len(matches)}')
    return matches[0]


def verify_recycled(target, destination):
    source = Path(target['path'])
    files, directories = original.tree(destination)
    expected_files = {
        str(destination / Path(item['path']).relative_to(source)): item
        for item in target['files']
    }
    expected_dirs = {
        str(destination / Path(item).relative_to(source))
        for item in target['directories']
    }
    if files != set(expected_files) or directories != expected_dirs:
        raise RuntimeError(f'Recycled membership differs: {source}')
    for file, item in expected_files.items():
        if Path(file).stat().st_size != item['size'] or original.digest(file) != item['sha256']:
            raise RuntimeError(f'Recycled file differs: {item["path"]}')


def verify_original(target, full_hash=True):
    source = Path(target['path'])
    work = original.WORK.resolve(strict=True)
    if source.resolve(strict=True) != source or not source.is_relative_to(work) or source in (work, original.ROOT, Path(source.anchor)):
        raise RuntimeError(f'Target escaped approved scope: {source}')
    parent = source.parent
    while parent != work:
        if getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400:
            raise RuntimeError(f'Reparse ancestor refused: {parent}')
        parent = parent.parent
    files, directories = original.tree(source)
    if files != {item['path'] for item in target['files']} or directories != set(target['directories']):
        raise RuntimeError(f'Target membership changed: {source}')
    for item in target['files']:
        file = Path(item['path'])
        info = file.stat()
        if info.st_size != item['size'] or info.st_mtime_ns != item['mtimeNs']:
            raise RuntimeError(f'Target changed: {file}')
        original.exclusive_read_probe(file)
        if full_hash and original.digest(file) != item['sha256']:
            raise RuntimeError(f'Target hash changed: {file}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if original.digest(original.MANIFEST) != original.MANIFEST_HASH:
        raise RuntimeError('Approved manifest hash changed')
    manifest = read_json(original.MANIFEST)
    if (manifest['targetCount'], manifest['fileCount'], manifest['totalBytes']) != (26, 13078, 4952471602):
        raise RuntimeError('Approved manifest totals changed')
    receipt = read_json(original.RECEIPT)
    if not receipt.get('activeUserReplyVerified') or receipt.get('manifestSha256') != original.MANIFEST_HASH:
        raise RuntimeError('Current approval missing or does not match')
    failure = read_json(original.AUDIT / 'cleanup-failure.json')
    log = (original.AUDIT / 'recycle-execution.jsonl').read_text(encoding='utf-8').splitlines()
    targets = manifest['targets']
    first, rest = targets[0], targets[1:]
    if (len(log) != 1 or json.loads(log[0]) != {'phase': 'intent', 'source': first['path']}
            or failure.get('error') != 'Pre-existing Recycle Bin entry disappeared; stopping'
            or failure.get('completed') != []):
        raise RuntimeError('Prior operation history differs from expected single-item stop')
    if Path(first['path']).exists() or any(not Path(row['path']).is_dir() for row in rest):
        raise RuntimeError('Original paths do not match the expected one-moved, 25-remaining state')
    if len(rest) != 25:
        raise RuntimeError('Unexpected number of remaining targets')
    result_path = original.AUDIT / 'cleanup-resume-result.json'
    run_log = original.AUDIT / 'recycle-resume-execution.jsonl'
    failure_path = original.AUDIT / 'cleanup-resume-failure.json'
    if args.execute and any(path.exists() for path in (result_path, run_log, failure_path)):
        raise RuntimeError('Resume was already attempted; refusing replay')
    active = read_json(original.ROOT / 'config/anode-local-mcp.json')
    active_exe = (original.ROOT / active['executable']).resolve(strict=True)
    if (original.digest(active_exe) != active['sha256']
            or any(active_exe == Path(item['path']) or Path(item['path']) in active_exe.parents for item in targets)):
        raise RuntimeError('Active Anode binary changed or overlaps cleanup')
    refs = original.direct_references([Path(item['path']) for item in rest])
    original.search_current_and_historic_references()
    capacity = original.recycle_capacity(sum(row['bytes'] for row in rest))
    folder = win32com.client.Dispatch('Shell.Application').NameSpace(shellcon.CSIDL_BITBUCKET)
    if folder is None:
        raise RuntimeError('Recycle Bin unavailable')
    entries, baseline = shell_items(folder)
    first_destination = recycled_destination(entries, Path(first['path']))
    verify_recycled(first, first_destination)
    print(f'FIRST_RECYCLED_VERIFIED files={first["fileCount"]} bytes={first["bytes"]}', flush=True)
    for item in rest:
        verify_original(item)
        print(f'REMAINING_VALIDATED {item["path"]} files={item["fileCount"]}', flush=True)
    # Capture a fresh baseline only after all validations and require it to stay stable.
    entries, baseline = shell_items(folder)
    if str(first_destination) not in baseline:
        raise RuntimeError('First recovered item is no longer in Recycle Bin')
    time.sleep(2)
    _, second = shell_items(folder)
    if baseline != second:
        raise RuntimeError('Recycle Bin changed during preflight')
    flags = (0x00080000 | shellcon.FOFX_EARLYFAILURE | shellcon.FOF_NOERRORUI
             | shellcon.FOF_SILENT | shellcon.FOF_WANTNUKEWARNING | shellcon.FOF_NO_CONNECTED_ELEMENTS)
    probe = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
    probe.SetOperationFlags(flags)
    print(f'RESUME_PREFLIGHT_OK remaining=25 files={sum(x["fileCount"] for x in rest)} baselineItems={len(baseline)} processFieldsUnavailable={refs["processFieldsUnavailable"]}', flush=True)
    if not args.execute:
        print('VALIDATION_ONLY; no file operation submitted', flush=True)
        return
    before_items = baseline
    completed = [{'source': first['path'], 'recyclePath': str(first_destination),
                  'fileCount': first['fileCount'], 'bytes': first['bytes'], 'verifiedFromPreviousAttempt': True}]
    try:
        with run_log.open('x', encoding='utf-8') as stream:
            for target in rest:
                source = Path(target['path'])
                original.direct_references([source])
                verify_original(target, full_hash=False)
                _, current_before = shell_items(folder)
                if not before_items.issubset(current_before):
                    raise RuntimeError('Recycle Bin entry disappeared before the next operation')
                before_items = current_before
                stream.write(json.dumps({'phase': 'intent', 'source': str(source)}, ensure_ascii=False) + '\n')
                stream.flush()
                op = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
                op.SetOperationFlags(flags)
                op.DeleteItem(shell.SHCreateItemFromParsingName(str(source), None, shell.IID_IShellItem))
                op.PerformOperations()
                if op.GetAnyOperationsAborted() or source.exists():
                    raise RuntimeError(f'Recycle aborted or original remains: {source}')
                deadline = time.monotonic() + 10
                while True:
                    entries, after = shell_items(folder)
                    if not before_items.issubset(after):
                        raise RuntimeError('Recycle Bin entry disappeared; stopping')
                    try:
                        destination = recycled_destination(entries, source, before_items)
                        break
                    except RuntimeError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.1)
                verify_recycled(target, destination)
                record = {'phase': 'verified', 'source': str(source), 'recyclePath': str(destination),
                          'fileCount': target['fileCount'], 'bytes': target['bytes'], 'hashesVerified': True}
                completed.append(record)
                before_items = after
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
                stream.flush()
                print(f'RECYCLED_VERIFIED {source} files={target["fileCount"]}', flush=True)
        _, final_paths = shell_items(folder)
        if (len(completed) != 26 or sum(row['fileCount'] for row in completed) != 13078
                or any(Path(row['path']).exists() for row in targets) or not before_items.issubset(final_paths)):
            raise RuntimeError('Final original or recycle inventory differs')
        original.direct_references([Path(item['path']) for item in targets])
        result = {'result': 'RECYCLE_RESUME_VERIFIED', 'targets': len(completed),
                  'files': sum(row['fileCount'] for row in completed), 'bytes': manifest['totalBytes'],
                  'manifestSha256': original.MANIFEST_HASH, 'entries': completed,
                  'firstAttemptStoppedAfterRecycleBinChanged': True,
                  'userReportedManualRecycleBinOrDiskCleanup': True,
                  'preciseCauseOfEarlierDisappearanceUnverified': True,
                  'currentBaselinePreserved': True, 'permanentDeletionFallbackUsed': False}
        with result_path.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print('RECYCLE_RESUME_VERIFIED targets=26 files=13078 originalsAbsent=true hashes=13078', flush=True)
    except Exception as error:
        failure = {'error': str(error), 'completed': completed,
                   'originalExists': {row['path']: Path(row['path']).exists() for row in targets},
                   'permanentDeletionFallbackUsed': False}
        with failure_path.open('x', encoding='utf-8') as stream:
            json.dump(failure, stream, ensure_ascii=False, indent=2)
        raise


if __name__ == '__main__':
    main()
