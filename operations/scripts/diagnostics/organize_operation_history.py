"""Conservatively archive unreferenced local evidence; never delete files.

Default is plan-only. --apply executes the recorded plan with hash verification.
Live runtimes, task payloads and any currently referenced directory stay in place.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess

OPS = Path(__file__).resolve().parents[2]
REPO = OPS.parent
REPORT = OPS / 'artifacts' / 'directory-layout-20261108'
DEST = OPS / 'artifacts' / 'history' / 'consolidated-20261108'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inventory(root):
    entries = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            item = Path(folder) / name
            if item.lstat().st_file_attributes & 0x400:
                raise RuntimeError(f'REPARSE_POINT: {item}')
        for name in files:
            item = Path(folder) / name
            entries.append({'path': str(item.relative_to(root)),
                            'bytes': item.stat().st_size, 'sha256': sha(item)})
    return sorted(entries, key=lambda entry: entry['path'])


def live_references():
    command = r'''$ErrorActionPreference='Stop'; $s=New-Object -ComObject WScript.Shell; $links=@(Get-ChildItem -LiteralPath $args[0] -Recurse -Filter *.lnk -File | ForEach-Object {$l=$s.CreateShortcut($_.FullName); @{path=$_.FullName;target=$l.TargetPath;arguments=$l.Arguments;cwd=$l.WorkingDirectory}}); @{processes=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -notin @('pwsh.exe','powershell.exe','python.exe')} | Select-Object ProcessId,ExecutablePath,CommandLine);tasks=@(Get-ScheduledTask | Where-Object {$_.TaskName -like 'AgentWindowsOperation*'} | Select-Object TaskName,State,Actions);links=$links} | ConvertTo-Json -Depth 7 -Compress'''
    # Encode the complete command; quote the literal path for PowerShell itself.
    literal = "'" + str(OPS / 'launchers').replace("'", "''") + "'"
    command = "$ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command.replace('$args[0]', literal)
    encoded = base64.b64encode(command.encode('utf-16le')).decode('ascii')
    output = subprocess.check_output(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded],
                                    encoding='utf-8', errors='replace')
    return json.loads(output)


def sources():
    paths = subprocess.check_output(['git', '-C', str(REPO), 'ls-files', '-z',
                                     'operations/scripts', 'operations/config',
                                     'src', 'scripts'], encoding='utf-8').split('\0')
    result = []
    for relative in paths:
        path = REPO / relative
        if path.is_file() and path.suffix.lower() in {'.py', '.ps1', '.psm1', '.json', '.cs', '.cmd'}:
            result.append((relative, path.read_text(encoding='utf-8-sig', errors='replace').lower()))
    for path in (OPS / 'artifacts').glob('*.json'):
        result.append((str(path.relative_to(REPO)), path.read_text(encoding='utf-8-sig').lower()))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    REPORT.mkdir(exist_ok=True)
    plan_path = REPORT / 'plan-v2.json'
    live = live_references()
    refs = sources() + [('live-process-task-shortcut', json.dumps(live).lower())]
    keep_names = {'anode-current', 'anode-screenshot-viewer-20261106-734d2b',
                  'uu-console-task-v1', 'wechat-resident-tray-20261104-224309-1f8eaf46',
                  'history', 'history-20261031', 'recovery', 'restructure-20261106',
                  'migration-backups', REPORT.name, 'project-slim-20261108'}
    # App profiles are recovery material: keep them, but archive inactive ones intact.
    work_candidates = {'alpha-readonly-browser', 'recheck-20261031-0206',
                       'alpha-upstream-docs-20261104-09232cbc', 'grokbot-comparison-20261031'}
    plan = []
    kept = []
    for parent in (OPS / 'work', OPS / 'artifacts'):
        for path in sorted(parent.iterdir()):
            if not path.is_dir():
                continue
            reasons = [name for name, content in refs if path.name.lower() in content]
            if parent.name == 'work' and path.name in work_candidates:
                # Old cleanup/audit scripts are historical readers, not app launchers.
                reasons = [reason for reason in reasons if not reason.startswith('operations/scripts/diagnostics/')]
            if path.name in keep_names or (parent.name == 'work' and path.name not in work_candidates):
                reasons.append('runtime/recovery/application-data retained')
            if reasons:
                kept.append({'path': str(path), 'references': reasons})
                continue
            destination = DEST / ('inactive-application-data' if parent.name == 'work' else 'evidence') / path.name
            if destination.exists():
                raise RuntimeError(f'DESTINATION_EXISTS: {destination}')
            entries = inventory(path)
            plan.append({'source': str(path), 'destination': str(destination),
                         'files': len(entries), 'bytes': sum(e['bytes'] for e in entries),
                         'inventory': entries})
    data = {'action': 'move intact; no deletion; original hashes retained',
            'moves': plan, 'retained': kept, 'live': live}
    if args.apply:
        if not plan_path.exists():
            raise RuntimeError('PLAN_REQUIRED')
        old = json.loads(plan_path.read_text(encoding='utf-8'))
        if old['moves'] != plan:
            raise RuntimeError('PLAN_CHANGED: review a new dry run')
        receipt = REPORT / 'receipt.json'
        if receipt.exists():
            raise RuntimeError('RECEIPT_EXISTS')
        completed = []
        for entry in plan:
            source = Path(entry['source']).resolve()
            destination = Path(entry['destination']).resolve()
            if source.parent not in (OPS / 'work', OPS / 'artifacts') or not destination.is_relative_to(DEST):
                raise RuntimeError('OUTSIDE_SCOPE')
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            if inventory(destination) != entry['inventory'] or source.exists():
                raise RuntimeError(f'MOVE_VERIFY_FAILED: {destination}')
            completed.append(entry)
            # Exclusive intermediate receipts preserve recovery information on interruption.
            with (REPORT / f'moved-{len(completed):03}.json').open('x', encoding='utf-8') as stream:
                json.dump(entry, stream, ensure_ascii=False, indent=2)
        with receipt.open('x', encoding='utf-8') as stream:
            json.dump({'completed': completed, 'verified': True}, stream, ensure_ascii=False, indent=2)
        print('HISTORY_MOVE_HASHES_OK')
    else:
        with plan_path.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'folders': len(plan), 'files': sum(e['files'] for e in plan),
                      'bytes': sum(e['bytes'] for e in plan),
                      'workMoves': [{'source': e['source'], 'destination': e['destination']}
                                    for e in plan if Path(e['source']).parent.name == 'work']},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
