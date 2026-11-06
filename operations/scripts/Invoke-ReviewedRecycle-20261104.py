"""One-shot execution of the exact 12-item list approved in this chat; defaults to validation."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import pythoncom
import win32com.client
from win32com.shell import shell, shellcon

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / 'artifacts/ghost-reorganization-20261104-2bdd75c9'
PLAN = AUDIT / 'cleanup-plan.json'
EXPECTED_HASH = 'a135fba31ae50a88faa58f0e152e473a0b62c126228502db83cfc05b9f05e891'
EXPECTED_NAMES = {
    'trope-built-3a1fbf0917fa', 'trope-source-3a1fbf0917fa', 'trope-trial-config',
    'trope-cua-main-20261030.zip', 'trope-bridge-port.json', 'trope-target-process.json',
    'mousemux-installer-inspect', 'mousemux-payload-inspect',
    'mousemux-v3-setup-3.0.19-complete.exe.parts',
    'mousemux-v3-setup-3.0.19-verified.exe.small-parts',
    'mousemux-v3-setup-3.0.19-verified.exe', 'mousemux-v3-setup-3.0.19.exe',
}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def write_new(name, data):
    with (AUDIT / name).open('x', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def tree(path):
    files, directories, pending = set(), set(), [path]
    while pending:
        item = pending.pop()
        st = item.lstat()
        if item.is_symlink() or getattr(st, 'st_file_attributes', 0) & 1024:
            raise RuntimeError(f'Reparse point: {item}')
        if item.is_dir():
            directories.add(str(item))
            pending.extend(item.iterdir())
        else:
            files.add(str(item))
    return files, directories


def process_snapshot(targets, scope):
    protected = {}
    wmi = win32com.client.GetObject('winmgmts:')
    for p in wmi.ExecQuery('SELECT Name,ProcessId,SessionId,ExecutablePath,CommandLine,CreationDate FROM Win32_Process'):
        name = str(p.Name).lower()
        exe = str(p.ExecutablePath) if p.ExecutablePath else None
        command = str(p.CommandLine or '').replace('\\', '/').casefold()
        if exe and any(Path(exe) == t or t in Path(exe).parents for t in targets):
            raise RuntimeError(f'A target executable is running: {exe}')
        if any(str(t).replace('\\', '/').casefold() in command for t in targets):
            raise RuntimeError(f'A running process references a target: PID {p.ProcessId}')
        if name.startswith('trope') or ('trope' in command and str(scope).replace('\\', '/').casefold() in command):
            raise RuntimeError(f'A retired Trope process is active: PID {p.ProcessId}')
        if name == 'anode.exe' or name == 'mousemux-v3-service.exe':
            protected[str(p.ProcessId)] = {'name': name, 'session': int(p.SessionId), 'executable': exe,
                                           'creationDate': str(p.CreationDate or '')}
    return protected


def verify_recycled(target, destination):
    destination = Path(destination)
    actual, directories = tree(destination)
    expected = {}
    for entry in target['files']:
        recovered = destination / Path(entry['path']).relative_to(Path(target['path'])) if target['isDirectory'] else destination
        expected[str(recovered)] = entry
    if actual != set(expected):
        raise RuntimeError(f'Recycled file membership mismatch: {target["path"]}')
    for path, entry in expected.items():
        if Path(path).stat().st_size != entry['bytes'] or digest(path) != entry['sha256']:
            raise RuntimeError(f'Recycled content mismatch: {entry["path"]}')
    return len(expected)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    execute = parser.parse_args().execute
    if digest(PLAN) != EXPECTED_HASH:
        raise RuntimeError('Reviewed plan hash changed')
    plan = json.loads(PLAN.read_text(encoding='utf-8-sig'))
    receipt = json.loads((AUDIT / 'approval-received.json').read_text(encoding='utf-8-sig'))
    if receipt.get('activeUserReplyVerified') is not True or receipt.get('manifestSha256') != EXPECTED_HASH:
        raise RuntimeError('Current-batch approval receipt is missing or differs')
    if (len(plan['targets']), plan['fileCount'], plan['totalBytes']) != (12, 353, 285144514):
        raise RuntimeError('Reviewed counts differ')
    scope = Path(plan['scope'])
    if scope.resolve(strict=True) != scope or scope == Path(scope.anchor) or scope == ROOT:
        raise RuntimeError('Invalid source scope')
    targets = [Path(t['path']) for t in plan['targets']]
    if {p.name for p in targets} != EXPECTED_NAMES or len(set(targets)) != 12:
        raise RuntimeError('Target names differ from the displayed list')
    for target in plan['targets']:
        path = Path(target['path'])
        if path.resolve(strict=True) != path or path.parent != scope or ROOT in path.parents:
            raise RuntimeError(f'Target escaped the approved source scope: {path}')
        files, directories = tree(path)
        if files != {f['path'] for f in target['files']} or directories != set(target['directories']):
            raise RuntimeError(f'Target membership changed: {path}')
        for entry in target['files']:
            current = Path(entry['path']).stat()
            if current.st_size != entry['bytes'] or current.st_mtime_ns != entry['mtimeNs'] or digest(entry['path']) != entry['sha256']:
                raise RuntimeError(f'Target content or identity changed: {entry["path"]}')
    config_target = next(t for t in plan['targets'] if Path(t['path']).name == 'trope-trial-config')
    for entry in config_target['files']:
        copy = AUDIT / 'retained-trope-config' / Path(entry['path']).relative_to(Path(config_target['path']))
        if digest(copy) != entry['sha256']:
            raise RuntimeError('Retained Trope configuration changed')
    protected_before = process_snapshot(targets, scope)
    app = win32com.client.Dispatch('Shell.Application')
    recycle = app.NameSpace(shellcon.CSIDL_BITBUCKET)
    if recycle is None:
        raise RuntimeError('Recycle Bin is unavailable; permanent deletion is forbidden')
    before_items = {str(item.Path) for item in recycle.Items()}
    flags = (0x00080000 | shellcon.FOFX_EARLYFAILURE | shellcon.FOF_NOERRORUI
             | shellcon.FOF_SILENT | shellcon.FOF_NOCONFIRMATION
             | shellcon.FOF_WANTNUKEWARNING | shellcon.FOF_NO_CONNECTED_ELEMENTS)
    probe = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
    probe.SetOperationFlags(flags)
    print(f'RECYCLE_PRECHECK_OK targets=12 files=353 bytes=285144514 protectedProcesses={len(protected_before)}', flush=True)
    if not execute:
        print('VALIDATION_ONLY; no DeleteItem request submitted', flush=True)
        return
    if (AUDIT / 'cleanup-result.json').exists() or (AUDIT / 'recycle-execution.jsonl').exists():
        raise RuntimeError('This batch has already been attempted; refusing replay')
    write_new('cleanup-protected-processes-before.json', protected_before)
    completed, verified_count = [], 0
    try:
        with (AUDIT / 'recycle-execution.jsonl').open('x', encoding='utf-8') as log:
            for target in plan['targets']:
                operation = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
                operation.SetOperationFlags(flags)
                operation.DeleteItem(shell.SHCreateItemFromParsingName(target['path'], None, shell.IID_IShellItem))
                operation.PerformOperations()
                if operation.GetAnyOperationsAborted() or Path(target['path']).exists():
                    raise RuntimeError(f'Recycle was aborted or original remains: {target["path"]}')
                deadline, candidates = time.monotonic() + 5, []
                while time.monotonic() < deadline:
                    current_items = list(recycle.Items())
                    current_paths = {str(item.Path) for item in current_items}
                    if not before_items.issubset(current_paths):
                        raise RuntimeError('A pre-existing Recycle Bin entry disappeared; stop and inspect')
                    candidates = []
                    for item in current_items:
                        if str(item.Path) in before_items:
                            continue
                        deleted_from = item.ExtendedProperty('System.Recycle.DeletedFrom')
                        original_name = item.ExtendedProperty('System.FileName')
                        if deleted_from and original_name and Path(str(deleted_from)) / str(original_name) == Path(target['path']):
                            candidates.append(str(item.Path))
                    if len(candidates) == 1:
                        break
                    time.sleep(0.1)
                if len(candidates) != 1:
                    raise RuntimeError(f'Cannot uniquely confirm the recoverable entry: {target["path"]}')
                count = verify_recycled(target, candidates[0])
                verified_count += count
                record = {'source': target['path'], 'recyclePath': candidates[0], 'files': count,
                          'originalAbsent': True, 'contentHashesVerified': True}
                completed.append(record)
                log.write(json.dumps(record, ensure_ascii=False) + '\n')
                log.flush()
                print(f'RECYCLED_AND_HASH_VERIFIED files={count} source={target["path"]}', flush=True)
        protected_after = process_snapshot(targets, scope)
        if any(protected_after.get(pid) != value for pid, value in protected_before.items()):
            raise RuntimeError('A protected Anode or MouseMux service process changed')
        if verified_count != 353 or any(p.exists() for p in targets):
            raise RuntimeError('Final recycled inventory or original-path check failed')
        result = {'status': 'RECYCLE_VERIFIED', 'targets': 12, 'fileCount': 353, 'totalBytes': 285144514,
                  'sha256VerifiedFiles': verified_count, 'entries': completed,
                  'protectedProcessesBefore': protected_before, 'protectedProcessesAfter': protected_after,
                  'preExistingRecycleEntriesPreserved': True, 'permanentDeletionUsed': False,
                  'recycleBinEmptied': False, 'approvalManifestSha256': EXPECTED_HASH}
        write_new('cleanup-result.json', result)
        print('RECYCLE_VERIFIED targets=12 files=353 hashes=353 originalsAbsent=true binRetained=true', flush=True)
    except Exception as error:
        write_new('cleanup-failure.json', {'error': str(error), 'completed': completed,
                  'originalExistence': {str(p): p.exists() for p in targets}, 'permanentDeletionFallbackUsed': False})
        raise


if __name__ == '__main__':
    main()
