"""Recycle only the five Anode builds approved on 2026-11-06; default is read-only validation."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import time
import winreg

import pythoncom
import win32com.client
from win32com.shell import shell, shellcon

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / 'artifacts/anode-screenshot-viewer-20261106-734d2b'
MANIFEST = EVIDENCE / 'old-build-cleanup-manifest.json'
RECEIPT = EVIDENCE / 'live-switch/cleanup-approval.json'
EXPECTED_HASH = 'c5cc181ed698b947e36afc6cb367f76beb561aafeb0c4e2fe0efbbb0955f15d9'
EXPECTED_TARGETS = [
    ROOT / 'work/anode-remember-login-20261105-118f620b/publish',
    ROOT / 'work/anode-remember-login-20261105-118f620b/publish-v2',
    ROOT / 'work/anode-remember-login-20261105-118f620b/publish-v3',
    ROOT / 'work/anode-remember-login-20261105-118f620b/publish-v4',
    ROOT / 'work/anode-login-repair-20261106-7e839c/publish-v5',
]


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_new(name, data):
    with (EVIDENCE / 'live-switch' / name).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def tree(path):
    files, directories, pending = set(), set(), [path]
    while pending:
        item = pending.pop()
        info = item.lstat()
        if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & 1024:
            raise RuntimeError(f'Reparse point refused: {item}')
        if item.is_dir():
            directories.add(str(item))
            pending.extend(item.iterdir())
        else:
            files.add(str(item))
    return files, directories


def protected_processes(targets):
    result = {}
    wmi = win32com.client.GetObject('winmgmts:')
    for process in wmi.ExecQuery('SELECT Name,ProcessId,SessionId,ExecutablePath,CommandLine,CreationDate FROM Win32_Process'):
        executable = str(process.ExecutablePath) if process.ExecutablePath else None
        command = str(process.CommandLine or '').replace('\\', '/').casefold()
        if executable and any(Path(executable) == target or target in Path(executable).parents for target in targets):
            raise RuntimeError(f'A cleanup target contains a running executable: PID {process.ProcessId}')
        if any(str(target).replace('\\', '/').casefold() in command for target in targets):
            raise RuntimeError(f'A running process references a cleanup target: PID {process.ProcessId}')
        if str(process.Name).casefold() in {'anode.exe', 'alpha.exe', 'chatgpt.exe'}:
            result[str(process.ProcessId)] = {'name': str(process.Name), 'session': int(process.SessionId),
                'executable': executable, 'created': str(process.CreationDate or '')}
    return result


def recycle_capacity(target_bytes):
    class Info(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('bytes', ctypes.c_int64), ('items', ctypes.c_int64)]
    info = Info(ctypes.sizeof(Info), 0, 0)
    query = ctypes.windll.shell32.SHQueryRecycleBinW
    query.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(Info)]
    query.restype = ctypes.c_long
    if query('F:\\', ctypes.byref(info)) != 0:
        raise RuntimeError('Could not query the F: Recycle Bin')
    key = r'Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\{bba0fc0a-df87-4b1d-bbe2-7bcae5ebf174}'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as reg:
        nuke = winreg.QueryValueEx(reg, 'NukeOnDelete')[0]
        maximum_mib = winreg.QueryValueEx(reg, 'MaxCapacity')[0]
    if nuke != 0 or info.bytes + target_bytes + 512 * 1048576 >= maximum_mib * 1048576:
        raise RuntimeError('Recycle Bin is disabled or lacks safe capacity')
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\Policies\Explorer') as reg:
                try: disabled = winreg.QueryValueEx(reg, 'NoRecycleFiles')[0]
                except FileNotFoundError: disabled = 0
                if disabled: raise RuntimeError('Windows policy disables recycling')
        except FileNotFoundError:
            pass
    return {'occupiedBytes': info.bytes, 'existingItems': info.items, 'capacityMiB': maximum_mib}


def verify_recycled(target, recycled_path):
    destination = Path(recycled_path)
    actual, _ = tree(destination)
    expected = {}
    for item in target['files']:
        path = destination / Path(item['path']).relative_to(Path(target['targetDirectory']))
        expected[str(path)] = item
    if actual != set(expected):
        raise RuntimeError('Recycled content membership differs')
    for path, item in expected.items():
        if Path(path).stat().st_size != item['size'] or digest(path) != item['sha256']:
            raise RuntimeError(f'Recycled file hash differs: {item["path"]}')
    return len(expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if digest(MANIFEST) != EXPECTED_HASH:
        raise RuntimeError('Approved manifest hash differs')
    receipt = json.loads(RECEIPT.read_text('utf-8-sig'))
    if not receipt.get('activeUserReplyVerified') or receipt.get('manifestSha256') != EXPECTED_HASH:
        raise RuntimeError('Current approval receipt is missing or differs')
    manifest = json.loads(MANIFEST.read_text('utf-8-sig'))
    targets = [Path(row['targetDirectory']) for row in manifest['targets']]
    if targets != EXPECTED_TARGETS or (len(targets), manifest['fileCount'], manifest['bytes']) != (5, 20, 384174641):
        raise RuntimeError('Target list or counts differ from user approval')
    scope = (ROOT / 'work').resolve(strict=True)
    for target in manifest['targets']:
        path = Path(target['targetDirectory'])
        if path.resolve(strict=True) != path or not path.is_relative_to(scope) or path in (ROOT, scope, Path(path.anchor)):
            raise RuntimeError(f'Target escaped approved scope: {path}')
        files, directories = tree(path)
        expected_files = {item['path'] for item in target['files']}
        expected_dirs = {str(path)}
        for name in expected_files:
            parent = Path(name).parent
            while parent != path:
                if not parent.is_relative_to(path): raise RuntimeError('Invalid file parent')
                expected_dirs.add(str(parent)); parent = parent.parent
        if files != expected_files or directories != expected_dirs:
            raise RuntimeError(f'Target contents changed: {path}')
        for item in target['files']:
            if Path(item['path']).stat().st_size != item['size'] or digest(item['path']) != item['sha256']:
                raise RuntimeError(f'Target file changed: {item["path"]}')
    active = json.loads((ROOT / 'config/anode-local-mcp.json').read_text('utf-8-sig'))
    if active['sha256'] != 'c001edaf1de970e0e1936113ac534268bc3e28d7dfb3876de9fd1a87e4532fc8':
        raise RuntimeError('Screenshot release is not active')
    if json.loads((EVIDENCE / 'live-switch/preview-active/result.json').read_text('utf-8'))['result'] != 'ACTIVITY_PREVIEW_CHECK_OK':
        raise RuntimeError('Active release preview check has not passed')
    if json.loads((EVIDENCE / 'live-switch/global-mcp-discovery.json').read_text('utf-8'))['count'] != 41:
        raise RuntimeError('Active release MCP check has not passed')
    before_processes = protected_processes(targets)
    capacity = recycle_capacity(manifest['bytes'])
    app = win32com.client.Dispatch('Shell.Application')
    recycle = app.NameSpace(shellcon.CSIDL_BITBUCKET)
    if recycle is None: raise RuntimeError('Recycle Bin unavailable')
    before_items = {str(item.Path) for item in recycle.Items()}
    flags = (0x00080000 | shellcon.FOFX_EARLYFAILURE | shellcon.FOF_NOERRORUI
             | shellcon.FOF_SILENT | shellcon.FOF_NOCONFIRMATION
             | shellcon.FOF_WANTNUKEWARNING | shellcon.FOF_NO_CONNECTED_ELEMENTS)
    probe = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
    probe.SetOperationFlags(flags)
    print('ANODE_RECYCLE_PREFLIGHT_OK targets=5 files=20 bytes=384174641', flush=True)
    if not args.execute:
        print('VALIDATION_ONLY; no file operation submitted', flush=True)
        return
    execution = EVIDENCE / 'live-switch/recycle-execution.jsonl'
    if execution.exists(): raise RuntimeError('This batch has already been attempted; refusing replay')
    completed = []
    try:
        with execution.open('x', encoding='utf-8') as log:
            for target in manifest['targets']:
                log.write(json.dumps({'phase': 'intent', 'source': target['targetDirectory']}, ensure_ascii=False) + '\n'); log.flush()
                operation = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
                operation.SetOperationFlags(flags)
                operation.DeleteItem(shell.SHCreateItemFromParsingName(target['targetDirectory'], None, shell.IID_IShellItem))
                operation.PerformOperations()
                if operation.GetAnyOperationsAborted() or Path(target['targetDirectory']).exists():
                    raise RuntimeError(f'Recycle aborted or original remains: {target["targetDirectory"]}')
                deadline, candidates = time.monotonic() + 10, []
                while time.monotonic() < deadline:
                    current = list(recycle.Items())
                    paths = {str(item.Path) for item in current}
                    if not before_items.issubset(paths):
                        raise RuntimeError('A pre-existing Recycle Bin entry disappeared; stopping')
                    candidates = []
                    for item in current:
                        if str(item.Path) in before_items: continue
                        parent, name = item.ExtendedProperty('System.Recycle.DeletedFrom'), item.ExtendedProperty('System.FileName')
                        if parent and name and Path(str(parent)) / str(name) == Path(target['targetDirectory']):
                            candidates.append(str(item.Path))
                    if len(candidates) == 1: break
                    time.sleep(.1)
                if len(candidates) != 1:
                    raise RuntimeError(f'Recoverable entry could not be uniquely verified: {target["targetDirectory"]}')
                count = verify_recycled(target, candidates[0])
                record = {'phase': 'verified', 'source': target['targetDirectory'], 'recyclePath': candidates[0],
                          'files': count, 'originalAbsent': True, 'contentHashesVerified': True}
                completed.append(record); log.write(json.dumps(record, ensure_ascii=False) + '\n'); log.flush()
                print(f'RECYCLED_HASH_VERIFIED files={count} source={target["targetDirectory"]}', flush=True)
        after_processes = protected_processes(targets)
        if any(after_processes.get(pid) != value for pid, value in before_processes.items()):
            raise RuntimeError('A protected process changed during cleanup')
        if len(completed) != 5 or sum(row['files'] for row in completed) != 20 or any(path.exists() for path in targets):
            raise RuntimeError('Final cleanup inventory differs')
        write_new('cleanup-result.json', {'result': 'RECYCLE_VERIFIED', 'targets': 5, 'files': 20,
            'bytes': manifest['bytes'], 'entries': completed, 'capacityBefore': capacity,
            'preExistingRecycleEntriesPreserved': True, 'permanentDeletionUsed': False, 'recycleBinEmptied': False,
            'approvalManifestSha256': EXPECTED_HASH})
        print('RECYCLE_VERIFIED targets=5 files=20 hashes=20 originalsAbsent=true', flush=True)
    except Exception as error:
        write_new('cleanup-failure.json', {'error': str(error), 'completed': completed,
            'originalExists': {str(path): path.exists() for path in targets}, 'permanentDeletionFallbackUsed': False})
        raise


if __name__ == '__main__':
    main()
