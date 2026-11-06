"""Recycle only the reviewed work build manifest. Default validates without moving files."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time
import winreg

import pythoncom
import win32com.client
from win32com.shell import shell, shellcon

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work'
AUDIT = ROOT / 'artifacts/work-cleanup-audit-20261106-6f7bb3e2'
MANIFEST = AUDIT / 'cleanup-manifest.json'
PATTERNS = AUDIT / 'candidate-reference-patterns.txt'
RECEIPT = AUDIT / 'current-batch-approval.json'
MANIFEST_HASH = 'ceb1da1edacfe9f45804cf2c9e4d9bf93ad5cdbb937ec26f3f566f0adefda193'
PATTERN_HASH = '0c477f9971edeb004a5bc007e73f6dfbbc975b15f88bfcba397936696be0a85e'


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def tree(path):
    files, directories, todo = set(), set(), [path]
    while todo:
        item = todo.pop()
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError(f'Reparse point refused: {item}')
        if stat.S_ISDIR(info.st_mode):
            directories.add(str(item))
            with os.scandir(item) as entries:
                todo.extend(Path(entry.path) for entry in entries)
        elif stat.S_ISREG(info.st_mode):
            if info.st_nlink > 1: raise RuntimeError(f'Hard link refused: {item}')
            files.add(str(item))
        else:
            raise RuntimeError(f'Unsupported file type: {item}')
    return files, directories


def exclusive_read_probe(path):
    # OPEN_EXISTING + read access, no sharing and no delete flag: closes immediately.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), f'Exclusive read unavailable: {path}')
    kernel.CloseHandle(handle)


def normalized(value):
    return str(value or '').replace('\\', '/').casefold()


def direct_references(targets):
    def check(value, source):
        text = normalized(value)
        if any(normalized(target) in text for target in targets):
            raise RuntimeError(f'A current process, task, shortcut, or environment refers to a target: {source}')

    # No command lines are written into the report or printed.
    wmi = win32com.client.GetObject('winmgmts:')
    checked, unavailable = 0, 0
    protected = {}
    for process in wmi.ExecQuery('SELECT Name,ProcessId,SessionId,ExecutablePath,CommandLine,CreationDate FROM Win32_Process'):
        checked += 1
        executable, command = process.ExecutablePath, process.CommandLine
        if executable is None or command is None: unavailable += 1
        check(executable, f'process PID {process.ProcessId}')
        check(command, f'process PID {process.ProcessId}')
        if str(process.Name).casefold() in {'anode.exe', 'alpha.exe', 'chatgpt.exe', 'wechatresidenttray.exe'}:
            protected[str(process.ProcessId)] = {'name': str(process.Name), 'session': int(process.SessionId),
                'executable': str(executable or ''), 'created': str(process.CreationDate or '')}

    service = win32com.client.Dispatch('Schedule.Service'); service.Connect()
    folders, task_count = [service.GetFolder('\\')], 0
    while folders:
        folder = folders.pop()
        folders.extend(folder.GetFolders(0))
        for task in folder.GetTasks(1):  # includes hidden/disabled tasks
            task_count += 1
            for action in task.Definition.Actions:
                if action.Type == 0:
                    check(' '.join(str(value or '') for value in (action.Path, action.Arguments, action.WorkingDirectory)),
                          f'scheduled task {task.Path}')

    shortcut_shell = win32com.client.Dispatch('WScript.Shell')
    roots = [shortcut_shell.SpecialFolders(name) for name in ('Desktop', 'Programs', 'AllUsersPrograms', 'Startup')]
    roots += [str(Path(os.environ['APPDATA']) / r'Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar'), str(ROOT / 'launchers')]
    link_count = 0
    for root in map(Path, roots):
        if not root.is_dir(): continue
        for path in root.rglob('*.lnk'):
            link = shortcut_shell.CreateShortcut(str(path)); link_count += 1
            check(' '.join(str(value or '') for value in (link.TargetPath, link.Arguments, link.WorkingDirectory, link.IconLocation)),
                  f'shortcut {path}')
    for name, value in os.environ.items():
        check(value, f'process environment {name}')
    for hive, subkey in [(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run'),
                         (winreg.HKEY_LOCAL_MACHINE, r'Software\Microsoft\Windows\CurrentVersion\Run'),
                         (winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\RunOnce'),
                         (winreg.HKEY_LOCAL_MACHINE, r'Software\Microsoft\Windows\CurrentVersion\RunOnce'),
                         (winreg.HKEY_CURRENT_USER, 'Environment'),
                         (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment')]:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                for i in range(winreg.QueryInfoKey(key)[1]):
                    name, value, _ = winreg.EnumValue(key, i)
                    check(value, f'registry {subkey} value {name}')
        except FileNotFoundError:
            pass
    return {'processesChecked': checked, 'processFieldsUnavailable': unavailable, 'scheduledTasksChecked': task_count,
            'shortcutsChecked': link_count, 'protectedProcesses': protected}


def search_current_and_historic_references():
    if digest(PATTERNS) != PATTERN_HASH:
        raise RuntimeError('Candidate reference patterns changed')
    base = ['rg', '-l', '-i', '--hidden', '-F', '-f', str(PATTERNS)]
    locations = ['config', 'scripts', 'launchers', 'work/anode-fork/scripts', 'docs',
                 str(Path(os.environ['USERPROFILE']) / '.codex/config.toml')]
    for location in locations:
        if not (ROOT / location).exists(): raise RuntimeError(f'Reference source is missing: {location}')
    result = subprocess.run(base + locations + ['-g', '!*.lnk'], cwd=ROOT, capture_output=True,
                            text=True, encoding='utf-8', timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 1:
        raise RuntimeError(f'Current reference search did not prove absence (exit {result.returncode}; files: {result.stdout[:500]})')
    globs = ['*.json', '*.jsonl', '*.md', '*.txt', '*.log', '*.csv']
    args = base + ['artifacts']
    for pattern in globs: args += ['-g', pattern]
    args += ['-g', '!**/work-cleanup-audit-20261106-6f7bb3e2/**']
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=60,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 1:
        raise RuntimeError(f'Historical evidence reference search did not prove absence (exit {result.returncode}; files: {result.stdout[:500]})')


def recycle_capacity(total_bytes):
    class Info(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('bytes', ctypes.c_int64), ('items', ctypes.c_int64)]
    info = Info(ctypes.sizeof(Info), 0, 0)
    if ctypes.windll.shell32.SHQueryRecycleBinW('F:\\', ctypes.byref(info)) != 0:
        raise RuntimeError('Could not query F: Recycle Bin')
    buffer = ctypes.create_unicode_buffer(256)
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW('F:\\', buffer, len(buffer)):
        raise ctypes.WinError()
    volume = buffer.value.split('Volume', 1)[1].strip('\\')
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume' + chr(92) + volume) as reg:
        nuke = winreg.QueryValueEx(reg, 'NukeOnDelete')[0]
        maximum_mib = winreg.QueryValueEx(reg, 'MaxCapacity')[0]
    if nuke != 0 or info.bytes + total_bytes + 512 * 1048576 >= maximum_mib * 1048576:
        raise RuntimeError('Recycle Bin disabled or insufficient capacity for safe recovery')
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\Policies\Explorer') as reg:
                try: disabled = winreg.QueryValueEx(reg, 'NoRecycleFiles')[0]
                except FileNotFoundError: disabled = 0
                if disabled: raise RuntimeError('Windows policy disables recycling')
        except FileNotFoundError:
            pass
    return {'occupiedBytes': info.bytes, 'existingItems': info.items, 'capacityMiB': maximum_mib}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if digest(MANIFEST) != MANIFEST_HASH: raise RuntimeError('Reviewed manifest hash differs')
    manifest = json.loads(MANIFEST.read_text('utf-8'))
    if (manifest['targetCount'], manifest['fileCount'], manifest['totalBytes']) != (26, 13078, 4952471602):
        raise RuntimeError('Reviewed target counts differ')
    targets = [Path(row['path']) for row in manifest['targets']]
    if len(set(targets)) != 26: raise RuntimeError('Duplicate target directory')
    work = WORK.resolve(strict=True)
    if work != WORK or str(work) != manifest['scope']: raise RuntimeError('Unexpected work scope')
    if args.execute:
        receipt = json.loads(RECEIPT.read_text('utf-8-sig'))
        if not receipt.get('activeUserReplyVerified') or receipt.get('manifestSha256') != MANIFEST_HASH:
            raise RuntimeError('Current-batch approval is missing or differs')
        if (AUDIT / 'recycle-execution.jsonl').exists():
            raise RuntimeError('This cleanup was already attempted; refusing replay')
    active = json.loads((ROOT / 'config/anode-local-mcp.json').read_text('utf-8-sig'))
    active_exe = (ROOT / active['executable']).resolve(strict=True)
    if digest(active_exe) != active['sha256'] or any(active_exe == p or p in active_exe.parents for p in targets):
        raise RuntimeError('Active Anode binary changed or overlaps cleanup')
    protected_before = direct_references(targets)
    search_current_and_historic_references()
    capacity = recycle_capacity(manifest['totalBytes'])
    for target in manifest['targets']:
        path = Path(target['path'])
        if path.resolve(strict=True) != path or not path.is_relative_to(work) or path in (ROOT, work, Path(path.anchor)):
            raise RuntimeError(f'Target escaped approved scope: {path}')
        ancestor = path.parent
        while ancestor != work:
            if getattr(ancestor.lstat(), 'st_file_attributes', 0) & 0x400:
                raise RuntimeError(f'Reparse ancestor refused: {ancestor}')
            ancestor = ancestor.parent
        files, directories = tree(path)
        if files != {item['path'] for item in target['files']} or directories != set(target['directories']):
            raise RuntimeError(f'Target membership changed: {path}')
        for item in target['files']:
            file = Path(item['path']); current = file.stat()
            if current.st_size != item['size'] or current.st_mtime_ns != item['mtimeNs']:
                raise RuntimeError(f'Target changed: {file}')
            exclusive_read_probe(file)
            if digest(file) != item['sha256']:
                raise RuntimeError(f'Target hash changed: {file}')
        print(f'VALIDATED {path} files={target["fileCount"]} bytes={target["bytes"]}', flush=True)
    app = win32com.client.Dispatch('Shell.Application')
    recycle = app.NameSpace(shellcon.CSIDL_BITBUCKET)
    if recycle is None: raise RuntimeError('Recycle Bin unavailable')
    before_items = {str(item.Path) for item in recycle.Items()}
    # Do not set FOF_NOCONFIRMATION: it can answer destructive warnings "Yes".
    flags = (0x00080000 | shellcon.FOFX_EARLYFAILURE | shellcon.FOF_NOERRORUI
             | shellcon.FOF_SILENT
             | shellcon.FOF_WANTNUKEWARNING | shellcon.FOF_NO_CONNECTED_ELEMENTS)
    probe = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
    probe.SetOperationFlags(flags)
    print(f'WORK_RECYCLE_PREFLIGHT_OK targets=26 files=13078 bytes=4952471602 exclusiveRead=13078 processFieldsUnavailable={protected_before["processFieldsUnavailable"]}', flush=True)
    if not args.execute:
        print('VALIDATION_ONLY; no file operation submitted', flush=True)
        return
    completed = []
    try:
        with (AUDIT / 'recycle-execution.jsonl').open('x', encoding='utf-8') as log:
            for target in manifest['targets']:
                path = Path(target['path'])
                direct_references([path])
                files, directories = tree(path)
                if files != {item['path'] for item in target['files']} or directories != set(target['directories']):
                    raise RuntimeError(f'Target membership changed immediately before recycling: {path}')
                for item in target['files']:
                    file = Path(item['path'])
                    current = file.stat()
                    if current.st_size != item['size'] or current.st_mtime_ns != item['mtimeNs']:
                        raise RuntimeError(f'Target changed immediately before recycling: {file}')
                    exclusive_read_probe(file)
                log.write(json.dumps({'phase': 'intent', 'source': target['path']}, ensure_ascii=False) + '\n'); log.flush()
                op = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
                op.SetOperationFlags(flags)
                op.DeleteItem(shell.SHCreateItemFromParsingName(target['path'], None, shell.IID_IShellItem))
                op.PerformOperations()
                if op.GetAnyOperationsAborted() or Path(target['path']).exists():
                    raise RuntimeError(f'Recycle aborted or original remains: {target["path"]}')
                deadline, candidates = time.monotonic() + 10, []
                while time.monotonic() < deadline:
                    current = list(recycle.Items()); paths = {str(item.Path) for item in current}
                    if not before_items.issubset(paths):
                        raise RuntimeError('Pre-existing Recycle Bin entry disappeared; stopping')
                    candidates = []
                    for item in current:
                        if str(item.Path) in before_items: continue
                        parent, name = item.ExtendedProperty('System.Recycle.DeletedFrom'), item.ExtendedProperty('System.FileName')
                        if parent and name and Path(str(parent)) / str(name) == Path(target['path']): candidates.append(str(item.Path))
                    if len(candidates) == 1: break
                    time.sleep(.1)
                if len(candidates) != 1: raise RuntimeError(f'Recoverable entry is not unique: {target["path"]}')
                destination = Path(candidates[0]); files, _ = tree(destination)
                expected = {str(destination / Path(item['path']).relative_to(Path(target['path']))): item for item in target['files']}
                if files != set(expected): raise RuntimeError(f'Recycled contents differ: {target["path"]}')
                for name, item in expected.items():
                    if Path(name).stat().st_size != item['size'] or digest(name) != item['sha256']:
                        raise RuntimeError(f'Recycled file hash differs: {item["path"]}')
                record = {'phase': 'verified', 'source': target['path'], 'recyclePath': str(destination),
                          'fileCount': len(expected), 'bytes': target['bytes'], 'originalAbsent': True, 'hashesVerified': True}
                before_items = paths
                completed.append(record); log.write(json.dumps(record, ensure_ascii=False) + '\n'); log.flush()
                print(f'RECYCLED_VERIFIED {target["path"]} files={len(expected)}', flush=True)
        if len(completed) != 26 or sum(row['fileCount'] for row in completed) != 13078 or any(path.exists() for path in targets):
            raise RuntimeError('Final inventory differs')
        protected_after = direct_references(targets)
        result = {'result': 'RECYCLE_VERIFIED', 'targets': 26, 'files': 13078, 'bytes': manifest['totalBytes'],
                  'entries': completed, 'manifestSha256': MANIFEST_HASH, 'capacityBefore': capacity,
                  'referencesBefore': protected_before, 'referencesAfter': protected_after,
                  'preExistingRecycleEntriesPreserved': True, 'permanentDeletionUsed': False, 'recycleBinEmptied': False}
        with (AUDIT / 'cleanup-result.json').open('x', encoding='utf-8') as f: json.dump(result, f, ensure_ascii=False, indent=2)
        print('RECYCLE_VERIFIED targets=26 files=13078 originalsAbsent=true hashes=13078', flush=True)
    except Exception as error:
        failure = {'error': str(error), 'completed': completed, 'originalExists': {str(p): p.exists() for p in targets},
                   'permanentDeletionFallbackUsed': False}
        with (AUDIT / 'cleanup-failure.json').open('x', encoding='utf-8') as f: json.dump(failure, f, ensure_ascii=False, indent=2)
        raise


if __name__ == '__main__':
    main()
