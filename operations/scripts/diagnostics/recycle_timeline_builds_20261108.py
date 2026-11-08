"""Recycle a fixed, separately approved 2026-11-08 build batch; never widen its target list."""
import argparse
import importlib.util
import json
from pathlib import Path
import time

OPS = Path(__file__).resolve().parents[2]
EVIDENCE = OPS / 'artifacts/anode-slim-20261108'
SCOPE = OPS / 'artifacts/timeline-timeout-20261107'
NAMES = ('build', 'instrumented', 'regression-red')


def write_new(name, value):
    with (EVIDENCE / name).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    global EVIDENCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--batch', choices=['transitional', 'validated-caches'], default='transitional')
    args = parser.parse_args()
    if args.batch == 'validated-caches':
        EVIDENCE = EVIDENCE / 'verified-cache-cleanup'
        targets = [OPS / 'artifacts/timeline-timeout-20261107/verified-build',
                   OPS / 'artifacts/restructure-20261106/build-new-paths',
                   OPS / 'artifacts/anode-slim-20261108/build']
        expected = (3, 1508, 568355458)
    else:
        targets = [SCOPE / name for name in NAMES]
        expected = (3, 1503, 558265721)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('verified_recycle', OPS / 'scripts/Recycle-ApprovedAnodeVersions-20261106.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    approval = json.loads((EVIDENCE / 'cleanup-approval.json').read_text('utf-8-sig'))
    if not approval.get('activeUserReplyVerified') or approval['targets'] != [str(path) for path in targets]:
        raise RuntimeError('Current approval does not cover these targets')
    if (EVIDENCE / 'recycle-execution.jsonl').exists():
        raise RuntimeError('This batch was already attempted; inspect its receipt instead of replaying')
    manifest = []
    for target in targets:
        if target.resolve(strict=True) != target or not target.is_relative_to(OPS / 'artifacts') or target in (OPS, OPS / 'artifacts', SCOPE, Path(target.anchor)):
            raise RuntimeError('Target escaped the displayed scope')
        files, _ = helper.tree(target)
        records = [{'path': name, 'size': Path(name).stat().st_size, 'sha256': helper.digest(name)} for name in sorted(files)]
        manifest.append({'targetDirectory': str(target), 'files': records})
    count = sum(len(row['files']) for row in manifest)
    size = sum(item['size'] for row in manifest for item in row['files'])
    if (len(manifest), count, size) != expected:
        raise RuntimeError('Target count or size changed since approval')
    before_processes = helper.protected_processes(targets)
    capacity = helper.recycle_capacity(size)
    shell, shellcon, pythoncom = helper.shell, helper.shellcon, helper.pythoncom
    app = helper.win32com.client.Dispatch('Shell.Application')
    recycle = app.NameSpace(shellcon.CSIDL_BITBUCKET)
    if recycle is None:
        raise RuntimeError('Recycle Bin unavailable')
    before_items = {str(item.Path) for item in recycle.Items()}
    flags = (0x00080000 | shellcon.FOFX_EARLYFAILURE | shellcon.FOF_NOERRORUI | shellcon.FOF_SILENT
             | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_WANTNUKEWARNING | shellcon.FOF_NO_CONNECTED_ELEMENTS)
    operation = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
    operation.SetOperationFlags(flags)
    print(f'TIMELINE_RECYCLE_PREFLIGHT_OK targets=3 files={count} bytes={size}', flush=True)
    if not args.execute:
        return
    write_new('cleanup-manifest.json', {'targets': manifest, 'files': count, 'bytes': size})
    completed = []
    try:
        with (EVIDENCE / 'recycle-execution.jsonl').open('x', encoding='utf-8') as journal:
            for row in manifest:
                journal.write(json.dumps({'phase': 'intent', 'source': row['targetDirectory']}) + '\n')
                journal.flush()
                operation = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
                operation.SetOperationFlags(flags)
                operation.DeleteItem(shell.SHCreateItemFromParsingName(row['targetDirectory'], None, shell.IID_IShellItem))
                operation.PerformOperations()
                if operation.GetAnyOperationsAborted() or Path(row['targetDirectory']).exists():
                    raise RuntimeError('Recycle aborted or original path remains')
                deadline, candidates = time.monotonic() + 10, []
                while time.monotonic() < deadline:
                    items = list(recycle.Items())
                    if not before_items.issubset({str(item.Path) for item in items}):
                        raise RuntimeError('A pre-existing Recycle Bin item disappeared')
                    candidates = [str(item.Path) for item in items if str(item.Path) not in before_items
                                  and item.ExtendedProperty('System.Recycle.DeletedFrom')
                                  and item.ExtendedProperty('System.FileName')
                                  and Path(str(item.ExtendedProperty('System.Recycle.DeletedFrom'))) / str(item.ExtendedProperty('System.FileName')) == Path(row['targetDirectory'])]
                    if len(candidates) == 1:
                        break
                    time.sleep(.1)
                if len(candidates) != 1:
                    raise RuntimeError('A unique recoverable entry could not be verified')
                verified = helper.verify_recycled(row, candidates[0])
                receipt = {'source': row['targetDirectory'], 'recyclePath': candidates[0], 'files': verified,
                           'originalAbsent': True, 'contentHashesVerified': True}
                completed.append(receipt)
                journal.write(json.dumps({'phase': 'verified', **receipt}) + '\n')
                journal.flush()
                print(f'RECYCLED_HASH_VERIFIED files={verified} source={row["targetDirectory"]}', flush=True)
        after_processes = helper.protected_processes(targets)
        if any(after_processes.get(pid) != value for pid, value in before_processes.items()):
            raise RuntimeError('A protected process changed during cleanup')
        if any(path.exists() for path in targets) or sum(row['files'] for row in completed) != count:
            raise RuntimeError('Final inventory differs')
        write_new('cleanup-result.json', {'result': 'RECYCLE_VERIFIED', 'targets': 3, 'files': count, 'bytes': size,
                  'entries': completed, 'capacityBefore': capacity, 'permanentDeletionUsed': False,
                  'recycleBinEmptied': False, 'preExistingRecycleEntriesPreserved': True})
        print(f'RECYCLE_VERIFIED targets=3 files={count} originalsAbsent=true hashes={count}', flush=True)
    except Exception as error:
        write_new('cleanup-failure.json', {'error': str(error), 'completed': completed,
                  'originalExists': {str(path): path.exists() for path in targets}, 'permanentDeletionFallback': False})
        raise


if __name__ == '__main__':
    main()
