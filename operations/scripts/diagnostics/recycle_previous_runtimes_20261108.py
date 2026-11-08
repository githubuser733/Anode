"""Recycle exactly the two previous runtimes approved by the user on 2026-11-08."""
import argparse
import importlib.util
import json
from pathlib import Path
import time

OPS = Path(__file__).resolve().parents[2]
ROOT = OPS.parent.parent
REPORT = OPS / 'artifacts/current-only-20261108'
TARGETS = [OPS / 'work/anode-screenshot-viewer-20261106-734d2b',
           ROOT / 'ghost/local/panel/previous']


def write_new(name, value):
    with (REPORT / name).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    approval = json.loads((REPORT / 'approval.json').read_text(encoding='utf-8'))
    if not approval['activeUserReplyVerified'] or approval['targets'] != list(map(str, TARGETS)):
        raise RuntimeError('APPROVAL_MISMATCH')
    if (REPORT / 'execution.jsonl').exists():
        raise RuntimeError('BATCH_ALREADY_ATTEMPTED: inspect receipts; never replay')
    spec = importlib.util.spec_from_file_location('recycle_helper', OPS / 'scripts/Recycle-ApprovedAnodeVersions-20261106.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    rows = []
    for target in TARGETS:
        if target.resolve(strict=True) != target or not target.is_relative_to(ROOT) or target in (ROOT, OPS, Path(target.anchor)):
            raise RuntimeError('OUTSIDE_APPROVED_SCOPE')
        files, _ = helper.tree(target)
        rows.append({'targetDirectory': str(target), 'files': [
            {'path': name, 'size': Path(name).stat().st_size, 'sha256': helper.digest(name)} for name in sorted(files)]})
    count = sum(len(row['files']) for row in rows)
    size = sum(item['size'] for row in rows for item in row['files'])
    if (count, size) != (6, 76871357):
        raise RuntimeError('DISPLAYED_INVENTORY_CHANGED')
    before_processes = helper.protected_processes(TARGETS)
    capacity = helper.recycle_capacity(size)
    shell, shellcon, pythoncom = helper.shell, helper.shellcon, helper.pythoncom
    app = helper.win32com.client.Dispatch('Shell.Application')
    recycle = app.NameSpace(shellcon.CSIDL_BITBUCKET)
    if recycle is None:
        raise RuntimeError('RECYCLE_BIN_UNAVAILABLE')
    before_items = {str(item.Path) for item in recycle.Items()}
    flags = (0x00080000 | shellcon.FOFX_EARLYFAILURE | shellcon.FOF_NOERRORUI | shellcon.FOF_SILENT
             | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_WANTNUKEWARNING | shellcon.FOF_NO_CONNECTED_ELEMENTS)
    probe = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
    probe.SetOperationFlags(flags)
    print(f'CURRENT_ONLY_RECYCLE_PREFLIGHT_OK targets=2 files={count} bytes={size}', flush=True)
    if not args.execute:
        return
    write_new('manifest.json', {'targets': rows, 'files': count, 'bytes': size})
    completed = []
    with (REPORT / 'execution.jsonl').open('x', encoding='utf-8') as journal:
        for row in rows:
            journal.write(json.dumps({'phase': 'intent', 'source': row['targetDirectory']}) + '\n')
            journal.flush()
            operation = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
            operation.SetOperationFlags(flags)
            operation.DeleteItem(shell.SHCreateItemFromParsingName(row['targetDirectory'], None, shell.IID_IShellItem))
            operation.PerformOperations()
            if operation.GetAnyOperationsAborted() or Path(row['targetDirectory']).exists():
                raise RuntimeError('RECYCLE_ABORTED_OR_SOURCE_REMAINS')
            deadline = time.monotonic() + 10
            while True:
                items = list(recycle.Items())
                if not before_items.issubset({str(item.Path) for item in items}):
                    raise RuntimeError('PREEXISTING_TRASH_ITEM_DISAPPEARED')
                matches = [str(item.Path) for item in items if str(item.Path) not in before_items
                           and item.ExtendedProperty('System.Recycle.DeletedFrom')
                           and item.ExtendedProperty('System.FileName')
                           and Path(str(item.ExtendedProperty('System.Recycle.DeletedFrom'))) /
                           str(item.ExtendedProperty('System.FileName')) == Path(row['targetDirectory'])]
                if len(matches) == 1:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('UNIQUE_RECOVERABLE_ENTRY_NOT_VERIFIED')
                time.sleep(.1)
            verified = helper.verify_recycled(row, matches[0])
            result = {'source': row['targetDirectory'], 'recyclePath': matches[0], 'files': verified,
                      'originalAbsent': True, 'contentHashesVerified': True}
            completed.append(result)
            journal.write(json.dumps({'phase': 'verified', **result}) + '\n')
            journal.flush()
            print(f'RECYCLED_HASH_VERIFIED files={verified}', flush=True)
    after = helper.protected_processes(TARGETS)
    if any(after.get(pid) != value for pid, value in before_processes.items()):
        raise RuntimeError('PROTECTED_PROCESS_CHANGED')
    if any(path.exists() for path in TARGETS):
        raise RuntimeError('ORIGINAL_PATH_REMAINS')
    write_new('result.json', {'result': 'RECYCLE_VERIFIED', 'entries': completed, 'files': count, 'bytes': size,
                             'capacityBefore': capacity, 'permanentDeletionUsed': False,
                             'recycleBinEmptied': False, 'preExistingRecycleEntriesPreserved': True})
    print('RECYCLE_VERIFIED targets=2 files=6 originalsAbsent=true hashes=6')


if __name__ == '__main__':
    main()
