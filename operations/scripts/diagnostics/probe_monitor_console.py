"""Observe console windows without focus/input; exercise the known watchdog once."""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import time
import uuid
import psutil

ROOT = Path(__file__).resolve().parents[2]
user32 = ctypes.WinDLL('user32', use_last_error=True)
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

def consoles():
    found = []
    def visit(hwnd, unused):
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, len(name))
        if name.value in ('ConsoleWindowClass', 'CASCADIA_HOSTING_WINDOW_CLASS') and user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append({'window': int(hwnd), 'pid': pid.value, 'class': name.value})
        return True
    callback = callback_type(visit)
    if not user32.EnumWindows(callback, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return found

def lineage(pid):
    rows = []
    try:
        process = psutil.Process(pid)
        for unused in range(7):
            command = ' '.join(process.cmdline())
            owned = str(ROOT).casefold() in command.casefold() and any(x in command for x in ('Control-WeChatResident', 'resident.py', 'WeChatResidentTray'))
            rows.append({'pid': process.pid, 'name': process.name(), 'monitorCommand': command if owned else None})
            process = process.parent()
            if process is None:
                break
    except (psutil.Error, OSError):
        pass
    return rows

def observe(seconds, trigger=True, mode='watchdog'):
    prior = {(row['window'], row['pid']) for row in consoles()}
    seen, rows = set(prior), []
    started, launched = time.monotonic(), False
    child = None
    while time.monotonic() - started < seconds:
        if trigger and not launched and time.monotonic() - started >= 1:
            launched = True
            command = ['powershell.exe', '-NoProfile', '-NonInteractive']
            if mode == 'status':
                command += ['-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'scripts/Control-WeChatResident.ps1'), '-Action', 'Status']
            else:
                command += ['-Command', "Start-ScheduledTask -TaskName 'AgentWindowsOperation-WeChatResident-Watchdog'"]
            child = subprocess.Popen(command,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        for row in consoles():
            key = (row['window'], row['pid'])
            if key not in seen:
                row.update(elapsed=round(time.monotonic() - started, 3), ancestry=lineage(row['pid']))
                rows.append(row)
                seen.add(key)
        time.sleep(0.01)
    if child:
        child.wait(timeout=10)
    return {'seconds': seconds, 'mode': mode, 'baselineWindows': len(prior), 'newVisibleConsoles': rows,
            'monitorConsoleCount': sum(any(p['monitorCommand'] for p in r['ancestry']) for r in rows),
            'samplingMs': 10, 'inputActions': 0}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=12)
    parser.add_argument('--no-trigger', action='store_true')
    parser.add_argument('--mode', choices=['watchdog', 'status'], default='watchdog')
    args = parser.parse_args()
    if not 5 <= args.seconds <= 60:
        raise ValueError('Observation must be 5–60 seconds.')
    result = observe(args.seconds, not args.no_trigger, args.mode)
    directory = ROOT / 'artifacts' / ('wechat-console-probe-' + uuid.uuid4().hex[:10])
    directory.mkdir()
    (directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({'evidence': str(directory / 'result.json'), **result}, ensure_ascii=True))
