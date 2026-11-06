"""Live reversible tray acceptance: pause/resume/singleton/exit/notification/console."""
import ctypes
import hashlib
import json
from pathlib import Path
import statistics
import struct
import subprocess
import sys
import threading
import time
import uuid
import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/wechat_monitor'))
from core import configuration
from resident import status
from probe_monitor_console import observe

config = configuration()
manifest = json.loads((ROOT / 'config/wechat-resident-tray.json').read_text('utf-8'))
exe = ROOT / manifest['executable']
assert hashlib.sha256(exe.read_bytes()).hexdigest() == manifest['sha256']
binary = exe.read_bytes()
assert struct.unpack_from('<H', binary, struct.unpack_from('<I', binary, 60)[0] + 24 + 68)[0] == 2
directory = ROOT / 'artifacts' / ('wechat-tray-live-' + uuid.uuid4().hex[:10])
directory.mkdir()
before = status(config)
assert before['enabled'] and before['running'], 'Expected the authorized monitor to be running.'
result = {'guiSubsystem': 2, 'checks': [], 'evidence': str(directory), 'wechatSends': 0, 'keyboardActions': 0}

def invoke(mode, result_file=None):
    command = [str(exe), '--root', str(ROOT), mode]
    if result_file:
        command += ['--result', str(result_file)]
    completed = subprocess.run(command, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=50)
    assert completed.returncode == 0, (mode, completed.returncode)
    return json.loads(result_file.read_text('utf-8')) if result_file else None

def wait_state(running, healthy=False):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        value = status(config)
        if value['running'] == running and (not healthy or value['healthy']):
            return value
        time.sleep(.4)
    raise AssertionError('Worker did not reach the requested state')

def tray_processes():
    rows = []
    for process in psutil.process_iter(['pid', 'name', 'cmdline']):
        if (process.info['name'] or '').lower() == 'wechatresidenttray.exe' and '--tray' in (process.info['cmdline'] or []):
            rows.append(process.info['pid'])
    return rows

def inspect(name):
    return invoke('--inspect', directory / (name + '.json'))

console = {}
observer = threading.Thread(target=lambda: console.update(observe(60, False)))
observer.start()
try:
    initial = inspect('initial')
    assert initial['trayRunning'] and initial['workerRunning'] and initial['tray']['iconVisible']
    assert initial['tray']['session'] == 1
    invoke('--tray')
    assert len(tray_processes()) == 1
    result['checks'].append('one GUI tray and one locked worker')
    invoke('--pause')
    paused = wait_state(False)
    assert not paused['enabled']
    invoke('--ensure')
    time.sleep(2)
    assert not status(config)['running'] and len(tray_processes()) == 1
    result['checks'].append('pause persists and watchdog respects it')
    invoke('--resume')
    resumed = wait_state(True, True)
    assert resumed['activation_ms'] == before['activation_ms']
    assert all(resumed['checkpoints'][key] >= value for key, value in before['checkpoints'].items())
    result['checks'].append('resume preserves activation and checkpoints')
    invoke('--exit')
    wait_state(False)
    deadline = time.monotonic() + 8
    while tray_processes() and time.monotonic() < deadline:
        time.sleep(.2)
    assert not tray_processes()
    invoke('--ensure')
    time.sleep(1)
    assert not tray_processes()
    result['checks'].append('exit stops worker and does not relaunch when disabled')
    invoke('--resume')
    wait_state(True, True)
    assert len(tray_processes()) == 1
    result['checks'].append('native scheduled task recreates tray after exit')
    notice = invoke('--test-notification', directory / 'notification.json')
    assert notice['state'] == 'shown'
    result['checks'].append('isolated local test notification shown')
    time.sleep(6)
    final = inspect('final')
    assert final['trayRunning'] and final['workerRunning']
    health = [json.loads(line) for line in (ROOT / config['resident']['stateDirectory'] / 'health.jsonl').read_text('utf-8').splitlines()]
    current = [row['at_ms'] for row in health if row.get('pid') == final['health']['pid'] and row.get('state') == 'healthy'][-8:]
    gaps = [(b - a) / 1000 for a, b in zip(current, current[1:])]
    assert len(gaps) >= 2 and 1.5 <= statistics.median(gaps) <= 3.2, gaps
    result.update(pollMedianSeconds=statistics.median(gaps), pollSamples=len(gaps), notification=notice)
    result['checks'].append('live poll interval approximately 2 seconds')
    observer.join()
    result['consoleObservation'] = console
    assert not console['newVisibleConsoles'], 'A visible console was observed; inspect ownership before claiming fixed.'
    result['checks'].append('no new visible console during 60 second observation')
    result['result'] = 'TRAY_LIVE_ACCEPTANCE_OK'
except Exception as error:
    result.update(result='TRAY_LIVE_ACCEPTANCE_FAILED', error=repr(error))
    raise
finally:
    if not status(config)['enabled']:
        invoke('--resume')
    observer.join()
    result['consoleObservation'] = console
    (directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(result, ensure_ascii=True), flush=True)
